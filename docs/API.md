# HTTP API

All routes are defined in `app/main.py`. Request bodies use form fields. `parent_id` defaults to `1` (the demo family).

## Pages

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Child's app (Chat, Care log, Family book, Services) |
| GET | `/demo` | Side-by-side demo view: parent's phone, agent panel, child's app, presenter controls |
| GET | `/sim` | Parent's WhatsApp-style simulator |

## Child's app

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Everything the child's app and demo view need: clock, care tasks and runs, alerts, chat, family book, agent activity, next event |
| POST | `/api/chat` | Send a chat message (`text`). The Orchestrator replies and may propose a care plan, deliver a message, ask a family-book question or explain what is not available yet |
| POST | `/api/chat/voice` | Send a voice message for the parent (`file`, optional `text`) |
| POST | `/api/tasks/{tid}/voice` | Record the child's own voice for a medicine reminder (`file`) |
| POST | `/api/tasks/{tid}/{action}` | Act on a care task. `action` is `confirm`, `cancel`, `pause`, `resume`, `delete` or `run` (carry it out now) |
| POST | `/api/alerts/{aid}/action` | Respond to an alert (`action`). `call` tells the parent the child will call; other actions mark it handled |

## Family book

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/book/chapters` | Add a chapter (`title`, `emoji`, optional `first_question`) |
| POST | `/api/book/chapters/{cid}/question` | Add a question to a chapter (`text`) |
| POST | `/api/book/questions/{qid}/ask` | Ask the parent this question now (the "Ask Mum" button) |
| POST | `/api/book/chapters/{cid}/ask` | Ask the parent the next unanswered question in this chapter |

## Demo controls

| Method | Path | Purpose |
|---|---|---|
| POST | `/demo/next` | Time-lapse: jump the clock to the next scheduled event and carry it out |
| POST | `/demo/brief` | Jump to the child's next morning and generate the morning brief |
| POST | `/demo/reset` | Start the demo again from empty |

## Parent's simulator

| Method | Path | Purpose |
|---|---|---|
| POST | `/sim/send` | The parent sends a message (`text`, `kind` = `text`, `audio` or `image`, optional `file`) |
| POST | `/sim/button` | The parent taps a reply button (`button_id`, `title`) |
| GET | `/sim/messages` | The parent's conversation (`after_id` for new messages only) |

## WhatsApp (production path)

| Method | Path | Purpose |
|---|---|---|
| GET | `/webhook` | Meta webhook verification (uses `WHATSAPP_VERIFY_TOKEN`) |
| POST | `/webhook` | Incoming WhatsApp messages and button replies from Meta's Cloud API |
| GET | `/api/whatsapp/status` | Whether WhatsApp is configured, the channel in use and the linked number |
