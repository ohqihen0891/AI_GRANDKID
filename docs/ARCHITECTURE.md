# Architecture

AI GrandKid is a single FastAPI application with a SQLite database. The child uses a web app; the parent uses WhatsApp (a WhatsApp-style simulator in the demo). An AI agent sits in between: it uses a large language model for understanding and writing, and fixed rules for scheduling and safety.

## Components

| Component | File(s) | Role |
|---|---|---|
| Web server | `app/main.py` | HTTP routes for the child app, simulator, demo controls and WhatsApp webhook |
| Orchestrator | `app/coordinator.py`, `app/prompts.py` | Reads the child's message and returns a reply plus structured actions |
| Health / care engine | `app/care.py` | Creates care tasks, carries out due slots, follows up, links the parent's replies to tasks |
| Parent pipeline | `app/agents.py` | Every parent message: Safety, signals, Insight, Legacy, Companion reply; alerts; morning brief |
| Legacy | `app/book.py` | Family-book chapters, questions and bilingual pages |
| Scheduler | `app/scheduler.py` | Background tick every 20 seconds that runs due care tasks and follow-ups |
| Clock and time-lapse | `app/clock.py`, `app/timelapse.py` | Virtual clock; in demo mode jumps to the next scheduled event |
| Channel | `app/channel.py`, `app/whatsapp.py` | Stores every message and sends it through the simulator or the WhatsApp Cloud API |
| LLM client | `app/llm.py`, `app/mock_ai.py` | OpenAI-compatible client; rule-based mock when no key is set or a call fails |
| Front end | `static/app.html`, `static/simulator.html`, `static/demo.html` | Child app, parent simulator, side-by-side demo view (they poll `/api/state` and `/sim/messages`) |

## Flow 1: a child's request becomes care in the parent's day

```mermaid
sequenceDiagram
    participant C as Child app
    participant API as FastAPI
    participant O as Orchestrator (LLM)
    participant H as Health (care.py)
    participant W as WhatsApp / simulator
    participant P as Parent

    C->>API: POST /api/chat "Remind Mum to take her medicine at 2pm"
    API->>O: message + what we know about the parent
    O-->>API: reply + action create_task (medication, daily 14:00)
    API-->>C: plan card with both time zones
    C->>API: POST /api/tasks/{id}/confirm
    API->>H: activate task
    Note over H: Scheduler (or demo time-lapse) reaches 14:00 HK
    H->>W: Cantonese reminder with buttons
    W->>P: 💊 reminder
    P->>W: taps "已食藥 ✅"
    W->>H: POST /sim/button (or /webhook)
    H-->>C: Care log: Taken at 14:00 HK
    Note over H: No reply after 30 min: second reminder.<br/>No reply after 30 more: marked missed and the child is told.
```

Messages the child writes while the parent is asleep are held until `MORNING_DELIVERY` (default 09:15 HK). Proactive messages are only sent between `WAKE_START` and `WAKE_END`.

## Flow 2: a parent's message is understood and checked

```mermaid
sequenceDiagram
    participant P as Parent
    participant A as agents.py
    participant L as LLM
    participant DB as SQLite
    participant C as Child app

    P->>A: "膝頭仲係好痛，成日都唔想出街"
    A->>L: analyse (signals, facts, scam, emergency, story)
    L-->>A: signals: pain = knee, mobility = stayed home
    A->>A: Safety: scam / emergency check
    A->>DB: save signals and new facts
    A->>A: Insight rule: same problem 3 times in 7 days?
    A-->>C: alert "Knee pain mentioned 3 times this week" with the parent's own words
    A->>A: link the reply to the open check-in (care.py)
    A->>L: Companion writes a Cantonese reply
    A->>A: Safety guard adds 18222 / 999 wording if needed
    A->>P: caring reply
```

The Safety guard is deterministic code: if a message looks like a scam or emergency, the reply always contains the 18222 anti-scam hotline or 999 wording, whatever the model wrote.

## Flow 3: a family-book question becomes a page

```mermaid
sequenceDiagram
    participant C as Child app
    participant O as Orchestrator
    participant B as Legacy (book.py)
    participant P as Parent
    participant L as LLM

    C->>O: "Ask Mum how she met Grandpa"
    O->>B: add question to chapter "How I met Grandpa"
    B->>P: 婆婆，嘉欣好想知道：你同公公係點識㗎？
    P->>B: tells the story
    B->>L: write a page (title, Cantonese text, English translation, quote)
    L-->>B: page
    B-->>C: 📖 "New page in Mum's family book"
```

Stories the parent shares on her own, without being asked, are also detected and saved as pages. Everyday care replies are not; they go to the Care log.

## Database (SQLite, `data/aigrandkid.db`)

| Table | Stores |
|---|---|
| `parents` | The family: parent name and nickname, WhatsApp id, child's name, city and time zone |
| `messages` | The parent's WhatsApp conversation (both directions) |
| `chat` | The child's chat with the AI agent, including cards (plans, alerts, book pages, brief) |
| `care_tasks` | Care plans the child asked for: kind, schedule, details, status |
| `care_runs` | Each time a task was carried out: slot, status (sent, taken, answered, missed…), outcome |
| `signals` | Health and mood signals extracted from the parent's messages, with her words as evidence |
| `facts` | Long-term facts about the parent, used to personalise replies |
| `alerts` | Alerts to the child: kind, level, evidence, suggested actions, status |
| `digests` | Morning briefs |
| `book_chapters` | Family-book chapters (template + chapters the family adds) |
| `book_questions` | Questions per chapter: from the template, the child, or follow-ups |
| `book_entries` | Family-book pages: Cantonese story, English translation, quote, original answer |
| `events` | Activity log of the agent's parts (agent panel and "What the AI did") |
| `processed` | WhatsApp message ids already handled (prevents duplicates) |
| `kv` | Small key-value settings (e.g. busy flags, last brief date) |

## Time and the demo clock

All times are stored in UTC and shown in both the parent's and the child's time zone. `app/clock.py` holds a single offset applied to "now" everywhere. With `DEMO_INSTANT=true`, confirming a plan or pressing **⏭ Next event** moves the clock to the next scheduled moment (`app/timelapse.py`), so several days can be shown in a few minutes. With `DEMO_INSTANT=false`, the scheduler runs everything in real time and sends the morning brief at `BRIEF_TIME` in the child's time zone.
