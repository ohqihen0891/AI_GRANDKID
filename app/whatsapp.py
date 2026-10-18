"""Official Meta WhatsApp Cloud API client (no unofficial libraries, no ban risk)."""
import hashlib
import hmac
import mimetypes
import os
import uuid
from datetime import datetime, timezone

import httpx

from .config import S

# Last result of any WhatsApp call, shown on the dashboard's WhatsApp card
STATUS = {"last_ok": None, "last_error": None, "last_error_at": None, "sent": 0, "failed": 0}

HINTS = {
    "131047": "24-hour window closed. Send any message from Mum's phone to the bot first, then retry.",
    "131026": "Message undeliverable. Check Mum's number has WhatsApp and is a verified test recipient.",
    "131030": "This number is not in your test recipient list (Meta > WhatsApp > API Setup > To). Add and verify it.",
    "131051": "Unsupported message type.",
    "131053": "Media upload error. Make sure ffmpeg is installed so voice notes are converted to OGG/Opus.",
    "190": "Access token expired or invalid. Generate a new token in Meta > WhatsApp > API Setup and update .env.",
    "100": "Invalid parameter. Check WHATSAPP_PHONE_NUMBER_ID (it is the Phone number ID, not the phone number).",
    "133010": "The test phone number is not registered. Re-open API Setup in Meta and follow the prompts.",
}


def enabled() -> bool:
    return bool(S.WHATSAPP_TOKEN and S.WHATSAPP_PHONE_NUMBER_ID)


def _base():
    return f"{S.GRAPH_API_BASE.rstrip('/')}/{S.GRAPH_API_VERSION}"


def _headers():
    return {"Authorization": f"Bearer {S.WHATSAPP_TOKEN}"}


def explain(text: str) -> str:
    for code, hint in HINTS.items():
        if f'"code":{code}' in text.replace(" ", "") or f"({code})" in text or f" {code} " in text:
            return hint
    return ""


def _record_error(msg: str):
    STATUS["last_error"] = msg[:400]
    STATUS["last_error_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATUS["failed"] += 1
    print(f"[whatsapp] ERROR: {msg[:500]}")


def _post_message(payload: dict):
    if not enabled():
        return None
    url = f"{_base()}/{S.WHATSAPP_PHONE_NUMBER_ID}/messages"
    body = {"messaging_product": "whatsapp", **payload}
    if "to" in payload:
        body["recipient_type"] = "individual"
    try:
        r = httpx.post(url, headers=_headers(), json=body, timeout=30)
    except Exception as e:
        _record_error(f"Network error: {e}")
        return None
    if r.status_code >= 400:
        hint = explain(r.text)
        _record_error(f"HTTP {r.status_code}: {r.text[:300]}" + (f"  HINT: {hint}" if hint else ""))
        return None
    if payload.get("status") != "read":
        STATUS["last_ok"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        STATUS["sent"] += 1
    return r.json()


def send_text(to: str, text: str):
    return _post_message({"to": to, "type": "text",
                          "text": {"body": text[:4096], "preview_url": False}})


def upload_media(path: str, mime: str):
    url = f"{_base()}/{S.WHATSAPP_PHONE_NUMBER_ID}/media"
    try:
        with open(path, "rb") as f:
            r = httpx.post(url, headers=_headers(), timeout=60,
                           data={"messaging_product": "whatsapp", "type": mime},
                           files={"file": (os.path.basename(path), f, mime)})
    except Exception as e:
        _record_error(f"Media upload network error: {e}")
        return None
    if r.status_code >= 400:
        hint = explain(r.text)
        _record_error(f"Media upload HTTP {r.status_code}: {r.text[:300]}" + (f"  HINT: {hint}" if hint else ""))
        return None
    return r.json().get("id")


def send_audio(to: str, path: str):
    """WhatsApp voice notes must be OGG/Opus. Returns None if it could not be sent."""
    if not path or not path.endswith(".ogg") or not os.path.exists(path):
        _record_error(f"Audio not sent: {os.path.basename(path or '')} is not an OGG file "
                      "(install ffmpeg so recordings can be converted)")
        return None
    media_id = upload_media(path, "audio/ogg")
    if media_id:
        return _post_message({"to": to, "type": "audio", "audio": {"id": media_id}})
    return None


def send_buttons(to: str, body: str, buttons: list):
    """buttons: [{"id": "...", "title": "<=20 chars"}] (max 3)"""
    return _post_message({
        "to": to, "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": b["id"][:256], "title": b["title"][:20]}}
                for b in buttons[:3]]}}})


def mark_read(message_id: str):
    _post_message({"status": "read", "message_id": message_id})


def download_media(media_id: str):
    """Returns (local_path, mime) or (None, None)."""
    try:
        r = httpx.get(f"{_base()}/{media_id}", headers=_headers(), timeout=30)
        r.raise_for_status()
        info = r.json()
        mime = info.get("mime_type", "application/octet-stream").split(";")[0].strip()
        r2 = httpx.get(info["url"], headers=_headers(), timeout=60, follow_redirects=True)
        r2.raise_for_status()
        ext = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
               "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
            mime, mimetypes.guess_extension(mime) or ".bin")
        os.makedirs(S.MEDIA_DIR, exist_ok=True)
        path = os.path.join(S.MEDIA_DIR, f"wa_{uuid.uuid4().hex[:10]}{ext}")
        with open(path, "wb") as f:
            f.write(r2.content)
        return path, mime
    except Exception as e:
        _record_error(f"Media download failed: {e}")
        return None, None


def verify_signature(raw: bytes, header: str) -> bool:
    if not S.WHATSAPP_APP_SECRET:
        return True  # signature check skipped (set WHATSAPP_APP_SECRET to enable)
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(S.WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])
