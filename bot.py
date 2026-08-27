import json
import logging
import os
import re
import socket
import difflib
from pathlib import Path

from unidecode import unidecode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
load_dotenv()

# Ép chỉ phân giải IPv4 — tránh treo khi hệ thống ưu tiên route IPv6 không ổn định
_original_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent / "data" / "recipes.json"
BOT_TOKEN = os.environ.get("BOT_TOKEN")


def normalize(text: str) -> str:
    """Bỏ dấu, chữ thường, bỏ khoảng trắng thừa để so khớp không phân biệt dấu/hoa-thường."""
    text = unidecode(text.lower())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class RecipeStore:
    def __init__(self, path: Path):
        self.recipes = json.loads(path.read_text(encoding="utf-8"))
        for r in self.recipes:
            r["_norm"] = normalize(r["name"])
        logger.info("Loaded %d recipes", len(self.recipes))

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

    def by_index(self, idx: int):
        if 0 <= idx < len(self.recipes):
            return self.recipes[idx]
        return None

    def index_of(self, recipe: dict) -> int:
        return self.recipes.index(recipe)


store: RecipeStore | None = None


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


def escape_md(text: str) -> str:
    if not text:
        return text
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Xin chào! Gõ tên món (có dấu hoặc không dấu đều được), "
        "mình sẽ gửi lại công thức, định lượng, dụng cụ và cách pha.\n\n"
        "Ví dụ: \"cà phê sữa\" hoặc \"ca phe sua\"\n\n"
        "Gõ /list để xem toàn bộ menu."
    )


async def list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        [InlineKeyboardButton(r["name"], callback_data=f"r:{store.index_of(r)}")]
        for r in results
    ]
    await update.message.reply_text(
        f"Tìm thấy {len(results)} món khớp với \"{query}\", chọn món bạn cần:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":", 1)[1])
    recipe = store.by_index(idx)
    if not recipe:
        await query.edit_message_text("Món này không còn tồn tại trong dữ liệu.")
        return
    await query.edit_message_text(format_recipe(recipe), parse_mode=ParseMode.MARKDOWN_V2)


def main():
    global store
    if not BOT_TOKEN:
        raise RuntimeError(
            "Thiếu BOT_TOKEN. Đặt biến môi trường BOT_TOKEN = token bot lấy từ @BotFather."
        )
    store = RecipeStore(DATA_PATH)

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
        proxy=None,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_menu))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
