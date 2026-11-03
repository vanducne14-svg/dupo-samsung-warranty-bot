import os, re, logging
from io import BytesIO
from flask import Flask, request, abort
from PIL import Image, ImageOps, ImageEnhance
import pytesseract
import telebot

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dupo")
BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

MODEL_RE = re.compile(r"\bSM[- ]?[A-Z0-9]{4,}(?:/[A-Z0-9]+)?\b", re.I)
IMEI_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")

def ocr(data):
    im = Image.open(BytesIO(data)).convert("RGB")
    w, h = im.size
    if max(w, h) < 1800:
        s = min(3, 1800 / max(w, h))
        im = im.resize((int(w*s), int(h*s)))
    im = ImageOps.grayscale(im)
    im = ImageEnhance.Contrast(im).enhance(1.8)
    return pytesseract.image_to_string(im, config="--oem 3 --psm 6")

@bot.message_handler(commands=["start", "help"])
def start(m):
    bot.reply_to(m, "📱 <b>DUPO SAMSUNG WARRANTY BOT</b>\\nGửi ảnh tem hộp Samsung rõ nét. Bot sẽ đọc model và IMEI.\\n\\n⚠️ Phiên bản này chỉ nhận diện tem, chưa truy vấn cơ sở dữ liệu bảo hành Samsung nên không xác nhận ngày kích hoạt/hạn bảo hành.")

@bot.message_handler(content_types=["photo"])
def photo(m):
    msg = bot.reply_to(m, "🔎 Đang đọc tem hộp...")
    try:
        f = bot.get_file(m.photo[-1].file_id)
        raw = bot.download_file(f.file_path)
        text = ocr(raw)
        models = list(dict.fromkeys(MODEL_RE.findall(text)))
        imeis = list(dict.fromkeys(IMEI_RE.findall(text)))
        out = ["📷 <b>KẾT QUẢ NHẬN DIỆN TỪ ẢNH</b>",
               "Model: <b>" + (", ".join(models) if models else "Chưa đọc rõ") + "</b>"]
        if imeis:
            out.append(f"Đã nhận diện <b>{len(imeis)}</b> IMEI:")
            out += [f"{i}. <code>{v}</code>" for i, v in enumerate(imeis, 1)]
        else:
            out.append("Chưa đọc được IMEI 15 số. Hãy chụp gần tem hơn, đủ sáng và không nghiêng.")
        out += ["", "🛡️ <b>Bảo hành:</b> Chưa tra cứu được từ Samsung. Bot không tự suy đoán ngày kích hoạt hoặc hạn bảo hành."]
        bot.edit_message_text("\n".join(out), m.chat.id, msg.message_id)
    except Exception:
        log.exception("OCR error")
        bot.edit_message_text("Không xử lý được ảnh. Vui lòng gửi ảnh tem rõ nét hơn.", m.chat.id, msg.message_id)

@app.get("/")
def health():
    return "DUPO Samsung Warranty Bot is running", 200

@app.post("/telegram/" + WEBHOOK_SECRET)
def webhook():
    if not request.is_json:
        abort(400)
    update = telebot.types.Update.de_json(request.get_json())
    bot.process_new_updates([update])
    return "ok", 200

# Register Telegram webhook when the Render service starts.
bot.remove_webhook()
bot.set_webhook(url=f"{PUBLIC_URL}/telegram/{WEBHOOK_SECRET}")
