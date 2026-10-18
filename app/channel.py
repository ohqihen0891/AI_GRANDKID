"""Send messages to a parent. Every message is saved to the DB (the simulator and the
dashboard read from there) and, if the parent uses real WhatsApp, is also sent via Meta."""
import os

from . import db, whatsapp


def uses_whatsapp(parent) -> bool:
    return parent.get("channel") == "whatsapp" and whatsapp.enabled()


def store(parent, direction, sender, mtype, content, media_path=None, meta=None, wa_msg_id=None):
    return db.x(
        "INSERT INTO messages(parent_id, direction, sender, type, content, media_path, meta, "
        "wa_msg_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (parent["id"], direction, sender, mtype, content,
         os.path.basename(media_path) if media_path else None,
         db.jdump(meta) if meta else None, wa_msg_id, db.now_iso()))


def send_text(parent, text: str, sender: str = "ai"):
    store(parent, "out", sender, "text", text)
    if uses_whatsapp(parent):
        whatsapp.send_text(parent["wa_id"], text)


def send_audio(parent, path: str, sender: str = "ai", transcript: str = None):
    store(parent, "out", sender, "audio", transcript or "", media_path=path)
    if uses_whatsapp(parent):
        ok = whatsapp.send_audio(parent["wa_id"], path)
        if not ok and transcript and not transcript.startswith("("):
            # voice note could not be delivered: send the words as text so Mum still gets them
            whatsapp.send_text(parent["wa_id"], transcript)


def send_buttons(parent, body: str, buttons: list, sender: str = "ai"):
    store(parent, "out", sender, "buttons", body, meta={"buttons": buttons})
    if uses_whatsapp(parent):
        whatsapp.send_buttons(parent["wa_id"], body, buttons)
