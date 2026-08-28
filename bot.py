import json
import logging
import os
import re
import socket
import difflib
from datetime import datetime, timezone
from pathlib import Path

from unidecode import unidecode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from dotenv import load_dotenv
load_dotenv()

# Ép chỉ phân giải IPv4 — tránh treo khi hệ thống ưu tiên route IPv6 không ổn định
_original_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
RECIPES_PATH = BASE_DIR / "data" / "recipes.json"
STAFF_PATH = BASE_DIR / "data" / "staff.json"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID_RAW = os.environ.get("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.strip().isdigit() else None

# States cho luồng thêm món (ConversationHandler)
ADD_NAME, ADD_CATEGORY, ADD_INGREDIENTS, ADD_METHOD, ADD_TOOLS, ADD_CONFIRM = range(6)
# States cho luồng sửa món
EDIT_PICK_FIELD, EDIT_NEW_VALUE = range(6, 8)


def normalize(text: str) -> str:
    text = unidecode(text.lower())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def escape_md(text: str) -> str:
    if not text:
        return text
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


class RecipeStore:
    def __init__(self, path: Path):
        self.path = path
        self.recipes = json.loads(path.read_text(encoding="utf-8"))
        self._migrate_ids()
        self._reindex()

    def _migrate_ids(self):
        next_id = 1
        changed = False
        used = {r["id"] for r in self.recipes if "id" in r}
        for r in self.recipes:
            if "id" not in r:
                while next_id in used:
                    next_id += 1
                r["id"] = next_id
                used.add(next_id)
                changed = True
        if changed:
            self.save()

    def _reindex(self):
        for r in self.recipes:
            r["_norm"] = normalize(r["name"])

    def save(self):
        clean = [{k: v for k, v in r.items() if k != "_norm"} for r in self.recipes]
        self.path.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def search(self, query: str, limit: int = 8):
        q = normalize(query)
        if not q:
            return []
        exact = [r for r in self.recipes if r["_norm"] == q]
        if exact:
            return exact
        contains = [r for r in self.recipes if q in r["_norm"] or r["_norm"] in q]
        if contains:
            contains.sort(key=lambda r: abs(len(r["_norm"]) - len(q)))
            return contains[:limit]
        q_words = q.split()
        word_hits = []
        for r in self.recipes:
            name_tokens = r["_norm"].split()
            ok = True
            for w in q_words:
                if len(w) <= 2:
                    matched = any(t == w for t in name_tokens)
                else:
                    matched = any(t.startswith(w) or w in t for t in name_tokens)
                if not matched:
                    ok = False
                    break
            if ok:
                word_hits.append(r)
        if word_hits:
            return word_hits[:limit]
        names = {r["_norm"]: r for r in self.recipes}
        close = difflib.get_close_matches(q, names.keys(), n=limit, cutoff=0.55)
        return [names[c] for c in close]

    def by_id(self, rid: int):
        for r in self.recipes:
            if r["id"] == rid:
                return r
        return None

    def add(self, name, category, ingredients, method, tools):
        next_id = max((r["id"] for r in self.recipes), default=0) + 1
        recipe = {
            "id": next_id,
            "name": name,
            "category": category or None,
            "ingredients": ingredients,
            "method": method,
            "tools": tools or "",
            "_norm": normalize(name),
        }
        self.recipes.append(recipe)
        self.save()
        return recipe

    def delete(self, rid: int) -> bool:
        r = self.by_id(rid)
        if not r:
            return False
        self.recipes.remove(r)
        self.save()
        return True

    def update_field(self, rid: int, field: str, value: str):
        r = self.by_id(rid)
        if not r:
            return None
        r[field] = value
        if field == "name":
            r["_norm"] = normalize(value)
        self.save()
        return r


class StaffStore:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self.staff = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.staff = []
            self.save()

    def save(self):
        self.path.write_text(
            json.dumps(self.staff, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def is_staff(self, user_id: int) -> bool:
        return any(s["id"] == user_id for s in self.staff)

    def add(self, user_id: int, name: str):
        if self.is_staff(user_id):
            for s in self.staff:
                if s["id"] == user_id:
                    s["name"] = name
            self.save()
            return
        self.staff.append(
            {
                "id": user_id,
                "name": name,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save()

    def remove(self, user_id: int) -> bool:
        before = len(self.staff)
        self.staff = [s for s in self.staff if s["id"] != user_id]
        if len(self.staff) != before:
            self.save()
            return True
        return False


store: RecipeStore | None = None
staff: StaffStore | None = None


def is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


def is_authorized(user_id: int) -> bool:
    return is_admin(user_id) or (staff and staff.is_staff(user_id))


def format_recipe(r: dict) -> str:
    lines = [f"🧋 *{escape_md(r['name'])}*"]
    if r.get("category"):
        lines.append(f"_{escape_md(r['category'])}_")
    lines.append("")
    lines.append("*📋 Nguyên liệu / Định lượng:*")
    lines.append(escape_md(r["ingredients"]) or "_(chưa có dữ liệu)_")
    lines.append("")
    lines.append("*🥄 Phương pháp pha chế:*")
    lines.append(escape_md(r["method"]) or "_(chưa có dữ liệu)_")
    if r.get("tools"):
        lines.append("")
        lines.append("*🧰 Dụng cụ / Trang trí:*")
        lines.append(escape_md(r["tools"]))
    return "\n".join(lines)


# ---------- Lệnh chung ----------

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"ID Telegram của bạn: `{user.id}`\n"
        f"Gửi ID này cho admin để được cấp quyền dùng bot.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(
            "Bạn chưa được cấp quyền dùng bot này.\n"
            f"ID Telegram của bạn: `{user.id}`\n"
            f"Gửi ID này cho admin để được thêm vào danh sách nhân viên.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    text = (
        "Xin chào! Gõ tên món (có dấu hoặc không dấu đều được), "
        "mình sẽ gửi lại công thức, định lượng, dụng cụ và cách pha.\n\n"
        "Ví dụ: \"cà phê sữa\" hoặc \"ca phe sua\"\n\n"
        "Gõ /list để xem toàn bộ menu."
    )
    if is_admin(user.id):
        text += (
            "\n\n*Lệnh quản trị:*\n"
            "/themnv \\<id\\> \\<tên\\> — thêm nhân viên\n"
            "/xoanv \\<id\\> — xoá nhân viên\n"
            "/dsnv — danh sách nhân viên\n"
            "/themmon — thêm món mới\n"
            "/suamon \\<tên món\\> — sửa món\n"
            "/xoamon \\<tên món\\> — xoá món"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text)


async def list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Bạn chưa được cấp quyền dùng bot này. Gõ /myid để lấy ID gửi admin.")
        return
    grouped: dict[str, list[str]] = {}
    for r in store.recipes:
        grouped.setdefault(r.get("category") or "Khác", []).append(r["name"])

    chunks = []
    current = ""
    for cat, names in grouped.items():
        block = f"\n【 {cat} 】\n" + "\n".join(f"• {n}" for n in names) + "\n"
        if len(current) + len(block) > 3500:
            chunks.append(current)
            current = block
        else:
            current += block
    if current:
        chunks.append(current)
    for chunk in chunks:
        await update.message.reply_text(chunk)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(
            "Bạn chưa được cấp quyền dùng bot này.\n"
            f"ID Telegram của bạn: `{user.id}`\n"
            f"Gửi ID này cho admin để được thêm vào danh sách nhân viên.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    query = update.message.text
    results = store.search(query)

    if not results:
        await update.message.reply_text(
            f"Không tìm thấy món nào khớp với \"{query}\". "
            f"Thử gõ /list để xem danh sách, hoặc kiểm tra lại chính tả."
        )
        return

    if len(results) == 1:
        await update.message.reply_text(
            format_recipe(results[0]), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    buttons = [
        [InlineKeyboardButton(r["name"], callback_data=f"r:{r['id']}")]
        for r in results
    ]
    await update.message.reply_text(
        f"Tìm thấy {len(results)} món khớp với \"{query}\", chọn món bạn cần:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_authorized(query.from_user.id):
        await query.answer("Bạn chưa được cấp quyền.", show_alert=True)
        return
    await query.answer()
    rid = int(query.data.split(":", 1)[1])
    recipe = store.by_id(rid)
    if not recipe:
        await query.edit_message_text("Món này không còn tồn tại trong dữ liệu.")
        return
    await query.edit_message_text(format_recipe(recipe), parse_mode=ParseMode.MARKDOWN_V2)


# ---------- Quản trị nhân viên ----------

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Lệnh này chỉ admin mới dùng được.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def add_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1 or not args[0].isdigit():
        await update.message.reply_text(
            "Cú pháp: /themnv <id_telegram> <tên nhân viên>\n"
            "Nhân viên gõ /myid trong chat với bot để lấy ID gửi cho bạn."
        )
        return
    user_id = int(args[0])
    name = " ".join(args[1:]).strip() or f"NV #{user_id}"
    staff.add(user_id, name)
    await update.message.reply_text(f"Đã thêm nhân viên: {name} (ID {user_id})")


@admin_only
async def remove_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Cú pháp: /xoanv <id_telegram>")
        return
    user_id = int(args[0])
    if staff.remove(user_id):
        await update.message.reply_text(f"Đã xoá nhân viên ID {user_id}")
    else:
        await update.message.reply_text("Không tìm thấy nhân viên với ID này.")


@admin_only
async def list_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not staff.staff:
        await update.message.reply_text("Chưa có nhân viên nào trong danh sách.")
        return
    lines = ["*Danh sách nhân viên:*"]
    for s in staff.staff:
        lines.append(f"• {escape_md(s['name'])} — ID `{s['id']}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------- Thêm món mới (ConversationHandler) ----------

@admin_only
async def add_recipe_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_recipe"] = {}
    await update.message.reply_text(
        "Thêm món mới. Gõ /huy để huỷ bất cứ lúc nào.\n\nTên món là gì?"
    )
    return ADD_NAME


async def add_recipe_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_recipe"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "Nhóm món là gì? (vd: Cà phê, Trà sữa... gõ /bo_qua nếu không có)"
    )
    return ADD_CATEGORY


async def add_recipe_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new_recipe"]["category"] = None if text == "/bo_qua" else text
    await update.message.reply_text("Nguyên liệu / định lượng?")
    return ADD_INGREDIENTS


async def add_recipe_ingredients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_recipe"]["ingredients"] = update.message.text.strip()
    await update.message.reply_text("Phương pháp pha chế?")
    return ADD_METHOD


async def add_recipe_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_recipe"]["method"] = update.message.text.strip()
    await update.message.reply_text(
        "Dụng cụ / trang trí? (gõ /bo_qua nếu không có)"
    )
    return ADD_TOOLS


async def add_recipe_tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new_recipe"]["tools"] = "" if text == "/bo_qua" else text

    r = context.user_data["new_recipe"]
    preview = format_recipe(r)
    await update.message.reply_text("Xem lại trước khi lưu:")
    await update.message.reply_text(preview, parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text("Lưu món này? Gõ /luu để lưu, /huy để huỷ.")
    return ADD_CONFIRM


async def add_recipe_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = context.user_data.get("new_recipe")
    if not r:
        await update.message.reply_text("Không có dữ liệu món để lưu.")
        return ConversationHandler.END
    saved = store.add(r["name"], r["category"], r["ingredients"], r["method"], r["tools"])
    context.user_data.pop("new_recipe", None)
    await update.message.reply_text(f"Đã lưu món \"{saved['name']}\" vào menu.")
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_recipe", None)
    context.user_data.pop("edit_target", None)
    context.user_data.pop("edit_field", None)
    await update.message.reply_text("Đã huỷ.")
    return ConversationHandler.END


# ---------- Sửa món ----------

FIELD_LABELS = {
    "name": "Tên món",
    "category": "Nhóm món",
    "ingredients": "Nguyên liệu",
    "method": "Phương pháp",
    "tools": "Dụng cụ",
}


@admin_only
async def edit_recipe_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /suamon <tên món>")
        return ConversationHandler.END
    query = " ".join(context.args)
    results = store.search(query)
    if not results:
        await update.message.reply_text(f"Không tìm thấy món nào khớp với \"{query}\".")
        return ConversationHandler.END
    if len(results) > 1:
        buttons = [
            [InlineKeyboardButton(r["name"], callback_data=f"editpick:{r['id']}")]
            for r in results
        ]
        await update.message.reply_text(
            "Có nhiều món khớp, chọn món cần sửa:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return EDIT_PICK_FIELD
    return await _show_edit_fields(update.message.reply_text, context, results[0]["id"])


async def _show_edit_fields(send, context, rid):
    context.user_data["edit_target"] = rid
    r = store.by_id(rid)
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"editfield:{field}")]
        for field, label in FIELD_LABELS.items()
    ]
    buttons.append([InlineKeyboardButton("🗑 Xoá món này", callback_data="editfield:__delete__")])
    await send(
        f"Đang sửa: {r['name']}\nChọn trường cần sửa:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return EDIT_PICK_FIELD


async def edit_pick_recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Chỉ admin mới dùng được.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    rid = int(query.data.split(":", 1)[1])
    return await _show_edit_fields(query.message.reply_text, context, rid)


async def edit_pick_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Chỉ admin mới dùng được.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    field = query.data.split(":", 1)[1]
    rid = context.user_data.get("edit_target")
    r = store.by_id(rid) if rid else None
    if not r:
        await query.edit_message_text("Món này không còn tồn tại.")
        return ConversationHandler.END

    if field == "__delete__":
        buttons = [
            [
                InlineKeyboardButton("✅ Xác nhận xoá", callback_data=f"confirmdel:{rid}"),
                InlineKeyboardButton("❌ Huỷ", callback_data="canceldel"),
            ]
        ]
        await query.edit_message_text(
            f"Xoá món \"{r['name']}\"? Không thể hoàn tác.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return EDIT_PICK_FIELD

    context.user_data["edit_field"] = field
    current_value = r.get(field) or "(trống)"
    await query.edit_message_text(
        f"{FIELD_LABELS[field]} hiện tại:\n{current_value}\n\nGõ giá trị mới:"
    )
    return EDIT_NEW_VALUE


async def edit_confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Chỉ admin mới dùng được.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    rid = int(query.data.split(":", 1)[1])
    r = store.by_id(rid)
    name = r["name"] if r else "?"
    store.delete(rid)
    context.user_data.pop("edit_target", None)
    await query.edit_message_text(f"Đã xoá món \"{name}\".")
    return ConversationHandler.END


async def edit_cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Đã huỷ xoá.")
    return ConversationHandler.END


async def edit_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rid = context.user_data.get("edit_target")
    field = context.user_data.get("edit_field")
    if not rid or not field:
        await update.message.reply_text("Không có thao tác sửa nào đang chờ.")
        return ConversationHandler.END
    new_value = update.message.text.strip()
    r = store.update_field(rid, field, new_value)
    context.user_data.pop("edit_target", None)
    context.user_data.pop("edit_field", None)
    if not r:
        await update.message.reply_text("Món này không còn tồn tại.")
        return ConversationHandler.END
    await update.message.reply_text(f"Đã cập nhật {FIELD_LABELS[field]} cho \"{r['name']}\".")
    return ConversationHandler.END


@admin_only
async def delete_recipe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Cú pháp: /xoamon <tên món>")
        return
    query = " ".join(context.args)
    results = store.search(query)
    if not results:
        await update.message.reply_text(f"Không tìm thấy món nào khớp với \"{query}\".")
        return
    if len(results) > 1:
        buttons = [
            [InlineKeyboardButton(r["name"], callback_data=f"confirmdel:{r['id']}")]
            for r in results
        ]
        await update.message.reply_text(
            "Có nhiều món khớp, chọn món cần xoá:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return
    r = results[0]
    buttons = [
        [
            InlineKeyboardButton("✅ Xác nhận xoá", callback_data=f"confirmdel:{r['id']}"),
            InlineKeyboardButton("❌ Huỷ", callback_data="canceldel"),
        ]
    ]
    await update.message.reply_text(
        f"Xoá món \"{r['name']}\"? Không thể hoàn tác.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def main():
    global store, staff
    if not BOT_TOKEN:
        raise RuntimeError(
            "Thiếu BOT_TOKEN. Đặt biến môi trường BOT_TOKEN = token bot lấy từ @BotFather."
        )
    if ADMIN_ID is None:
        raise RuntimeError(
            "Thiếu ADMIN_ID. Đặt biến môi trường ADMIN_ID = ID Telegram của bạn "
            "(gõ /myid trong chat với bot khác, hoặc dùng @userinfobot để lấy ID)."
        )

    store = RecipeStore(RECIPES_PATH)
    staff = StaffStore(STAFF_PATH)

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
        proxy=None,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("list", list_menu))
    app.add_handler(CommandHandler("themnv", add_staff))
    app.add_handler(CommandHandler("xoanv", remove_staff))
    app.add_handler(CommandHandler("dsnv", list_staff))

    add_recipe_conv = ConversationHandler(
        entry_points=[CommandHandler("themmon", add_recipe_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recipe_name)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recipe_category)],
            ADD_INGREDIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recipe_ingredients)],
            ADD_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recipe_method)],
            ADD_TOOLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recipe_tools)],
            ADD_CONFIRM: [CommandHandler("luu", add_recipe_confirm)],
        },
        fallbacks=[CommandHandler("huy", cancel_conversation)],
    )
    app.add_handler(add_recipe_conv)

    edit_recipe_conv = ConversationHandler(
        entry_points=[CommandHandler("suamon", edit_recipe_start)],
        states={
            EDIT_PICK_FIELD: [
                CallbackQueryHandler(edit_pick_recipe_callback, pattern=r"^editpick:"),
                CallbackQueryHandler(edit_pick_field_callback, pattern=r"^editfield:"),
                CallbackQueryHandler(edit_confirm_delete_callback, pattern=r"^confirmdel:"),
                CallbackQueryHandler(edit_cancel_delete_callback, pattern=r"^canceldel$"),
            ],
            EDIT_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_value)],
        },
        fallbacks=[CommandHandler("huy", cancel_conversation)],
    )
    app.add_handler(edit_recipe_conv)

    app.add_handler(CommandHandler("xoamon", delete_recipe_command))
    app.add_handler(CallbackQueryHandler(edit_confirm_delete_callback, pattern=r"^confirmdel:"))
    app.add_handler(CallbackQueryHandler(edit_cancel_delete_callback, pattern=r"^canceldel$"))

    app.add_handler(CallbackQueryHandler(handle_recipe_callback, pattern=r"^r:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting (polling)... admin_id=%s", ADMIN_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
