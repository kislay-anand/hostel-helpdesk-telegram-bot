> **Telegram-only project:** there is no website frontend. Users interact with the bot in Telegram. The Python/FastAPI service is deployed in the cloud only to receive Telegram's HTTPS webhook updates, so your laptop can be switched off after setup. Follow [`TELEGRAM_ONLY_HOSTING.md`](TELEGRAM_ONLY_HOSTING.md).

# Hostel Helpdesk Telegram Bot

A deployable Python/FastAPI Telegram webhook bot for a hostel maintenance helpdesk. It supports complaint registration, ticket tracking, SQLite persistence, photo/document evidence, human escalation, admin replies, session tracking, usage statistics, and an explainable rule-based FAQ classifier.

> **Project-prototype notice:** This is a student project starter, not a certified production helpdesk. Configure real office information only after it is confirmed by the hostel administration. Do not collect passwords, payment details, government ID documents or unrelated sensitive information.

## Contents

- `app/main.py` — FastAPI health endpoint, Telegram webhook, webhook authentication and admin analytics endpoint.
- `app/telegram_api.py` — Telegram Bot API wrapper, text messages, file download and forwarding media to the administrator.
- `app/bot.py` — user conversation flow, complaint/ticket logic and human handover commands.
- `app/db.py` — SQLite schema, migrations, sessions, conversations, tickets, messages, attachments and analytics.
- `app/ai_methods.py` — keyword baseline, regex rule engine, explanation trace and synthetic evaluation data.
- `data/faqs.json` — editable FAQ answer base.
- `scripts/set_webhook.py` / `scripts/check_webhook.py` — webhook registration and health inspection.
- `scripts/evaluate_methods.py` — run the AI-method comparison.
- `scripts/backup_db.py` — consistent SQLite database backup plus attachment archive.
- `tests/` — automated tests.
- `railway.json`, `start.sh` — Railway-compatible deployment configuration.
- `DEPLOYMENT.md` — hosting steps.
- `TEAM_TASKS.md` — three-person ownership and integration guide.

## Features

1. Telegram webhook protected by `X-Telegram-Bot-Api-Secret-Token`.
2. Conversation sessions stored per Telegram private chat.
3. A conversation counter that starts a new session after 30 minutes without a message.
4. Ticket ID generation and ownership checks: users can only see tickets belonging to their Telegram chat ID.
5. Ticket status lifecycle: `open`, `human_requested`, `in_progress`, `closed`.
6. Photo/document download with a 20 MB default cap and random server-side filenames.
7. Attachment forwarding to the administrator's Telegram account (not just a local server path).
8. Human handover and admin commands.
9. Incoming/outgoing message records and usage statistics.
10. Idempotent webhook update claims and ticket creation keyed to the source Telegram update ID.
11. Two intent methods: exact-keyword baseline and regex-based rule detector with a matched-rule explanation trace.
12. Health endpoint at `/health` and protected analytics endpoint at `/admin/stats`.

## Requirements

- Python 3.10 or newer (Python 3.12 recommended).
- A Telegram account to create the bot with `@BotFather`.
- A public HTTPS URL for webhook delivery. For a short demo, use ngrok. For hosted use, deploy to a service such as Railway and attach persistent storage.
- Internet connectivity from the bot server to `api.telegram.org`.

## 1. Create a Telegram bot

1. Open Telegram and search for the official `@BotFather` account.
2. Send `/newbot` and follow its prompts.
3. Choose a display name and a unique username ending in `bot`.
4. Copy the bot token. Treat the token like a password.
5. Open the bot's private chat and press **Start** after the backend is deployed.

## 2. Configure locally

### Windows PowerShell

```powershell
cd hostel-helpdesk-telegram-complete
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

### Linux/macOS

```bash
cd hostel-helpdesk-telegram-complete
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Generate two independent secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the generated values into `WEBHOOK_SECRET` and `ADMIN_KEY` in `.env`. Set `BOT_TOKEN` to the token from BotFather. Leave `ADMIN_CHAT_ID` blank until you retrieve the administrator's chat ID.

Never commit `.env`, bot tokens, admin keys, the SQLite database, or uploaded user files.

## 3. Start locally and verify the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/health`. Expected response:

```json
{"status":"ok"}
```

The bot has no polling loop. It receives messages only after a public HTTPS webhook is registered.

## 4. Expose your local server with ngrok (demo option)

1. Install ngrok from its official download page and create an account if required.
2. Add the authentication token shown in your ngrok dashboard:

```bash
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
```

3. Start the tunnel in a second terminal:

```bash
ngrok http 8000
```

4. Copy the HTTPS forwarding URL displayed by ngrok, e.g. `https://your-assigned-domain.ngrok-free.app`.
5. Put that URL in `PUBLIC_BASE_URL` in `.env` (no trailing slash).
6. Keep both the FastAPI terminal and ngrok terminal running. Your laptop must remain awake and online.

## 5. Register and verify the Telegram webhook

In another terminal, with the virtual environment active:

```bash
python scripts/set_webhook.py
python scripts/check_webhook.py
```

The reported webhook URL should end with `/telegram/webhook`, and Telegram's `last_error_message` should be empty. If you change the domain or `WEBHOOK_SECRET`, register the webhook again.

Telegram webhook delivery and long polling (`getUpdates`) are mutually exclusive. This project uses webhooks only.

## 6. Configure the administrator and team IDs

1. Start the bot and send `/id` in a private chat. The bot returns the Telegram chat ID.
2. Put the administrator's ID in `ADMIN_CHAT_ID` in `.env`.
3. Get the other two project members' IDs as well.
4. Put all three team chat IDs, comma-separated, in `TEAM_CHAT_IDS`.
5. Restart the FastAPI process.

The admin commands are accepted only from the configured `ADMIN_CHAT_ID`:

- `/help` — show admin commands.
- `/stats` — show usage statistics in Telegram.
- `/ticket HST-YYMMDD-XXXXXX` — inspect a ticket.
- `/reply HST-YYMMDD-XXXXXX Your message` — reply to the ticket owner.
- `/close HST-YYMMDD-XXXXXX` — close a ticket.

## 7. User conversation flow

1. User sends `/start` and sees the data-use notice.
2. User types `I AGREE` to allow ticket/support data to be stored, or `/decline` to stop without creating service records.
3. After consent, the user selects **New complaint**.
4. User selects Electrical, Plumbing, Cleaning, Wi-Fi, Furniture or Other.
5. User enters a block/room/location.
6. User describes the issue (minimum eight characters).
7. The system creates an `HST-YYMMDD-XXXXXX` ticket and asks for one photo/document or `skip`.
8. The user can select **Check status** and enter their ticket ID. The database checks both ticket ID and Telegram chat ID.
9. **Talk to human** marks an active ticket for handover, or creates a General Support ticket when needed. Follow-up text and media are relayed to the configured admin.
10. User can send `/delete_my_data` to remove their consent, messages, conversation records, tickets and saved attachments from this bot's database/storage.

The administrator receives the uploaded evidence in Telegram when forwarding succeeds. The server retains a copy under `uploads/` and records its metadata in SQLite.

## 8. Tests and AI comparison

Run automated tests:

```bash
pytest -q
```

Run the AI comparison:

```bash
python scripts/evaluate_methods.py
```

The test phrases in `app/ai_methods.py` are synthetic labelled examples. The output compares keyword matching and regex rules only on that set. It is **not** a real-user success rate and must not be described as real user evidence.

The regex method is explainable: it returns the detected intent, the exact regex pattern and the matched phrase. Unknown text returns `unknown` and a safe fallback.

## 9. Analytics and conversation counts

The protected endpoint is:

```bash
curl -H "X-Admin-Key: YOUR_ADMIN_KEY" https://YOUR_PUBLIC_HOST/admin/stats
```

PowerShell:

```powershell
$headers = @{ "X-Admin-Key" = "YOUR_ADMIN_KEY" }
Invoke-RestMethod -Uri "https://YOUR_PUBLIC_HOST/admin/stats" -Headers $headers
```

Important fields include:

- `external_users` — distinct Telegram chat IDs that are not configured team/admin accounts.
- `external_conversations` — number of conversation windows from external chat IDs that agreed to use the bot.
- `incoming_messages` — inbound external messages.
- `tickets_total`, `tickets_open_or_escalated`, `human_handover_tickets`.
- `attachments_saved` and `tickets_by_category`.

**Conversation definition:** A new conversation row is counted after more than 30 minutes of inactivity since the prior message from the same Telegram private chat. This is an operational definition for analytics, not proof that a person is a genuine hostel user. The course requirement still calls for actual conversations with people outside the project team. Do not create fake users, fabricate logs, count synthetic test cases, or alter these numbers.

Configure all three teammate IDs in `TEAM_CHAT_IDS` before internal testing; otherwise their usage can appear in external counts. The admin ID is excluded automatically.

## 10. Backup

With the app stopped or in a low-traffic window, run:

```bash
python scripts/backup_db.py
```

This creates a timestamped SQLite backup and an archive of attachments in a `backups/` directory beside the configured database file. Keep backups private and outside GitHub.

## 11. GitHub submission

```bash
git init
git add .
git status
git commit -m "chore: initialize hostel helpdesk telegram bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/hostel-helpdesk-telegram-bot.git
git push -u origin main
```

Use separate feature branches/commits for each member. Check `git status` and `git check-ignore .env` before pushing. Do not upload the database or `uploads/` contents. Add screenshots with user IDs and private information hidden.

## Privacy, security and limitations

- The bot shows a data-use notice before it accepts service requests. Users must type `I AGREE`; `/decline` stops use, and `/delete_my_data` removes their stored consent, messages, conversations, tickets and attachment files. The `/id` command is available before consent only to retrieve the chat ID needed for initial administrator configuration.
- After consent, the bot stores Telegram chat IDs, optional usernames, message text, ticket details and uploaded attachments. Explain this to users before they submit anything.
- Keep the admin account secure and restrict access to the host and its persistent volume.
- The admin stats endpoint uses an `X-Admin-Key`; keep this key secret and rotate it if exposed.
- The webhook uses Telegram's secret-token header; do not remove validation.
- Uploaded filenames are random server-side names. The bot does not serve uploaded files publicly.
- The Telegram standard cloud Bot API download limit used here is 20 MB per file. See Telegram's current Bot API documentation before changing this limit.
- The system is a one-day student prototype: it does not provide service-level guarantees, advanced spam prevention, multi-admin roles, or formal institutional integration.
- The bot only works while its hosting service is running and the webhook URL remains valid.

For the hosting steps and known storage caveats, read `DEPLOYMENT.md`.
