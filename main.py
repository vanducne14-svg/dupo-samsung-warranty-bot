
import os
import re
import json
import logging
from io import BytesIO

from flask import Flask, request, abort
from PIL import Image, ImageOps, ImageEnhance
import pytesseract
import telebot

# =====================================
# 1. CONFIGURATION
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger("dupo")

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML"
)

app = Flask(__name__)

# =====================================
# 2. REGEX NHAN DIEN MODEL VA IMEI
# =====================================

MODEL_RE = re.compile(
    r"\bSM[- ]?[A-Z0-9]{4,}(?:/[A-Z0-9]+)?\b",
    re.IGNORECASE
)

IMEI_RE = re.compile(
    r"(?<!\d)(?:\d[\s-]?){14}\d(?!\d)"
)

# =====================================
# 3. DOC ANH BANG OCR
# =====================================

def ocr(data):
    im = Image.open(BytesIO(data)).convert("RGB")

    w, h = im.size

    if max(w, h) < 1800:
        scale = min(3, 1800 / max(w, h))
        im = im.resize(
            (int(w * scale), int(h * scale))
        )

    im = ImageOps.grayscale(im)
    im = ImageEnhance.Contrast(im).enhance(2.0)
    im = ImageEnhance.Sharpness(im).enhance(2.0)

    return pytesseract.image_to_string(
        im,
        config="--psm 6"
    )


# =====================================
# 4. START / HELP
# =====================================

@bot.message_handler(commands=["start", "help"])
def start(m):
    log.info("START HANDLER: %s", m.text)

    bot.reply_to(
        m,
        "📱 <b>DUPO SAMSUNG WARRANTY BOT</b>\n\n"
        "Bot đang hoạt động!\n\n"
        "📸 Gửi ảnh tem hộp Samsung để nhận diện:\n"
        "• Model máy\n"
        "• IMEI 1\n"
        "• IMEI 2\n\n"
        "Hãy chụp rõ tem hộp, đủ ánh sáng."
    )


# =====================================
# 5. NHAN DIEN ANH TEM HOP
# =====================================

@bot.message_handler(content_types=["photo"])
def photo(m):

    msg = bot.reply_to(
        m,
        "🔎 Đang đọc tem hộp Samsung..."
    )

    try:
        file_info = bot.get_file(
            m.photo[-1].file_id
        )

        raw = bot.download_file(
            file_info.file_path
        )

        text = ocr(raw)

        log.info("OCR TEXT: %s", text)

        # Nhận diện model
        models = list(dict.fromkeys(
            MODEL_RE.findall(text)
        ))

        # Nhận diện IMEI
        imeis = []

        labeled = re.findall(
            r"IMEI\s*[12]?\s*[:\-]?\s*((?:\d[\s-]?){15})",
            text,
            re.IGNORECASE
        )

        for value in labeled:
            number = re.sub(r"\D", "", value)
            if len(number) == 15 and number not in imeis:
                imeis.append(number)

        if not imeis:
            candidates = IMEI_RE.findall(text)

            for value in candidates:
                number = re.sub(r"\D", "", value)

                if len(number) == 15 and number not in imeis:
                    imeis.append(number)

        # Tạo kết quả
        out = [
            "📷 <b>KẾT QUẢ NHẬN DIỆN TEM HỘP</b>",
            ""
        ]

        if models:
            out.append(
                "📱 <b>Model:</b> " + ", ".join(models)
            )
        else:
            out.append(
                "📱 <b>Model:</b> Chưa nhận diện được"
            )

        out.append("")

        if imeis:
            out.append(
                f"✅ <b>Đã nhận diện {len(imeis)} IMEI:</b>"
            )

            for i, value in enumerate(imeis, 1):
                out.append(
                    f"{i}. <code>{value}</code>"
                )
        else:
            out.append(
                "⚠️ Chưa đọc được IMEI 15 số. "
                "Vui lòng chụp rõ tem hộp hơn."
            )

        out += [
            "",
            "🛡️ <b>Bảo hành:</b> Chưa tra cứu "
            "trên hệ thống Samsung.",
            "",
            "DUPO TECH"
        ]

        bot.edit_message_text(
            "\n".join(out),
            chat_id=m.chat.id,
            message_id=msg.message_id
        )

        log.info("PHOTO HANDLER COMPLETED")

    except Exception:
        log.exception("OCR ERROR")

        bot.edit_message_text(
            "❌ Không xử lý được ảnh.\n"
            "Vui lòng thử lại hoặc gửi ảnh rõ hơn.",
            chat_id=m.chat.id,
            message_id=msg.message_id
        )


# =====================================
# 6. HEALTH CHECK
# =====================================

@app.get("/")
def health():
    return "DUPO Samsung Warranty Bot is running", 200


# =====================================
# 7. TELEGRAM WEBHOOK
# =====================================

@app.post("/telegram/" + WEBHOOK_SECRET)
def webhook():

    try:
        data = request.get_json(silent=True)

        if not data:
            log.error("WEBHOOK: No JSON")
            abort(400)

        update = telebot.types.Update.de_json(
            json.dumps(data)
        )

        log.info(
            "WEBHOOK RECEIVED update_id=%s",
            update.update_id
        )

        bot.process_new_updates([update])

        log.info("WEBHOOK PROCESSED")

        return "ok", 200

    except Exception:
        log.exception("WEBHOOK ERROR")
        return "error", 500


# =====================================
# 8. REGISTER TELEGRAM WEBHOOK
# =====================================

WEBHOOK_URL = (
    f"{PUBLIC_URL}/telegram/{WEBHOOK_SECRET}"
)

try:
    bot.remove_webhook()

    result = bot.set_webhook(
        url=WEBHOOK_URL,
        allowed_updates=["message"]
    )

    log.info("WEBHOOK REGISTERED: %s", result)

except Exception:
    log.exception("WEBHOOK REGISTRATION FAILED")


# =====================================
# 9. START APPLICATION
# =====================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
