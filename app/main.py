"""AI GrandKid - FastAPI server.

Run:  uvicorn app.main:app --reload --port 8000   (or start.bat / start.sh)
  Demo view (side by side): http://localhost:8000/demo
  Family app (child side):  http://localhost:8000/
  Mum's phone (simulator):  http://localhost:8000/sim
  WhatsApp webhook:         https://<public-url>/webhook   (optional, production path)
"""
import os
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import agents, book, care, chatlog, clock, coordinator, db, events, llm, scheduler, seed, speech, timelapse, whatsapp
from .config import S

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@asynccontextmanager
async def lifespan(app):
    db.init()
    if S.RESET_ON_START or not db.one("SELECT id FROM parents LIMIT 1"):
        seed.seed()   # demo: every start is a clean slate (set RESET_ON_START=false to keep data)
    scheduler.start()
    print("\n=== AI GrandKid running ===")
    print(f"AI: {'MOCK (no API key, rule-based)' if llm.is_mock() else S.LLM_MODEL + ' @ ' + S.LLM_BASE_URL}")
    print(f"Speech-to-text: {S.ASR_PROVIDER}   Voice replies: {S.TTS_ENABLED}   ffmpeg: {speech.has_ffmpeg()}")
    print(f"WhatsApp: {'configured' if whatsapp.enabled() else 'not configured (Mum uses the simulator)'}")
    print("Family app: http://localhost:8000/     Mum's phone: http://localhost:8000/sim\n")
    yield


app = FastAPI(title="AI GrandKid", lifespan=lifespan)
os.makedirs(S.MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=S.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def home():
    return FileResponse(os.path.join(STATIC, "app.html"))


@app.get("/demo")
def demo_view():
    return FileResponse(os.path.join(STATIC, "demo.html"))


@app.get("/sim")
def simulator():
    return FileResponse(os.path.join(STATIC, "simulator.html"))


def save_upload(f: UploadFile, prefix: str) -> str:
    ext = os.path.splitext(f.filename or "")[1] or {
        "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
        "image/png": ".png", "image/jpeg": ".jpg"}.get((f.content_type or "").split(";")[0], ".bin")
    path = os.path.join(S.MEDIA_DIR, f"{prefix}_{uuid.uuid4().hex[:10]}{ext}")
    with open(path, "wb") as out:
        shutil.copyfileobj(f.file, out)
    return path


def parent_or_404(pid: int):
    p = db.get_parent(pid)
    if not p:
        raise HTTPException(404, "parent not found")
    return p


# ====================================================================== state for the family app
@app.get("/api/state")
def state(parent_id: int = 1):
    p = parent_or_404(parent_id)
    pid, tz = p["id"], p["child_tz"]
    hk, ch = clock.parent_now(), clock.child_now(tz)
    tasks = [care.task_view(t, tz) for t in care.tasks_for(pid) if t["status"] not in ("deleted", "cancelled")]
    task_by_id = {t["id"]: t for t in tasks}
    chat = db.q("SELECT * FROM chat WHERE parent_id=? ORDER BY id", (pid,))
    for m in chat:
        m["card"] = db.jload(m["card"], None)
        m["when"] = clock.to_parent(m["created_at"]).strftime("%a %d %b · ") + clock.fmt_both(m["created_at"], tz)
    alerts = db.q("SELECT * FROM alerts WHERE parent_id=? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC LIMIT 15", (pid,))
    for a in alerts:
        a["evidence"] = db.jload(a["evidence"], [])
        a["actions"] = db.jload(a["actions"], [])
        a["when"] = clock.fmt_both(a["created_at"], tz)
    today = hk.strftime("%Y-%m-%d")
    today_runs = [r for t in tasks for r in t["runs"] if r["slot"][:10] == today]
    last = db.one("SELECT created_at FROM messages WHERE parent_id=? AND sender='parent' ORDER BY id DESC LIMIT 1", (pid,))
    chapters = book.chapters(pid)
    return {
        "parent": p,
        "mode": {"llm": "mock" if llm.is_mock() else S.LLM_MODEL, "asr": S.ASR_PROVIDER, "whatsapp": whatsapp.enabled()},
        "clock": {"hk": hk.strftime("%H:%M"), "hk_day": hk.strftime("%a %d %b"), "child": ch.strftime("%H:%M"),
                  "child_day": ch.strftime("%a %d %b"), "mum_awake": care.in_waking_hours(hk), "offset_min": clock.offset_minutes()},
        "tasks": tasks,
        "task_by_id": task_by_id,
        "today": {"done": len([r for r in today_runs if r["status"] in ("taken", "answered", "delivered")]),
                  "total": len(today_runs),
                  "last_heard": clock.fmt_both(last["created_at"], tz) if last else None,
                  "open_alerts": len([a for a in alerts if a["status"] == "open"])},
        "alerts": alerts,
        "chat": chat,
        "chat_busy": db.kv_get(f"busy:{pid}") == "1",
        "book": {"chapters": chapters, "pages": sum(len(c["entries"]) for c in chapters)},
        "events": db.q("SELECT * FROM events WHERE parent_id=? ORDER BY id DESC LIMIT 40", (pid,)),
        "upcoming": timelapse.upcoming(1),
        "demo_instant": S.DEMO_INSTANT,
    }


# ====================================================================== the child's chat
def _run_chat(parent, text):
    try:
        coordinator.handle_child_message(parent, text)
    except Exception as e:
        import traceback
        traceback.print_exc()
        chatlog.post(parent["id"], "ai", f"Sorry, something went wrong on my side ({e}). Please try again.")
    finally:
        db.kv_set(f"busy:{parent['id']}", "0")


@app.post("/api/chat")
def chat_send(bg: BackgroundTasks, parent_id: int = Form(1), text: str = Form(...)):
    p = parent_or_404(parent_id)
    text = text.strip()
    if not text:
        raise HTTPException(400, "empty message")
    chatlog.post(p["id"], "child", text)
    db.kv_set(f"busy:{p['id']}", "1")
    bg.add_task(_run_chat, p, text)
    return {"ok": True}


@app.post("/api/chat/voice")
async def chat_voice(bg: BackgroundTasks, parent_id: int = Form(1), text: str = Form(""), file: UploadFile = File(...)):
    p = parent_or_404(parent_id)
    raw = save_upload(file, "child_voice")
    path = speech.to_ogg_opus(raw) or raw
    chatlog.post(p["id"], "child", "🎙 Voice message for Mum" + (f": {text}" if text else ""),
                 {"cards": [{"type": "voice", "path": os.path.basename(path)}]})
    bg.add_task(coordinator.handle_child_voice, p, path, text)
    return {"ok": True}


# ====================================================================== care tasks
@app.post("/api/tasks/{tid}/voice")
async def task_voice(tid: int, file: UploadFile = File(...)):
    t = care.get_task(tid)
    if not t:
        raise HTTPException(404)
    raw = save_upload(file, "task_voice")
    path = speech.to_ogg_opus(raw) or raw
    db.x("UPDATE care_tasks SET voice_path=? WHERE id=?", (os.path.basename(path), tid))
    events.log(t["parent_id"], "Health", "Your voice reminder saved", t["title"])
    return {"ok": True, "voice_path": os.path.basename(path)}


@app.post("/api/tasks/{tid}/{action}")
def task_action(tid: int, action: str, bg: BackgroundTasks):
    t = care.get_task(tid)
    if not t:
        raise HTTPException(404)
    p = db.get_parent(t["parent_id"])
    if action == "confirm":
        coordinator.confirm(tid)
    elif action == "cancel":
        care.set_status(tid, "cancelled")
        chatlog.post(p["id"], "ai", f"OK, I won't start **{t['title']}**.")
    elif action in ("pause", "resume", "delete"):
        care.set_status(tid, {"pause": "paused", "resume": "active", "delete": "deleted"}[action])
        events.log(p["id"], "Orchestrator", action.capitalize(), t["title"])
    elif action == "run":  # demo: carry it out right now
        if t["status"] == "pending_confirm":
            coordinator.confirm(tid)
            t = care.get_task(tid)
        bg.add_task(care.execute, t)
    else:
        raise HTTPException(400, "unknown action")
    return {"ok": True}


@app.post("/api/alerts/{aid}/action")
def alert_action(aid: int, bg: BackgroundTasks, action: str = Form(...)):
    bg.add_task(agents.alert_action, aid, action)
    return {"ok": True}


# ====================================================================== family book
@app.post("/api/book/chapters/{cid}/question")
def chapter_question(cid: int, text: str = Form(...)):
    c = db.one("SELECT * FROM book_chapters WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    p = db.get_parent(c["parent_id"])
    zh = text if any("一" <= ch <= "鿿" for ch in text) else translate_question(p, text)
    book.add_question(p, cid, zh, text)
    return {"ok": True}


def translate_question(parent, text):
    if llm.is_mock():
        return f"{parent['child_name_zh']}好想知道：{text}"
    try:
        return llm.chat([{"role": "system", "content": f"Rewrite this question from {parent['child_name']} to their elderly mother as ONE "
                                                       f"gentle, warm question in colloquial Cantonese (Traditional Chinese). Return only the question."},
                         {"role": "user", "content": text}], max_tokens=120, temperature=0.4)
    except Exception:
        return f"{parent['child_name_zh']}好想知道：{text}"


@app.post("/api/book/chapters")
def chapter_add(parent_id: int = Form(1), title: str = Form(...), emoji: str = Form("✨"), first_question: str = Form("")):
    p = parent_or_404(parent_id)
    cid = book.add_chapter(p, title.strip(), emoji.strip() or "✨")
    if first_question.strip():
        q = first_question.strip()
        zh = q if any("\u4e00" <= ch <= "\u9fff" for ch in q) else translate_question(p, q)
        book.add_question(p, cid, zh, q)
    return {"ok": True, "chapter_id": cid}


@app.post("/api/book/questions/{qid}/ask")
def question_ask(qid: int, bg: BackgroundTasks):
    q = db.one("SELECT * FROM book_questions WHERE id=?", (qid,))
    if not q:
        raise HTTPException(404)
    p = db.get_parent(q["parent_id"])
    tid = book.plan_ask(p, qid)
    bg.add_task(coordinator.deliver, tid)
    return {"ok": True}


@app.post("/api/book/chapters/{cid}/ask")
def chapter_ask(cid: int, bg: BackgroundTasks):
    """Ask Mum the next question of this chapter."""
    q = db.one("SELECT * FROM book_questions WHERE chapter_id=? AND status='pending' "
               "ORDER BY CASE source WHEN 'child' THEN 0 WHEN 'followup' THEN 1 ELSE 2 END, id LIMIT 1", (cid,))
    if not q:
        raise HTTPException(400, "No questions left in this chapter. Add one first.")
    return question_ask(q["id"], bg)


# ====================================================================== demo controls
@app.post("/demo/next")
def demo_next():
    """Jump to the next thing in Mum's day and make it happen."""
    e = timelapse.next_event()
    if not e:
        raise HTTPException(400, "Nothing scheduled yet. Ask for something in the chat first.")
    return {"ok": True, "event": e["label"], "when": e["when"]}


@app.post("/demo/brief")
def demo_brief(bg: BackgroundTasks, parent_id: int = Form(1)):
    """Jump to the child's next morning and send the morning brief."""
    parent_or_404(parent_id)
    bg.add_task(timelapse.morning_brief, parent_id)
    return {"ok": True}


@app.post("/demo/reset")
def demo_reset():
    p = db.one("SELECT channel, wa_id FROM parents WHERE id=1")
    seed.seed()
    whatsapp.STATUS.update({"last_error": None, "last_error_at": None, "sent": 0, "failed": 0})
    if p and p["channel"] == "whatsapp":
        db.x("UPDATE parents SET channel=?, wa_id=? WHERE id=1", (p["channel"], p["wa_id"]))
    return {"ok": True}


# ====================================================================== Mum's phone (simulator)
@app.post("/sim/send")
async def sim_send(bg: BackgroundTasks, parent_id: int = Form(1), text: str = Form(""),
                   kind: str = Form("text"), file: UploadFile = File(None)):
    parent = parent_or_404(parent_id)
    if kind == "audio":
        path = save_upload(file, "sim_voice") if file else None
        bg.add_task(agents.handle_incoming, parent, "audio", text=text or None, media_path=path)
    elif kind == "image":
        path = save_upload(file, "sim_img") if file else None
        bg.add_task(agents.handle_incoming, parent, "image", text=text, media_path=path)
    else:
        bg.add_task(agents.handle_incoming, parent, "text", text=text)
    return {"ok": True}


@app.post("/sim/button")
def sim_button(bg: BackgroundTasks, parent_id: int = Form(1), button_id: str = Form(...), title: str = Form("")):
    bg.add_task(agents.handle_incoming, parent_or_404(parent_id), "button", text=title, button_id=button_id)
    return {"ok": True}


@app.get("/sim/messages")
def sim_messages(parent_id: int = 1, after_id: int = 0):
    rows = db.q("SELECT * FROM messages WHERE parent_id=? AND id>? ORDER BY id LIMIT 300", (parent_id, after_id))
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
    return rows


# ====================================================================== WhatsApp (production path, optional)
@app.get("/webhook")
def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == S.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "verification failed")


@app.post("/webhook")
async def receive(request: Request, bg: BackgroundTasks):
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(403, "bad signature")
    data = await request.json()
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                bg.add_task(process_wa_message, msg, names.get(msg.get("from")))
            for st in value.get("statuses", []):
                if st.get("status") == "failed":
                    errs = st.get("errors") or [{}]
                    code, title = errs[0].get("code"), errs[0].get("title", "")
                    hint = whatsapp.HINTS.get(str(code), "")
                    whatsapp._record_error(f"Delivery failed to {st.get('recipient_id')}: {code} {title}" + (f"  HINT: {hint}" if hint else ""))
    return {"ok": True}


def process_wa_message(msg: dict, profile_name: str = None):
    mid = msg.get("id")
    if db.one("SELECT wa_msg_id FROM processed WHERE wa_msg_id=?", (mid,)):
        return
    db.x("INSERT OR IGNORE INTO processed(wa_msg_id) VALUES(?)", (mid,))
    wa_id = msg.get("from")
    parent = db.one("SELECT * FROM parents WHERE wa_id=?", (wa_id,))
    demo = db.get_parent(1)
    if not parent and S.AUTO_LINK_FIRST_NUMBER and demo and demo["channel"] == "sim":
        db.x("UPDATE parents SET wa_id=?, channel='whatsapp' WHERE id=1", (wa_id,))
        parent = db.get_parent(1)
        events.log(1, "Orchestrator", "Linked Mum's WhatsApp", f"+{wa_id}")
    if not parent:
        print(f"[webhook] message from unknown number {wa_id} ignored")
        return
    if whatsapp.enabled():
        whatsapp.mark_read(mid)
    t = msg.get("type")
    if t == "text":
        agents.handle_incoming(parent, "text", text=msg["text"]["body"], wa_msg_id=mid)
    elif t == "audio":
        path, mime = whatsapp.download_media(msg["audio"]["id"])
        agents.handle_incoming(parent, "audio", media_path=path, mime=mime, wa_msg_id=mid)
    elif t == "image":
        path, mime = whatsapp.download_media(msg["image"]["id"])
        agents.handle_incoming(parent, "image", text=msg["image"].get("caption", ""), media_path=path, mime=mime, wa_msg_id=mid)
    elif t == "interactive":
        br = msg.get("interactive", {}).get("button_reply") or {}
        agents.handle_incoming(parent, "button", text=br.get("title"), button_id=br.get("id"), wa_msg_id=mid)
    else:
        agents.handle_incoming(parent, t or "unknown", wa_msg_id=mid)


@app.get("/api/whatsapp/status")
def wa_status(parent_id: int = 1):
    p = parent_or_404(parent_id)
    return {"configured": whatsapp.enabled(), "channel": p["channel"], "wa_id": p["wa_id"], **whatsapp.STATUS}
