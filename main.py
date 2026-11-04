import logging, os, re
from io import BytesIO

import pytesseract
import requests
from flask import Flask, abort, jsonify, request
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dupo-warranty")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Thiếu BOT_TOKEN trong Render Environment.")
if not PUBLIC_URL.startswith("https://"):
    raise RuntimeError("PUBLIC_URL phải là URL HTTPS của Render.")
if not WEBHOOK_SECRET:
    raise RuntimeError("Thiếu WEBHOOK_SECRET trong Render Environment.")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
WEBHOOK_URL = f"{PUBLIC_URL}/telegram"
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

def tg(method, payload=None, timeout=30):
    r = requests.post(f"{API}/{method}", json=payload or {}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data.get("result")

def send_text(chat_id, text, reply_to=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    return tg("sendMessage", data)

def download_telegram_file(file_id):
    info = tg("getFile", {"file_id": file_id})
    path = info.get("file_path")
    if not path:
        raise RuntimeError("Telegram không trả file_path.")
    r = requests.get(f"{FILE_API}/{path}", timeout=45)
    r.raise_for_status()
    return r.content

def read_image(image_bytes):
    im = ImageOps.exif_transpose(Image.open(BytesIO(image_bytes))).convert("RGB")
    w, h = im.size
    scale = min(3.0, max(1.5, 1800 / max(w, h)))
    if scale > 1:
        im = im.resize((int(w * scale), int(h * scale)))
    im = ImageOps.grayscale(im)
    im = ImageOps.autocontrast(im)
    im = ImageEnhance.Contrast(im).enhance(1.5)
    im = im.filter(ImageFilter.SHARPEN)
    return pytesseract.image_to_string(im, lang="eng", config="--oem 3 --psm 6")

def get_model(text):
    m = re.search(r"\bSM[\s\-]?[A-Z0-9]{4,10}\b", text, re.I)
    if m:
        return re.sub(r"[\s]", "", m.group(0).upper()).replace("SM", "SM-", 1).replace("SM--", "SM-")
    return None

def get_imeis(text):
    matches = re.findall(r"(?<!\d)(?:\d[\s\-]?){14}\d(?!\d)", text)
    found = []
    for item in matches:
        digits = re.sub(r"\D", "", item)
        if len(digits) == 15 and digits not in found:
            found.append(digits)
    return found[:2]

def make_result(text):
    model, imeis = get_model(text), get_imeis(text)
    lines = ["📱 <b>DUPO – KẾT QUẢ ĐỌC THÔNG TIN SAMSUNG</b>", ""]
    lines.append(f"• Model: <code>{model}</code>" if model else "• Model: Chưa nhận diện được")
    if imeis:
        lines.extend(f"• IMEI {i}: <code>{v}</code>" for i, v in enumerate(imeis, 1))
    else:
        lines.append("• IMEI: Chưa đọc được")
    lines += ["", "🛡️ <b>Bảo hành:</b> Chưa tra cứu trên hệ thống Samsung.",
              "ℹ️ Phiên bản này đọc thông tin trên ảnh, chưa kết nối hệ thống bảo hành chính thức của Samsung."]
    if not model and not imeis:
        lines += ["", "⚠️ Ảnh chưa đủ rõ. Hãy chụp thẳng tem hộp, đủ sáng và không bị lóa."]
    return "\n".join(lines)

def handle_message(msg):
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return
    message_id = msg.get("message_id")
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if text.startswith("/start"):
        send_text(chat_id,
            "👋 Chào mừng đến với <b>Samsung Warranty DUPO</b>!\n\n"
            "📸 Gửi ảnh tem hộp Samsung, bot sẽ tự đọc Model và IMEI.\n"
            "Dùng /help để xem hướng dẫn.", message_id)
        return
    if text.startswith("/help"):
        send_text(chat_id,
            "📌 <b>HƯỚNG DẪN</b>\n1. Chụp rõ tem hộp Samsung.\n"
            "2. Gửi ảnh vào đây.\n3. Bot đọc Model và tối đa 2 IMEI.\n\n"
            "Ảnh cần đủ sáng, không lóa và không cắt mất dãy số.", message_id)
        return

    photos = msg.get("photo") or []
    doc = msg.get("document") or {}
    file_id = photos[-1].get("file_id") if photos else None
    if not file_id and doc.get("mime_type", "").startswith("image/"):
        file_id = doc.get("file_id")

    if not file_id:
        if text:
            send_text(chat_id, "Bạn hãy gửi <b>ảnh tem hộp Samsung</b> để bot đọc Model và IMEI. Gõ /help để xem hướng dẫn.", message_id)
        return

    send_text(chat_id, "🔎 Đang đọc ảnh, vui lòng chờ...", message_id)
    try:
        ocr = read_image(download_telegram_file(file_id))
        result = make_result(ocr)
        send_text(chat_id, result)
        log.info("OCR completed; model=%s; imeis=%s", bool(get_model(ocr)), len(get_imeis(ocr)))
    except Exception:
        log.exception("PHOTO_PROCESSING_FAILED")
        send_text(chat_id, "❌ Chưa đọc được ảnh. Vui lòng gửi lại ảnh rõ hơn, chụp thẳng tem hộp và tránh phản chiếu ánh sáng.")

@app.get("/")
def home():
    return "DUPO Samsung Warranty Bot v2 is running", 200

@app.get("/health")
def health():
    return jsonify(status="ok", service="dupo-samsung-warranty-bot", version="2.0")

@app.post("/telegram")
def webhook():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != WEBHOOK_SECRET:
        abort(403)
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify(ok=False, error="invalid_json"), 400
    msg = update.get("message")
    if msg:
        try:
            handle_message(msg)
        except Exception:
            log.exception("UPDATE_HANDLER_FAILED")
    return jsonify(ok=True), 200

def register_webhook():
    result = tg("setWebhook", {
        "url": WEBHOOK_URL,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message"],
        "drop_pending_updates": False
    })
    log.info("WEBHOOK_REGISTERED result=%s url=%s", result, WEBHOOK_URL)

register_webhook()
