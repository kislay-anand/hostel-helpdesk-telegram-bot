# Three-member implementation split

All members should read this document and the README. The split balances technical implementation: integration, core workflow, and AI/evaluation/testing. Each member must be able to explain the end-to-end design in the viva.

## Member 1 — Telegram API and deployment

Owns:
- `app/telegram_api.py`
- `app/main.py`
- `app/config.py`
- `scripts/set_webhook.py`
- `scripts/check_webhook.py`
- `start.sh`, `railway.json`

### `app/telegram_api.py`
Implement and verify:
- `call(method, payload)`: sends JSON POST requests to `https://api.telegram.org/bot<TOKEN>/<method>` using `httpx`; checks HTTP status and Telegram's `ok` field; raises a useful exception on an API failure.
- `send_message(chat_id, text, reply_markup)`: constructs the `sendMessage` payload and splits text longer than Telegram's message size limit.
- `get_file_info(file_id)`: calls `getFile` and returns the `file_path` and declared size.
- `download_telegram_file(file_id, destination)`: calls `getFile`, streams the file from `https://api.telegram.org/file/bot<TOKEN>/<file_path>`, enforces the byte cap, and deletes partial files after failure.
- `send_media(chat_id, path, media_type, caption)`: uploads a stored file to the administrator with `sendPhoto` for supported image types or `sendDocument` otherwise.
- Handle missing token, timeout, HTTP failure, non-JSON/Telegram error responses, missing files and oversized downloads. Never log or print the bot token.

### `app/main.py`
- `GET /` gives service information.
- `GET /health` returns HTTP 200 while the app is available.
- `POST /telegram/webhook` validates `X-Telegram-Bot-Api-Secret-Token`, validates the update payload, atomically claims the `update_id`, calls the bot handler, and marks the update complete/failed.
- Duplicate update IDs must not repeat ticket creation.
- `GET /admin/stats` validates `X-Admin-Key` before returning analytics.
- Initialise SQLite and the upload directory on startup.

### `app/config.py`
- Read `BOT_TOKEN`, `WEBHOOK_SECRET`, `PUBLIC_BASE_URL`, `ADMIN_CHAT_ID`, `ADMIN_KEY`, `TEAM_CHAT_IDS`, `DB_PATH`, `UPLOAD_DIR` and size/session parameters.
- Parse numeric IDs safely and support absolute filesystem paths for cloud volumes.
- Keep secrets in environment variables; `.env` is local-only.

### Webhook deployment
- Create bot using `@BotFather` `/newbot`.
- Obtain chat IDs using `/id`.
- Run the app and expose it with ngrok, or deploy to Railway.
- Register the final public HTTPS address with `scripts/set_webhook.py`.
- Verify the URL and delivery error fields with `scripts/check_webhook.py`.

Acceptance criteria: Telegram can send `/start`; the server replies; invalid webhook secret is rejected; webhook info displays the right public URL; admin receives a complaint notification and attachment.

## Member 2 — conversation engine and database

Owns:
- `app/bot.py`
- `app/db.py`
- `data/faqs.json`
- `scripts/backup_db.py`

### `app/db.py`
Tables and responsibilities:
- `processed_updates`: idempotency state for Telegram update IDs.
- `conversations`: chat ID, username, start time, last activity and inbound-message count. A new conversation starts after the configured inactivity gap.
- `sessions`: one row per private Telegram chat with `state` and JSON `data`.
- `tickets`: ticket ID, owner chat ID, category, location, description, status, timestamp, source update ID and first human-escalation timestamp.
- `messages`: inbound/outbound text, chat ID, message direction, media type, timestamp and internal/team flag.
- `attachments`: ticket association, Telegram file ID, media type, server path, original name and size.

Required database functions include:
- `init_db()` with safe create/migration logic.
- `claim_update()`, `mark_update_processed()`, `mark_update_failed()`.
- `touch_conversation()` and `save_message()`.
- `has_consent()`, `record_consent()` and `delete_user_data()` for the privacy notice / withdrawal flow.
- `get_session()` and `set_session()`.
- `create_ticket()` idempotent on `source_update_id`.
- `get_ticket_for_user()` enforcing `ticket_id AND chat_id` ownership; `update_ticket_status()` records the first escalation time so lifetime handover statistics remain correct after an admin reply.
- `get_ticket()`, `get_latest_active_ticket_for_user()`, `update_ticket_status()`.
- `add_attachment()` and `get_stats()`.

Use parameterised SQL. Never construct SQL values by concatenating user text. Keep ticket access restricted to its owning Telegram chat ID except for admin commands.

### `app/bot.py`
Conversation states and required transitions:
- `idle` → `awaiting_category` → `awaiting_location` → `awaiting_description` → `awaiting_media` → `idle`.
- Status lookup: `idle` → `awaiting_ticket_id` → `idle`.
- Human support: create or reuse active ticket → `human_requested` → `human_mode`.

Implement the consent gate (`/start` → privacy notice → `I AGREE` or `/decline`), menu choices, validation, ticket creation, status check, safe fallback, FAQ response, photo/document save, admin notification, human handover and admin commands. Users can use `/delete_my_data` to remove their helpdesk records. Admin commands: `/help`, `/stats`, `/ticket`, `/reply`, `/close`. Only the configured admin can execute them.

Acceptance criteria: each user gets an independent session; tickets survive process restart; one user cannot access another user's ticket; webhook retry does not create a second ticket; attachment metadata and actual file exist; administrator receives the uploaded media; `/reply` and `/close` work.

## Member 3 — AI approach, evaluation and tests

Owns:
- `app/ai_methods.py`
- `tests/`
- `scripts/evaluate_methods.py`
- evaluation sections of `README.md` and report.

### `app/ai_methods.py`
- `keyword_baseline(text)`: count exact token overlap with category dictionaries; return the best category or `unknown`.
- `explain_intent(text)`: apply ordered regex rules; return `intent`, `matched_rule`, `matched_text`.
- `rule_based_intent(text)`: expose only the category label for evaluation.
- `TEST_CASES`: synthetic labelled phrases covering categories and unknown inputs.
- `evaluate_methods()`: compute correct/total and accuracy for both methods.

### Testing
- Run `pytest -q`.
- Test all category paraphrases and unknown fallback.
- Test database persistence, user separation, ticket ownership, ticket idempotency and conversation counting.
- Test `/health` and rejection of a bad webhook secret.
- Test the full Telegram workflow manually on two separate accounts.

### Evaluation and report
- Report the synthetic test set size and measured metrics honestly.
- Include a confusion matrix or category-level counts if you expand the test set.
- Explain rule traces, failure cases, privacy, fairness, data minimisation and known limits.
- Do not describe synthetic phrases as real users or claim 25 conversations until actual external use has occurred.

Acceptance criteria: tests pass; the comparison script runs; results can be reproduced; the method explanation is understandable in the viva.

## Shared integration protocol

1. Agree on function names before editing interfaces.
2. Each member commits only their owned files where practical.
3. Member 1 creates a feature branch for webhook and hosting work.
4. Member 2 creates a feature branch for conversation/database work.
5. Member 3 creates a feature branch for test/AI work.
6. Merge into `main` after local tests pass.
7. Run `pytest -q`, then perform the manual Telegram test flow from `README.md`.
8. Check that `.env`, SQLite databases, backups and uploaded user files are ignored by Git.
