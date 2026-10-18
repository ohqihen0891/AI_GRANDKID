"""Mum's side of AI GrandKid: every message she sends flows through the agents.

  Orchestrator -> Safety (scam / emergency) -> Health + Insight (signals, trends, alerts)
  -> care tasks (her reply closes the loop) -> Legacy (family book) -> Companion (Cantonese reply)

Child's side helpers also live here: alerts, the morning brief and alert actions.
"""
import base64
import random
from collections import Counter

from . import care, channel, chatlog, clock, db, events, llm, mock_ai, prompts, speech
from .config import S


def _fmt(parent) -> dict:
    return {"name": parent["name"], "nickname": parent["nickname"], "child": parent["child_name"],
            "child_zh": parent["child_name_zh"], "city": parent["child_city"]}


# =============================================================== MEMORY / CONTEXT
def memory_block(parent) -> str:
    pid = parent["id"]
    facts = db.q("SELECT content FROM facts WHERE parent_id=? ORDER BY id DESC LIMIT 25", (pid,))
    sigs = db.q("SELECT category, value, created_at FROM signals WHERE parent_id=? AND created_at>=? "
                "ORDER BY id DESC LIMIT 30", (pid, db.ago_iso(days=7)))
    tasks = care.tasks_for(pid, include_inactive=False)
    pages = db.q("SELECT title FROM book_entries WHERE parent_id=? ORDER BY id DESC LIMIT 6", (pid,))
    lines = ["Known facts:"] + [f"- {f['content']}" for f in facts]
    lines += ["Care the family set up: " + "; ".join(f"{t['title']} ({care.describe_schedule(t['schedule'])})"
                                                      for t in tasks)]
    lines += ["Signals from the last 7 days:"] + [f"- {s['created_at'][:10]} {s['category']}: {s['value']}" for s in sigs]
    if pages:
        lines += ["Family-book pages already written: " + "; ".join(p["title"] for p in pages)]
    return "\n".join(lines)


def history_messages(parent, limit=12) -> list:
    rows = db.q("SELECT sender, content FROM messages WHERE parent_id=? ORDER BY id DESC LIMIT ?", (parent["id"], limit))
    out = []
    for r in reversed(rows):
        if not r["content"] or r["content"].startswith("("):
            continue
        role = "user" if r["sender"] == "parent" else "assistant"
        content = r["content"] if r["sender"] != "child" else f"[Voice/message from {parent['child_name']}] {r['content']}"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + content
        else:
            out.append({"role": role, "content": content})
    return out


# =============================================================== ENTRY POINT (webhook + simulator)
def handle_incoming(parent, mtype, text=None, media_path=None, mime=None, button_id=None, wa_msg_id=None):
    pid = parent["id"]
    try:
        if mtype == "button":
            channel.store(parent, "in", "parent", "button", text or button_id, wa_msg_id=wa_msg_id)
            return care.handle_button(parent, button_id)
        if mtype == "audio":
            events.log(pid, "Orchestrator", "Voice note received", "Transcribing Cantonese speech")
            transcript = text or speech.transcribe(media_path)
            channel.store(parent, "in", "parent", "audio", transcript or "", media_path=media_path, wa_msg_id=wa_msg_id)
            if not transcript:
                events.log(pid, "Orchestrator", "Could not transcribe", "Speech-to-text not configured or failed")
                return channel.send_text(parent, f"收到你段錄音呀{parent['nickname']}😊 不過我暫時聽唔清楚，"
                                                 f"可唔可以打字或者再講多次俾我聽？")
            events.log(pid, "Orchestrator", "Transcribed", transcript)
            return process_text(parent, transcript, was_voice=True, media_path=media_path)
        if mtype == "image":
            channel.store(parent, "in", "parent", "image", text or "", media_path=media_path, wa_msg_id=wa_msg_id)
            return process_image(parent, media_path, text or "")
        if mtype == "text":
            channel.store(parent, "in", "parent", "text", text, wa_msg_id=wa_msg_id)
            return process_text(parent, text)
        channel.store(parent, "in", "parent", mtype, text or f"[{mtype}]", wa_msg_id=wa_msg_id)
        channel.send_text(parent, f"{parent['nickname']}，呢種訊息我暫時未識睇呀，可以打字或者錄音俾我嗎？😊")
    except Exception as e:
        import traceback
        traceback.print_exc()
        events.log(pid, "Orchestrator", "Error", str(e))
        channel.send_text(parent, f"{parent['nickname']}，唔好意思，我頭先諗嘢諗得慢咗少少，你可以再講多次嗎？")


def process_text(parent, text: str, was_voice: bool = False, media_path: str = None):
    pid = parent["id"]
    msg = db.one("SELECT id FROM messages WHERE parent_id=? AND sender='parent' ORDER BY id DESC LIMIT 1", (pid,))
    msg_id = msg["id"] if msg else None
    events.log(pid, "Orchestrator", "Routing Mum's message to the agents", text[:80])
    analysis = analyze(parent, text)

    # ---- Safety
    scam = analysis.get("scam") or {}
    if scam.get("risk") in ("medium", "high"):
        events.log(pid, "Safety", f"Scam risk {scam['risk'].upper()}", scam.get("reason", ""))
        create_alert(parent, "scam", 3, "Possible scam targeting Mum",
                     f"Mum received a suspicious message ({scam.get('type') or 'fraud pattern'}). "
                     f"I warned her not to transfer money or share any codes. {scam.get('reason', '')}",
                     [text[:300]], [{"id": "call", "label": "I'll call her now"}, {"id": "scam_ok", "label": "Handled"}])
    else:
        events.log(pid, "Safety", "No scam or emergency")
    emerg = analysis.get("emergency") or {}
    if emerg.get("is_emergency"):
        events.log(pid, "Safety", "EMERGENCY - escalating", emerg.get("reason", ""))
        create_alert(parent, "emergency", 3, "URGENT: Mum may need help now",
                     f"Mum reported something that sounds urgent. I told her to call 999. {emerg.get('reason', '')}",
                     [text[:300]], [{"id": "call", "label": "I'll call her now"}])

    # ---- Health + Insight
    sigs = analysis.get("signals") or []
    for s in sigs:
        db.x("INSERT INTO signals(parent_id, category, value, detail, evidence, message_id, created_at) "
             "VALUES(?,?,?,?,?,?,?)", (pid, s.get("category", "other"), (s.get("value") or "").lower(),
                                       s.get("detail", ""), s.get("evidence") or text[:200], msg_id, db.now_iso()))
    health = [s for s in sigs if s.get("category") in ("pain", "medication", "sleep", "meal")]
    if health:
        events.log(pid, "Health", "Logged health signals", ", ".join(f"{s['category']}: {s['value']}" for s in health))
    for f in analysis.get("facts") or []:
        if not db.one("SELECT id FROM facts WHERE parent_id=? AND content=?", (pid, f)):
            db.x("INSERT INTO facts(parent_id, content, created_at) VALUES(?,?,?)", (pid, f, db.now_iso()))
            events.log(pid, "Companion", "Saved to long-term memory", f)
    run_insight(parent)

    # ---- Care tasks: her reply closes the loop (check-in answer, medicine taken, book answer...)
    ctx = care.link_reply(parent, text, analysis, media_path if was_voice else None)

    # ---- Legacy: a story she shared on her own
    story = analysis.get("story") or {}
    if story.get("is_story") and not ctx.get("book_answer"):
        from . import book
        book.save_answer(parent, None, text, media_path if was_voice else None)
        ctx["book_answer"] = True

    # ---- Companion
    reply = companion_reply(parent, text, analysis, ctx)
    events.log(pid, "Companion", "Replied in Cantonese", reply[:80])
    voice = speech.tts(reply) if was_voice else None
    if voice:
        channel.send_audio(parent, voice, transcript=reply)
    else:
        channel.send_text(parent, reply)


def analyze(parent, text: str) -> dict:
    if llm.is_mock():
        return mock_ai.analyze(text)
    try:
        return llm.chat_json([
            {"role": "system", "content": prompts.ANALYZE_SYSTEM},
            {"role": "user", "content": f"Context about this parent:\n{memory_block(parent)}\n\nMessage from the parent:\n{text}"}])
    except Exception as e:
        print("[analyze] failed, using rules:", e)
        return mock_ai.analyze(text)


def companion_reply(parent, text: str, analysis: dict, ctx: dict = None) -> str:
    ctx = ctx or {}
    if llm.is_mock():
        return mock_ai.reply(text, analysis, parent["nickname"], parent["child_name_zh"], ctx)
    sys = prompts.COMPANION_SYSTEM.format(**_fmt(parent)) + "\n\nMemory:\n" + memory_block(parent)
    note = (f"[Analysis of the latest message] scam risk: {analysis.get('scam', {}).get('risk', 'none')}; "
            f"emergency: {analysis.get('emergency', {}).get('is_emergency', False)}; "
            f"she just answered a family-book question or shared a story (saved): {bool(ctx.get('book_answer'))}; "
            f"medicine confirmed: {bool(ctx.get('medication_confirmed'))}; "
            f"signals: {[s.get('value') for s in analysis.get('signals', [])]}")
    msgs = [{"role": "system", "content": sys}] + history_messages(parent)
    if not msgs or msgs[-1]["role"] != "user":
        msgs.append({"role": "user", "content": text})
    msgs.insert(-1, {"role": "system", "content": note})
    try:
        out = llm.chat(msgs, temperature=0.8, max_tokens=300)
    except Exception as e:
        print("[companion] LLM failed:", e)
        return mock_ai.reply(text, analysis, parent["nickname"], parent["child_name_zh"], ctx)
    return safety_guard(out, analysis, parent)


def safety_guard(reply: str, analysis: dict, parent) -> str:
    """Safety warnings must never depend on the model following instructions."""
    nick, child = parent["nickname"], parent["child_name_zh"]
    if (analysis.get("emergency") or {}).get("is_emergency") and "999" not in reply:
        reply = f"{nick}，如果好唔舒服，即刻打999或者㩒平安鐘呀！我已經通知咗{child}。\n" + reply
    if (analysis.get("scam") or {}).get("risk") in ("medium", "high") and not ("騙" in reply and "18222" in reply):
        reply += (f"\n⚠️ {nick}，呢個好似係騙案：千祈唔好轉錢、唔好俾銀行資料或驗證碼、唔好撳連結。"
                  f"我已經話咗俾{child}知，有懷疑可以打防騙易熱線18222。")
    return reply


def proactive_message(parent, kind="checkin", focus=""):
    if llm.is_mock():
        msg = mock_ai.greeting(parent["nickname"])
    else:
        sys = prompts.COMPANION_SYSTEM.format(**_fmt(parent)) + "\n\nMemory:\n" + memory_block(parent)
        task = prompts.CHECKIN_TASK.format(nickname=parent["nickname"], hk_time=clock.parent_now().strftime("%H:%M"),
                                           focus=f"The family asked you to check on: {focus}." if focus else "")
        try:
            msg = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": task}], max_tokens=200)
        except Exception as e:
            print("[checkin] LLM failed:", e)
            msg = mock_ai.greeting(parent["nickname"])
    channel.send_text(parent, msg)
    return msg


# =============================================================== IMAGES
def process_image(parent, path: str, caption: str):
    pid = parent["id"]
    events.log(pid, "Orchestrator", "Photo received", "Reading the image")
    if llm.is_mock() or not path:
        info = mock_ai.describe_image(caption)
    else:
        try:
            mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
            b64 = base64.b64encode(open(path, "rb").read()).decode()
            info = llm.chat_json([
                {"role": "system", "content": prompts.VISION_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Caption from the parent: {caption or '(none)'}"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
                model=S.LLM_VISION_MODEL)
        except Exception as e:
            print("[vision] failed (model may not support images):", e)
            info = {"kind": "other", "text": "", "summary": "a photo", "scam_risk": "none"}
    events.log(pid, "Orchestrator", f"Image type: {info.get('kind')}", info.get("summary", ""))
    text = f"[Mum sent a photo] {info.get('summary', '')}\nText in photo: {info.get('text', '')}\nCaption: {caption}"
    if info.get("scam_risk") in ("medium", "high"):
        text += "\n(This looks like a scam message asking for money or codes.)"
    process_text(parent, text)


# =============================================================== INSIGHT (trends against her own week)
def run_insight(parent):
    pid = parent["id"]
    week = db.q("SELECT * FROM signals WHERE parent_id=? AND created_at>=? ORDER BY id", (pid, db.ago_iso(days=7)))
    pain = [s for s in week if s["category"] == "pain"]
    skipped = [s for s in week if s["category"] == "meal" and s["value"] == "skipped" and s["created_at"] >= db.ago_iso(days=3)]
    stayed = [s for s in week if s["category"] == "mobility" and s["value"] in ("stayed home", "difficulty walking")
              and s["created_at"] >= db.ago_iso(days=3)]
    low = [s for s in week if s["category"] == "mood" and s["value"] in ("low", "lonely", "anxious")]
    events.log(pid, "Insight", "Compared with Mum's usual week",
               f"pain x{len(pain)}, skipped meals x{len(skipped)}, low mood x{len(low)}")
    ev = lambda rows: [f"{clock.to_parent(s['created_at']).strftime('%a %d %b')}: {s['evidence']}" for s in rows]
    if len(pain) >= 3:
        part = Counter(s["value"] for s in pain).most_common(1)[0][0]
        extra = " She also skipped buying groceries or meals recently." if skipped or stayed else ""
        create_alert(parent, "pain", 2, f"{part.capitalize()} mentioned {len(pain)} times this week",
                     f"Mum has mentioned {part} {len(pain)} times in 7 days, more than usual for her.{extra}",
                     ev(pain[-4:]), [{"id": "call", "label": "I'll call her"}, {"id": "dismiss", "label": "Noted"}])
    if len(skipped) >= 2:
        create_alert(parent, "meals", 2, "Mum skipped meals recently",
                     f"Mum skipped meals {len(skipped)} times in the last 3 days.", ev(skipped[-3:]),
                     [{"id": "call", "label": "I'll call her"}, {"id": "dismiss", "label": "Noted"}])
    if len(low) >= 3:
        create_alert(parent, "mood", 2, "Mum seems lonelier than usual",
                     f"Low or lonely mood mentioned {len(low)} times this week.", ev(low[-3:]),
                     [{"id": "call", "label": "I'll call her"}])


def create_alert(parent, kind, level, title, detail, evidence, actions):
    """New alert -> also posted in the child's chat. Same open alert within 12h is refreshed, not repeated."""
    pid = parent["id"]
    if kind not in ("scam", "emergency") and db.one(
            "SELECT id FROM alerts WHERE parent_id=? AND kind=? AND title=? AND created_at>=?", (pid, kind, title, db.ago_iso(hours=48))):
        return None  # already told the child about exactly this (even if they handled it)
    existing = db.one("SELECT * FROM alerts WHERE parent_id=? AND kind=? AND status='open' AND created_at>=? "
                      "ORDER BY id DESC LIMIT 1", (pid, kind, db.ago_iso(hours=12)))
    if existing and kind not in ("scam", "emergency"):
        if existing["title"] != title:
            db.x("UPDATE alerts SET title=?, detail=?, evidence=?, actions=?, level=? WHERE id=?",
                 (title, detail, db.jdump(evidence), db.jdump(actions), level, existing["id"]))
            events.log(pid, "Family Bridge", f"Updated alert (level {level})", title)
        return existing["id"]
    aid = db.x("INSERT INTO alerts(parent_id, kind, level, title, detail, evidence, actions, status, created_at) "
               "VALUES(?,?,?,?,?,?,?,'open',?)",
               (pid, kind, level, title, detail, db.jdump(evidence), db.jdump(actions), db.now_iso()))
    events.log(pid, "Family Bridge", f"Told {parent['child_name']} (level {level})", title)
    chatlog.post(pid, "ai", f"{'🚨' if level == 3 else '⚠️'} **{title}**\n{detail}", {"type": "alert", "alert_id": aid})
    return aid


# =============================================================== FAMILY BRIDGE: morning brief
def build_timeline(parent, since_iso) -> list:
    tz = parent["child_tz"]
    items = []
    for r in db.q("SELECT r.*, t.kind, t.title FROM care_runs r JOIN care_tasks t ON t.id=r.task_id "
                  "WHERE r.parent_id=? AND r.sent_at>=? AND t.kind != 'book' ORDER BY r.sent_at", (parent["id"], since_iso)):
        icon = care.ICONS.get(r["kind"], "•")
        status = {"taken": "✅", "answered": "✅", "missed": "❌", "no_reply": "⚠️", "delivered": "✓",
                  "sent": "⏳", "followed_up": "⏳"}.get(r["status"], "")
        items.append({"t": r["sent_at"], "when": clock.fmt_both(r["sent_at"], tz),
                      "text": f"{icon} {r['title']} {status} {r['outcome'] or ''}".strip()})
    for a in db.q("SELECT * FROM alerts WHERE parent_id=? AND created_at>=?", (parent["id"], since_iso)):
        items.append({"t": a["created_at"], "when": clock.fmt_both(a["created_at"], tz),
                      "text": f"{'🚨' if a['level'] == 3 else '⚠️'} {a['title']}"})
    for e in db.q("SELECT * FROM book_entries WHERE parent_id=? AND created_at>=?", (parent["id"], since_iso)):
        items.append({"t": e["created_at"], "when": clock.fmt_both(e["created_at"], tz), "text": f"📖 New book page: {e['title']}"})
    for i in items:
        i["when"] = clock.to_parent(i["t"]).strftime("%a ") + i["when"]
    return sorted(items, key=lambda i: i["t"])


def generate_brief(parent, hours=24) -> dict:
    pid = parent["id"]
    events.log(pid, "Family Bridge", "Writing the morning brief", f"for {parent['child_name']} in {parent['child_city']}")
    last = db.one("SELECT created_at FROM digests WHERE parent_id=? ORDER BY id DESC LIMIT 1", (pid,))
    since = min(last["created_at"], db.ago_iso(hours=24)) if last else db.ago_iso(days=7)   # since the last brief, at least 24h
    timeline = build_timeline(parent, since)
    data = {
        "timeline": [i["text"] for i in timeline],
        "mum_said": [m["content"] for m in db.q("SELECT content FROM messages WHERE parent_id=? AND sender='parent' "
                                                "AND created_at>=? AND content!='' ORDER BY id", (pid, since))][-25:],
        "signals": db.q("SELECT category, value, evidence FROM signals WHERE parent_id=? AND created_at>=?", (pid, since)),
        "facts": [f["content"] for f in db.q("SELECT content FROM facts WHERE parent_id=? ORDER BY id DESC LIMIT 10", (pid,))],
        "open_alerts": [a["title"] for a in db.q("SELECT title FROM alerts WHERE parent_id=? AND status='open'", (pid,))],
    }
    if llm.is_mock():
        out = mock_ai.brief(data)
    else:
        try:
            out = llm.chat_json([
                {"role": "system", "content": prompts.BRIEF_SYSTEM.format(
                    **_fmt(parent), lang=S.CHILD_LANG,
                    child_now=clock.child_now(parent["child_tz"]).strftime("%A %H:%M"))},
                {"role": "user", "content": db.jdump(data)}])
        except Exception as e:
            print("[brief] LLM failed, using rules:", e)
            out = mock_ai.brief(data)
    out["timeline"] = [{"when": i["when"], "text": i["text"]} for i in timeline]
    out["next_actions"] = [a.get("label") if isinstance(a, dict) else str(a) for a in out.get("next_actions", []) if a]
    db.x("INSERT INTO digests(parent_id, content, created_at) VALUES(?,?,?)", (pid, db.jdump(out), db.now_iso()))
    chatlog.post(pid, "ai", f"☀️ Good morning {parent['child_name']}! Here's how Mum's day went while you were asleep.",
                 {"type": "brief", **out})
    events.log(pid, "Family Bridge", "Morning brief sent to the chat", out.get("headline", ""))
    return out


# =============================================================== the child acts on an alert
def tell_mum_child_will_call(parent):
    channel.send_text(parent, f"📞 {parent['nickname']}，{parent['child_name_zh']}話一陣就打電話俾你呀！")
    events.log(parent["id"], "Family Bridge", f"Told Mum that {parent['child_name']} will call")


def alert_action(alert_id: int, action: str):
    a = db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if not a:
        return None
    parent = db.get_parent(a["parent_id"])
    db.x("UPDATE alerts SET status='handled' WHERE id=?", (alert_id,))
    if action == "call":
        tell_mum_child_will_call(parent)
        chatlog.post(parent["id"], "ai", "📞 I've told Mum you'll call her soon. She'll be happy to hear from you.")
        return "call"
    events.log(parent["id"], "Family Bridge", "Alert marked as handled", a["title"])
    return "handled"
