# Bot công thức đồ uống — Telegram (có admin)

Bot nhận tên món (có dấu hoặc không dấu, gõ hơi sai cũng được), trả về:
định lượng nguyên liệu, phương pháp pha chế, dụng cụ/trang trí — lấy từ `data/recipes.json`.

Chỉ **admin** (bạn) mới thêm/sửa/xoá được nhân viên và món. Nhân viên chưa được thêm vào danh sách sẽ không dùng được bot.

## 1. Tạo bot Telegram

1. Chat với **@BotFather** trên Telegram → `/newbot` → đặt tên, lấy **token** (dạng `123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)

## 2. Lấy ADMIN_ID của bạn

1. Chat với bot **@userinfobot** trên Telegram (gõ bất kỳ tin nhắn nào) — nó trả về ID Telegram của bạn
2. Hoặc: chạy bot của bạn trước (bỏ qua bước cần ADMIN_ID tạm thời bằng cách đặt `ADMIN_ID=0`), chat `/myid` với bot để lấy ID, rồi set lại đúng ADMIN_ID và khởi động lại

## 3. Chạy thử ở máy local

```bash
cd bot
pip install -r requirements.txt
# Windows PowerShell:
$env:BOT_TOKEN="token_bot_của_bạn"
$env:ADMIN_ID="123456789"
python bot.py

# macOS/Linux:
export BOT_TOKEN="token_bot_của_bạn"
export ADMIN_ID="123456789"
python bot.py
```

Chat với bot bằng chính tài khoản có ID = ADMIN_ID → bạn sẽ thấy thêm menu lệnh quản trị khi gõ `/start`.

## 4. Quản lý nhân viên (chỉ admin dùng được)

| Lệnh | Chức năng |
|---|---|
| `/themnv <id> <tên>` | Thêm nhân viên vào whitelist |
| `/xoanv <id>` | Xoá nhân viên khỏi whitelist |
| `/dsnv` | Xem danh sách nhân viên |

**Quy trình thêm nhân viên mới:**
1. Nhân viên mở chat với bot, gõ `/myid` → bot trả về ID Telegram của họ
2. Họ gửi ID đó cho bạn
3. Bạn gõ `/themnv <id> <tên nhân viên>` — ví dụ: `/themnv 987654321 Lan`
4. Nhân viên gõ lại `/start` là dùng được bot ngay

Ai chưa có trong danh sách sẽ nhận thông báo "chưa được cấp quyền" kèm ID của họ khi thử nhắn cho bot.

## 5. Thêm / sửa / xoá món ngay trong Telegram (chỉ admin)

**Thêm món mới** — gõ `/themmon`, bot sẽ hỏi lần lượt:
1. Tên món
2. Nhóm món (gõ `/bo_qua` nếu không có)
3. Nguyên liệu / định lượng
4. Phương pháp pha chế
5. Dụng cụ / trang trí (gõ `/bo_qua` nếu không có)

Sau đó bot cho xem lại toàn bộ, gõ `/luu` để lưu hoặc `/huy` để huỷ bất cứ lúc nào trong quá trình.

**Sửa món** — gõ `/suamon <tên món>` (ví dụ: `/suamon trà đào`). Nếu khớp nhiều món, bot cho chọn. Sau đó bấm nút chọn trường muốn sửa (Tên món / Nhóm món / Nguyên liệu / Phương pháp / Dụng cụ), gõ giá trị mới là xong. Cũng có nút "🗑 Xoá món này" ngay trong màn hình sửa.

**Xoá món** — gõ `/xoamon <tên món>`, bot hỏi xác nhận trước khi xoá (không thể hoàn tác).

Mọi thay đổi lưu thẳng vào `data/recipes.json` — không cần sửa code, không cần restart bot.

## 6. Deploy free 24/7

Vẫn chạy kiểu polling, không cần domain/webhook.

### Railway.app (khuyên dùng)
1. Đẩy thư mục `bot/` lên GitHub
2. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Tab **Variables**, thêm:
   - `BOT_TOKEN` = token bot
   - `ADMIN_ID` = ID Telegram của bạn
4. Railway tự chạy theo `Procfile`

### Render.com (Background Worker)
1. Đẩy code lên GitHub
2. **New** → **Background Worker**, Build: `pip install -r requirements.txt`, Start: `python bot.py`
3. Thêm biến môi trường `BOT_TOKEN` và `ADMIN_ID`

> ⚠️ **Lưu ý quan trọng về dữ liệu:** `data/recipes.json` và `data/staff.json` được ghi trực tiếp lên ổ đĩa của server. Trên Railway/Render, dữ liệu này **có thể bị mất khi bạn deploy lại code mới** (redeploy tạo container mới từ đầu), trừ khi bạn gắn thêm **persistent volume/disk** (Railway: mục Volumes; Render: mục Disks — cả hai đều có trên gói free hoặc trả phí tuỳ thời điểm, kiểm tra lại chính sách hiện tại). Nếu không gắn volume, nên:
> - Backup định kỳ: tải `data/recipes.json` về sau khi thêm nhiều món qua bot
> - Hoặc chỉ redeploy khi thực sự sửa code, không phải mỗi khi thêm món

### Chạy trên máy/VPS riêng
```bash
pip install -r requirements.txt
export BOT_TOKEN="..."
export ADMIN_ID="..."
nohup python bot.py &
```

## 7. Tổng hợp lệnh

**Ai cũng dùng được (sau khi được thêm vào whitelist):**
- Gõ tên món → nhận công thức
- `/list` — xem toàn bộ menu
- `/myid` — lấy ID Telegram của mình
- `/start` — hướng dẫn

**Chỉ admin:**
- `/themnv <id> <tên>`, `/xoanv <id>`, `/dsnv`
- `/themmon`, `/suamon <tên món>`, `/xoamon <tên món>`
- `/huy` — huỷ luồng thêm/sửa món đang thực hiện
