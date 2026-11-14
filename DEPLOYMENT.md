# Hosting and deployment guide

This project uses Telegram **webhooks**, so the backend must have a public HTTPS address. The recommended paths are:

- **For a same-day demonstration:** run FastAPI on your laptop and expose port `8000` using ngrok. This costs nothing for a basic demo, but your laptop and both processes must stay online.
- **For an always-reachable hosted demo:** deploy to Railway and attach a persistent Volume. The database and uploaded files must be written inside that Volume. Hosting plans and credits can change; check the live billing page before creating resources.

Do not deploy this SQLite version to a free host that has only an ephemeral filesystem. A restart, sleep or redeploy can erase the database and uploaded files.

## A. Deploy to Railway (recommended hosted path)

### Before starting

- Push the complete project to a GitHub repository.
- Create the Telegram bot with `@BotFather` and keep its bot token private.
- Have these values ready: `BOT_TOKEN`, a random `WEBHOOK_SECRET`, a random `ADMIN_KEY`, administrator chat ID and all three teammate chat IDs.
- Do not put secrets into GitHub or into `railway.json`.

### 1. Create a Railway project

1. Sign in to Railway.
2. Choose **New Project** → **Deploy from GitHub repo**.
3. Connect the GitHub account if requested.
4. Select your `hostel-helpdesk-telegram-bot` repository.
5. Railway detects `railway.json`; the project build command installs `requirements.txt` and the start command runs `sh start.sh`.

The start script binds to `0.0.0.0` and uses Railway's `PORT` environment variable.

### 2. Add a persistent Volume before collecting data

1. Open the Railway project canvas and select the bot service.
2. Add a **Volume** to that service.
3. Set its mount path to:

```text
/app/persist
```

4. Wait for the service to redeploy with the Volume mounted.

The application must store both the SQLite file and uploaded evidence on this persistent mount. Set the following variables in the service's **Variables** panel:

```text
DB_PATH=/app/persist/hostel_bot.sqlite3
UPLOAD_DIR=/app/persist/uploads
```

The application creates the database parent directory and upload directory on startup. Do not point these variables back to the source-code directory when using a Volume.

### 3. Set Railway variables

Add these in the service's Variables panel. Use your own values; do not copy the placeholders literally.

```text
BOT_TOKEN=<token from BotFather>
WEBHOOK_SECRET=<random secret, 16+ characters>
ADMIN_KEY=<different random secret, 16+ characters>
ADMIN_CHAT_ID=<administrator Telegram numeric chat ID>
TEAM_CHAT_IDS=<member1_id,member2_id,member3_id>
DB_PATH=/app/persist/hostel_bot.sqlite3
UPLOAD_DIR=/app/persist/uploads
MAX_UPLOAD_BYTES=20971520
CONVERSATION_GAP_MINUTES=30
UPDATE_PROCESSING_TTL_SECONDS=300
```

Do not add `PUBLIC_BASE_URL` until the public service domain exists.

### 4. Generate a public HTTPS domain

1. Open the bot service's **Settings** or **Networking** section.
2. Choose **Generate Domain**.
3. Copy the generated `https://...` domain.
4. Add a Railway variable:

```text
PUBLIC_BASE_URL=https://YOUR-GENERATED-DOMAIN
```

Do not add `/telegram/webhook` to `PUBLIC_BASE_URL`; the setup script appends that path.

### 5. Deploy and verify health

Wait for deployment to finish. Open these URLs in a browser (replace the host):

```text
https://YOUR-GENERATED-DOMAIN/
https://YOUR-GENERATED-DOMAIN/health
```

Expected health response:

```json
{"status":"ok"}
```

If deployment fails, open Railway's deployment logs. Check that the build installed all requirements, `start.sh` exists and is executable, and the service is listening on the injected `PORT`.

### 6. Register the Telegram webhook from your computer

The registration script can be run from your own computer. It needs a local `.env` file containing the same bot token, secret and public URL as the Railway service.

1. On your local copy, copy `.env.example` to `.env`.
2. Set `BOT_TOKEN`, `WEBHOOK_SECRET` and `PUBLIC_BASE_URL` to the exact same values as Railway.
3. Activate your virtual environment and install requirements if you have not already.
4. Run:

```bash
python scripts/set_webhook.py
python scripts/check_webhook.py
```

The webhook URL should be:

```text
https://YOUR-GENERATED-DOMAIN/telegram/webhook
```

Check that `last_error_message` is empty. If the URL or webhook secret changes, run `set_webhook.py` again.

### 7. Set the admin account

1. Open your bot on Telegram and send `/start`, then `/id`.
2. Copy the numeric chat ID into Railway's `ADMIN_CHAT_ID` variable.
3. Get all three member IDs and place them comma-separated in `TEAM_CHAT_IDS`.
4. Save the variables and redeploy/restart the Railway service.
5. Send `/help` to the bot from the admin account. The bot should show the admin commands.

If the bot cannot send a message to the administrator, first ensure the administrator has started the bot privately and `ADMIN_CHAT_ID` is correct.

### 8. Run the end-to-end hosted test

1. Send `/start` from a non-team Telegram account.
2. Register a complaint through category, room, and description.
3. Attach a photo or PDF/document.
4. Confirm the ticket appears in admin chat and the media is delivered to admin chat.
5. Use **Check status** from the original user account.
6. Try the same ticket ID from a different account; it should not disclose the ticket.
7. From the admin account run `/reply TICKET_ID Your update`.
8. Close it using `/close TICKET_ID`.
9. Open `/admin/stats` with the `X-Admin-Key` header and confirm counts.
10. Restart/redeploy the service and ensure existing ticket data is still present. If it is missing, stop: the Volume is not mounted or DB_PATH points outside it.

### 9. Cost and storage warning

Railway's pricing and free allowances are subject to change. At the time this guide was prepared, Railway describes a 30-day trial with a one-time $5 credit, then a Free plan with $1 monthly credit; the listed Free plan volume allowance is 0.5 GB. Trial credits and trial-created stateful data also have expiration terms. Read Railway's current pricing and trial/volume policy before relying on it for long-term storage. A free credit allowance is not a promise that continuous hosting will cost $0 or run indefinitely.

The database plus attachments share the configured persistent Volume. Monitor its capacity. Back up the data before changing plans or deleting the Railway project.

## B. Free same-day demo using your laptop and ngrok

This is the simplest option if you do not want to configure cloud storage or a paid plan.

1. Follow the local installation steps in `README.md`.
2. Run FastAPI:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

3. In another terminal start ngrok:

```bash
ngrok http 8000
```

4. Put the ngrok HTTPS URL into `PUBLIC_BASE_URL` in `.env`.
5. Run:

```bash
python scripts/set_webhook.py
python scripts/check_webhook.py
```

6. Keep both terminals, the laptop and internet connection active for the evaluation.

Because the app and SQLite database are on your computer, the database persists across application restarts as long as you do not delete the project data. If the ngrok domain changes, update `PUBLIC_BASE_URL` and re-register the webhook.

## C. Why a free Render web service is not the right fit for this SQLite build

Render Free web services have an ephemeral filesystem. Render's documentation states local files (including SQLite databases and uploaded images) are lost on redeploy, restart or spin-down. Free web services cannot attach persistent disks. You would need to redesign persistence around a managed database plus separate durable object storage or use a paid persistent disk setup. Do not deploy the current `DB_PATH`/`UPLOAD_DIR` local-file configuration to Render Free and then assume tickets or evidence will survive.

## D. Change or remove a webhook

To inspect it:

```bash
python scripts/check_webhook.py
```

To set a new URL, update `PUBLIC_BASE_URL` and run `scripts/set_webhook.py` again. If you intentionally want to stop webhook delivery, use Telegram's `deleteWebhook` Bot API method from a controlled admin environment; do not leave an old production URL registered accidentally.

## E. Backup and recovery

Run from the project directory with the correct `DB_PATH` and `UPLOAD_DIR` environment values:

```bash
python scripts/backup_db.py
```

Keep downloaded backups private. The script creates a SQLite backup and an archive of the uploads folder under `backups/`. Download backups off the hosting provider before deleting or changing the attached Volume.

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot does not respond | Service is live; webhook URL; `getWebhookInfo`; server logs; bot token. |
| `403 Invalid webhook secret` | Railway `WEBHOOK_SECRET` must exactly match the secret passed to `setWebhook`. |
| Telegram says webhook URL invalid | Use an HTTPS domain and the exact `/telegram/webhook` path. |
| Admin does not receive alerts | Admin has started the bot privately; correct numeric `ADMIN_CHAT_ID`; service redeployed after variables changed. |
| Photo stored but not forwarded | Check logs, file size, supported format and admin chat accessibility. |
| Tickets disappear after redeploy | Confirm Volume mount `/app/persist` and the exact absolute `DB_PATH`/`UPLOAD_DIR` variables. |
| `database is locked` | Avoid running multiple app replicas against the same SQLite file; keep a single service instance for this project. |
| Admin stats returns 403 | Send the exact `ADMIN_KEY` as `X-Admin-Key`; do not use the BotFather token here. |

## Hosting references

- Railway FastAPI deployment guide: https://railway.com/guides/fastapi
- Railway Volumes: https://docs.railway.com/volumes
- Railway Pricing: https://railway.com/pricing
- Railway Free Trial: https://docs.railway.com/pricing/free-trial
- Railway Healthchecks: https://docs.railway.com/deployments/healthchecks
- Render Free service limitations: https://render.com/docs/free
