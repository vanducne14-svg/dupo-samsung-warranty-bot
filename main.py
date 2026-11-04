import os
import re
import time
import threading
import logging
from io import BytesIO

import requests
import pytesseract
from PIL import Image, ImageOps, ImageEnhance
from flask import Flask, jsonify, request

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("dupo-warranty")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
PORT = int(os.getenv("PORT", "10000"))

app = Flask(__name__)
polling_started = False
polling_lock = threading.Lock()


def telegram(method, data=None, files=None, timeout=40):
    if not BOT_TOKEN:
        raise RuntimeError("Thiếu biến môi trường BOT_TOKEN")
    response = requests.post(
        f"{API}/{method}",
        data=data or {},
        files=files,
        timeout=timeout
    )
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError(f"Telegram trả về HTTP {response.status_code}")
    if not response.ok or not payload.get("ok"):
        # Do not log token or full API URL.
        description = payload.get("description", "Telegram API error")
        raise RuntimeError(f"Telegram {method}: {description}")
    return payload.get("result")


def send_text(chat_id, text, reply_to=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    telegram("sendMessage", data=data)


def get_file_bytes(file_id):
    info = telegram("getFile", data={"file_id": file_id})
    file_path = info.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram không trả về đường dẫn ảnh")
    response = requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",
        timeout=45
    )
    response.raise_for_status()
    return response.content


def ocr_image(raw):
    image = Image.open(BytesIO(raw)).convert("RGB")
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.6)
    # Upscale small label photos to improve OCR.
    if image.width < 1800:
        scale = min(3, 1800 / max(image.width, 1))
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    return pytesseract.image_to_string(image, lang="eng", config="--psm 6")


def extract_data(text):
    normalized = text.upper().replace(" ", "").replace("-", "")
    models = sorted(set(re.findall(r"SM[A-Z0-9]{5,}", normalized)))
    # IMEI is exactly 15 digits. Ignore duplicates.
    imeis = []
    for value in re.findall(r"(?<!\d)\d{15}(?!\d)", text):
        if value not in imeis:
            imeis.append(value)
    return models[:5], imeis[:2]


def help_text():
    return (
        "📱 SAMSUNG WARRANTY DUPO\n\n"
        "Gửi ảnh tem hộp Samsung rõ nét, bot sẽ đọc Model và IMEI.\n"
        "Có thể gửi ảnh dưới dạng Ảnh hoặc Tệp.\n\n"
        "Lưu ý: Bot chỉ đọc thông tin trên tem, chưa tra cứu ngày bảo hành "
        "trên hệ thống Samsung."
    )


def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if not chat_id:
        return

    text = (message.get("text") or "").strip()
    if text.startswith("/start") or text.startswith("/help"):
        send_text(chat_id, help_text(), message_id)
        return

    file_id = None
    photos = message.get("photo") or []
    if photos:
        file_id = photos[-1].get("file_id")
    elif message.get("document"):
        doc = message["document"]
        mime = (doc.get("mime_type") or "").lower()
        name = (doc.get("file_name") or "").lower()
        if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            file_id = doc.get("file_id")

    if not file_id:
        send_text(chat_id, "Bạn hãy gửi ảnh tem hộp Samsung (ảnh rõ, đủ sáng) để mình đọc Model và IMEI nhé.", message_id)
        return

    send_text(chat_id, "🔎 Đang đọc tem máy, vui lòng chờ...", message_id)
    try:
        raw = get_file_bytes(file_id)
        recognized = ocr_image(raw)
        models, imeis = extract_data(recognized)

        lines = ["📱 KẾT QUẢ ĐỌC TEM SAMSUNG", ""]
        lines.append("Model: " + ("\n".join(models) if models else "Chưa đọc rõ"))
        lines.append("")
        if imeis:
            lines.append("IMEI:")
            lines.extend(f"{i+1}. {value}" for i, value in enumerate(imeis))
        else:
            lines.append("IMEI: Chưa đọc rõ")
        lines += ["", "Bảo hành: Chưa tra cứu trên hệ thống Samsung."]
        lines += ["", "Nếu thông tin chưa chính xác, vui lòng chụp lại tem thẳng, rõ nét và không lóa sáng."]
        send_text(chat_id, "\n".join(lines))
    except Exception as exc:
        log.exception("Xử lý ảnh thất bại")
        send_text(chat_id, f"❌ Chưa đọc được ảnh. Vui lòng gửi ảnh rõ hơn.\nChi tiết: {str(exc)[:180]}")


def polling_loop():
    log.info("Bắt đầu Telegram long polling")
    # Remove any old webhook; long polling and webhook cannot run together.
    try:
        telegram("deleteWebhook", data={"drop_pending_updates": "false"})
        log.info("Đã xóa webhook cũ (nếu có)")
    except Exception:
        log.exception("Không xóa được webhook cũ")
        time.sleep(10)

    offset = None
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": '["message"]'}
            if offset is not None:
                params["offset"] = offset
            updates = telegram("getUpdates", data=params, timeout=35)
            for update in updates or []:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    try:
                        handle_message(message)
                    except Exception:
                        log.exception("Lỗi xử lý tin nhắn")
        except Exception:
            log.exception("Long polling gặp lỗi; sẽ thử lại sau 5 giây")
            time.sleep(5)


@app.get("/")
@app.get("/health")
def health():
    return jsonify(status="ok", service="Samsung Warranty DUPO", mode="long-polling")


def start_polling_once():
    global polling_started
    with polling_lock:
        if polling_started:
            return
        polling_started = True
        thread = threading.Thread(target=polling_loop, daemon=True, name="telegram-polling")
        thread.start()


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Thiếu BOT_TOKEN trong Environment của Render")
    start_polling_once()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)
