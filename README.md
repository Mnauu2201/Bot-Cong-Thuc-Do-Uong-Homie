# Bot công thức đồ uống — Telegram

Bot nhận tên món (có dấu hoặc không dấu, gõ hơi sai cũng được), trả về:
định lượng nguyên liệu, phương pháp pha chế, dụng cụ/trang trí — lấy từ file `data/recipes.json`
(đã trích xuất sẵn từ file Word menu của quán, gồm 73 món + 15 công thức bán thành phẩm).

## 1. Tạo bot Telegram (2 phút)

1. Mở Telegram, chat với **@BotFather**
2. Gõ `/newbot`, đặt tên và username cho bot (username phải kết thúc bằng `bot`)
3. BotFather trả về một **token** dạng `123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` — lưu lại, không chia sẻ token này cho ai

## 2. Chạy thử ở máy local

```bash
cd bot
pip install -r requirements.txt
export BOT_TOKEN="token_bạn_vừa_lấy"
python bot.py
```

Vào Telegram, chat với bot, gõ thử: `cà phê sữa` hoặc `ca phe sua`, hoặc `/list` để xem toàn bộ menu.

## 3. Sửa/thêm công thức

Mở `data/recipes.json`, mỗi món là một object:

```json
{
  "name": "Tên món",
  "category": "Nhóm món",
  "ingredients": "Định lượng nguyên liệu",
  "method": "Cách pha",
  "tools": "Dụng cụ/trang trí"
}
```

Thêm/sửa/xoá object rồi lưu lại — không cần sửa code.

## 4. Deploy free 24/7

Bot chạy kiểu "polling" (tự hỏi Telegram liên tục) nên chỉ cần **một process chạy nền liên tục** — không cần domain hay webhook. Gợi ý các nền tảng free phù hợp:

### Cách A — Railway.app (khuyên dùng, dễ nhất)
1. Đẩy toàn bộ thư mục `bot/` này lên một repo GitHub
2. Vào [railway.app](https://railway.app) → đăng nhập bằng GitHub → **New Project** → **Deploy from GitHub repo**
3. Chọn repo vừa tạo
4. Vào tab **Variables**, thêm biến `BOT_TOKEN` = token bot của bạn
5. Railway tự nhận `requirements.txt` + `Procfile` (`worker: python bot.py`) và chạy 24/7
6. Gói free có giới hạn giờ chạy/tháng (khoảng $5 credit free/tháng) — đủ cho 1 bot cỡ nhỏ chạy cả tháng

### Cách B — Render.com (Background Worker)
1. Đẩy code lên GitHub như trên
2. Vào [render.com](https://render.com) → **New** → **Background Worker**
3. Chọn repo, **Build Command**: `pip install -r requirements.txt`, **Start Command**: `python bot.py`
4. Thêm biến môi trường `BOT_TOKEN` trong tab Environment
5. Deploy — Background Worker không "ngủ" như Web Service free nên chạy được 24/7 (gói free giới hạn số giờ/tháng, kiểm tra hạn mức hiện tại trên Render)

### Cách C — Máy chủ/VPS riêng hoặc máy tính để chạy liên tục
Nếu có sẵn máy chạy 24/7 (VPS, Raspberry Pi...), chỉ cần:
```bash
pip install -r requirements.txt
export BOT_TOKEN="..."
nohup python bot.py &
```
hoặc chạy qua `systemd`/`pm2` để tự khởi động lại khi crash hoặc reboot máy.

> Lưu ý: các gói "free" của Railway/Render đều có giới hạn (giờ chạy hoặc credit hàng tháng), có thể thay đổi theo chính sách của họ — kiểm tra lại trang giá hiện tại trước khi deploy để chắc chắn phù hợp nhu cầu chạy 24/7 lâu dài.

## 5. Lệnh bot hỗ trợ

- Gõ trực tiếp tên món → nhận công thức (nếu ra nhiều kết quả, bot gửi nút bấm để chọn đúng món)
- `/list` — xem toàn bộ menu theo nhóm
- `/start` — hướng dẫn sử dụng
