"""Family book: Mum's life in her own words, built chapter by chapter.

The AI proposes themed chapters. The family can switch chapters on/off and add their own
questions in the chat. A few times a week the Legacy agent asks Mum ONE gentle question
inside a normal chat, turns her answer (text or voice) into a bilingual page, and may
queue one natural follow-up question.
"""
from . import channel, chatlog, db, events, llm, mock_ai, prompts

CHAPTERS = [
    ("childhood", "🏡", "Where I grew up", "我細個嗰陣",
     "Her childhood home, family, games and food.",
     ["你細個喺邊度大㗎？屋企係點樣㗎？", "你細個最鍾意玩咩呀？", "你細個嗰陣，過年屋企會點慶祝㗎？"]),
    ("first_home", "🏠", "Our first home in Hong Kong", "我哋第一個家",
     "Moving, first jobs, the neighbourhood back then.",
     ["你第一次搬嚟呢區嗰陣，條街係點㗎？", "你後生第一份工做咩㗎？辛唔辛苦？", "以前啲街坊係點樣㗎？"]),
    ("love", "💑", "How I met Grandpa", "我同公公點識",
     "How they met, their wedding, life together.",
     ["你同公公係點識㗎？", "你哋結婚嗰日係點樣㗎？", "公公有咩事最令你掛住？"]),
    ("recipes", "🍲", "Family recipes", "屋企嘅味道",
     "Dishes and soups she cooked for the family, and who taught her.",
     ["你最拿手煮咩餸呀？可唔可以教下我點煮？", "你以前最鍾意煲咩湯俾啲仔女飲？", "邊個教你煮嘢食㗎？"]),
    ("when_you_were_little", "👶", "When you were little", "你細個嘅時候",
     "Stories about her children growing up.",
     ["{child}細個係咪好曳㗎？有冇咩好笑嘅事？", "{child}細個最鍾意食你煮咩？", "你記唔記得{child}第一日返學嘅情形？"]),
    ("wisdom", "🌱", "What life taught me", "人生教識我嘅嘢",
     "Advice and blessings for the grandchildren.",
     ["你想同孫仔講咩人生道理呀？", "你人生最開心嘅一日係邊日？", "如果可以返去後生嗰陣，你會同自己講咩？"]),
]


def seed_chapters(pid: int, child_zh: str = "你個女"):
    for i, (key, emoji, title, title_zh, desc, qs) in enumerate(CHAPTERS):
        cid = db.x("INSERT INTO book_chapters(parent_id, key, emoji, title, title_zh, description, active, sort) "
                   "VALUES(?,?,?,?,?,?,1,?)", (pid, key, emoji, title, title_zh, desc, i))
        for q in qs:
            db.x("INSERT INTO book_questions(parent_id, chapter_id, question_zh, source, status, created_at) "
                 "VALUES(?,?,?,?,?,?)", (pid, cid, q.replace("{child}", child_zh), "ai", "pending", db.now_iso()))


def chapters(pid: int) -> list:
    out = []
    for c in db.q("SELECT * FROM book_chapters WHERE parent_id=? ORDER BY sort, id", (pid,)):
        c["entries"] = db.q("SELECT * FROM book_entries WHERE chapter_id=? ORDER BY id", (c["id"],))
        c["questions"] = db.q("SELECT * FROM book_questions WHERE chapter_id=? AND status IN ('pending','asked') "
                              "ORDER BY CASE source WHEN 'child' THEN 0 WHEN 'followup' THEN 1 ELSE 2 END, id", (c["id"],))
        out.append(c)
    return out


def chapter_by_key(pid: int, key: str):
    return db.one("SELECT * FROM book_chapters WHERE parent_id=? AND key=?", (pid, key))


def add_question(parent, chapter_key_or_id, question_zh: str, question_en: str = "", source="child"):
    pid = parent["id"]
    ch = db.one("SELECT * FROM book_chapters WHERE id=?", (chapter_key_or_id,)) if str(chapter_key_or_id).isdigit() \
        else chapter_by_key(pid, chapter_key_or_id)
    if not ch:
        ch = db.one("SELECT * FROM book_chapters WHERE parent_id=? ORDER BY sort LIMIT 1", (pid,))
    db.x("UPDATE book_chapters SET active=1 WHERE id=?", (ch["id"],))
    qid = db.x("INSERT INTO book_questions(parent_id, chapter_id, question_zh, question_en, source, status, created_at) "
               "VALUES(?,?,?,?,?,?,?)", (pid, ch["id"], question_zh, question_en, source, "pending", db.now_iso()))
    events.log(pid, "Legacy", f"Question added to \"{ch['title']}\"", question_en or question_zh)
    return ch, qid


def next_question(pid: int):
    """Child's own questions first, then follow-ups, then the chapter with the fewest pages."""
    rows = db.q("""SELECT q.*, c.title, c.title_zh, c.emoji,
                   (SELECT COUNT(*) FROM book_entries e WHERE e.chapter_id=c.id) AS n
                   FROM book_questions q JOIN book_chapters c ON c.id=q.chapter_id
                   WHERE q.parent_id=? AND q.status='pending' AND c.active=1
                   ORDER BY CASE q.source WHEN 'child' THEN 0 WHEN 'followup' THEN 1 ELSE 2 END, n, c.sort, q.id""",
                (pid,))
    return rows[0] if rows else None


def add_chapter(parent, title: str, emoji: str = "✨", title_zh: str = "", description: str = ""):
    n = db.one("SELECT COALESCE(MAX(sort), 0) + 1 AS n FROM book_chapters WHERE parent_id=?", (parent["id"],))["n"]
    cid = db.x("INSERT INTO book_chapters(parent_id, key, emoji, title, title_zh, description, active, sort) "
               "VALUES(?,?,?,?,?,?,1,?)", (parent["id"], f"custom_{n}", emoji or "✨", title, title_zh, description, n))
    events.log(parent["id"], "Legacy", "New chapter added to the family book", title)
    return cid


def plan_ask(parent, question_id: int):
    """Plan to ask Mum this question at a kind time (instantly in demo mode). Returns the task id."""
    from . import care
    q = db.one("SELECT q.*, c.title FROM book_questions q JOIN book_chapters c ON c.id=q.chapter_id WHERE q.id=?", (question_id,))
    if not q:
        return None
    slot, reason = care.next_good_slot()
    tid = care.create_task(parent, "book", f"Family book: {q['title']}", "回憶簿問題", {"type": "times", "at": [slot]},
                           {"question_id": question_id, "question_zh": q["question_zh"], "reason": reason}, status="active")
    events.log(parent["id"], "Legacy", "Will ask Mum a family-book question", q["question_zh"])
    return tid


def ask_next(parent, run_id: int, question_id: int = None):
    q = None
    if question_id:
        q = db.one("""SELECT q.*, c.title, c.title_zh, c.emoji FROM book_questions q JOIN book_chapters c ON c.id=q.chapter_id
                      WHERE q.id=? AND q.status IN ('pending','asked')""", (question_id,))
    q = q or next_question(parent["id"])
    if not q:
        return None
    nick = parent["nickname"]
    intro = f"{nick}，{parent['child_name_zh']}好想知道：" if q["source"] == "child" else f"{nick}，想同你傾下以前嘅事😊 "
    channel.send_text(parent, intro + q["question_zh"])
    db.x("UPDATE book_questions SET status='asked', asked_at=? WHERE id=?", (db.now_iso(), q["id"]))
    db.x("UPDATE care_runs SET ref=? WHERE id=?", (q["id"], run_id))
    events.log(parent["id"], "Legacy", f"Asked a family-book question ({q['title']})", q["question_zh"])
    return q


def save_answer(parent, run, answer: str, voice_path: str = None, question=None):
    """Turn Mum's answer into a book page. `run` may be None for stories she shares on her own."""
    pid = parent["id"]
    q = question or (db.one("SELECT * FROM book_questions WHERE id=?", (run["ref"],)) if run and run.get("ref") else None)
    ch = db.one("SELECT * FROM book_chapters WHERE id=?", (q["chapter_id"],)) if q else None
    question_text = q["question_zh"] if q else ""

    page = write_page(parent, question_text, answer, ch)
    if not ch:
        ch = chapter_by_key(pid, page.get("chapter") or "") or \
            db.one("SELECT * FROM book_chapters WHERE parent_id=? ORDER BY sort LIMIT 1", (pid,))
    import os
    eid = db.x("INSERT INTO book_entries(parent_id, chapter_id, question_id, title, title_zh, story_zh, story_en, quote, "
               "original, voice_path, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (pid, ch["id"], q["id"] if q else None, page.get("title") or "A memory", page.get("title_zh") or "",
                page.get("story_zh") or answer, page.get("story_en") or "", page.get("quote") or "", answer,
                os.path.basename(voice_path) if voice_path else None, db.now_iso()))
    if q:
        # also close identical questions in the chapter (e.g. the child asked a question the AI also had)
        db.x("UPDATE book_questions SET status='answered' WHERE id=? OR (chapter_id=? AND question_zh=? AND status='pending')",
             (q["id"], q["chapter_id"], q["question_zh"]))
    fu = (page.get("follow_up_zh") or "").strip()
    if fu and (not q or q["source"] != "followup"):
        db.x("INSERT INTO book_questions(parent_id, chapter_id, question_zh, source, status, created_at) "
             "VALUES(?,?,?,?,?,?)", (pid, ch["id"], fu, "followup", "pending", db.now_iso()))
    events.log(pid, "Legacy", f"New page in \"{ch['title']}\"", page.get("title") or "")
    n = db.one("SELECT COUNT(*) n FROM book_entries WHERE chapter_id=?", (ch["id"],))["n"]
    chatlog.post(pid, "ai", f"📖 New page in Mum's family book: **{page.get('title')}** "
                            f"({ch['emoji']} {ch['title']}, page {n}).",
                 {"type": "book", "entry_id": eid, "chapter_id": ch["id"]})
    return {"id": eid, "title": page.get("title")}


def write_page(parent, question: str, answer: str, chapter) -> dict:
    if llm.is_mock():
        return mock_ai.book_page(question, answer, chapter["key"] if chapter else None)
    keys = ", ".join(c[0] for c in CHAPTERS)
    try:
        return llm.chat_json([
            {"role": "system", "content": prompts.BOOK_PAGE_SYSTEM.format(keys=keys, nickname=parent["nickname"])},
            {"role": "user", "content": f"Chapter: {chapter['title'] if chapter else '(choose one)'}\n"
                                        f"Question asked: {question or '(she shared this on her own)'}\n"
                                        f"Mum's answer: {answer}"}])
    except Exception as e:
        print("[book] page writing failed:", e)
        return mock_ai.book_page(question, answer, chapter["key"] if chapter else None)
