# Telegram-only deployment (no website frontend, no laptop left running)

This project is a **Telegram bot only**. It does not contain a customer-facing website, dashboard, or web frontend. The only HTTP endpoints are backend endpoints used for Telegram webhook delivery and a health check.

## Why a cloud backend is still needed

Telegram does not run your Python code. BotFather creates the bot identity and token; it does not host the application. Telegram sends each incoming message to your bot's webhook URL. Therefore, a computer/server must run the Python backend continuously. Since you do not want to leave your laptop on, deploy this backend to a cloud service.

The public HTTPS domain below is only the address Telegram calls at `/telegram/webhook`. Users will chat with the bot inside Telegram; they do not need to open a website.

## Recommended setup: Railway + persistent Volume

The project already includes `railway.json`, `start.sh`, and a Dockerfile. Deploy it as one Railway service from GitHub.

### 1. Push the project to GitHub

1. Extract the project ZIP.
2. Create a GitHub repository, for example `hostel-helpdesk-telegram-bot`.
3. Push the complete project folder. Do not upload `.env`, database files, backups, or uploaded user attachments.
4. Make sure `railway.json`, `start.sh`, `requirements.txt`, and `app/` are at the repository root as shown in the README.

### 2. Create the Railway service

1. Sign in to Railway.
2. Create a new project and choose **Deploy from GitHub Repo**.
3. Select the repository.
4. Railway builds the Python dependencies from `requirements.txt` and starts the application using `sh start.sh` from `railway.json`.
5. Wait for the first deployment. If it fails, open the deployment build/deploy logs before continuing.

This is a backend service, not a website. Do not create a frontend/static-site service.

### 3. Attach persistent storage (required)

The application stores complaint tickets in SQLite and copies uploaded evidence to disk. These files must survive restarts and redeployments.

1. Open the Railway project canvas.
2. Add a **Volume** and attach it to the bot service.
3. Set its mount path to:

```text
/app/persist
```

Railway mounts the volume at runtime. The app must use paths inside this mount for persistent data.

### 4. Add environment variables

In the bot service's **Variables** panel, add:

```env
BOT_TOKEN=PASTE_TOKEN_FROM_BOTFATHER
WEBHOOK_SECRET=PASTE_A_RANDOM_SECRET_AT_LEAST_16_CHARACTERS
ADMIN_KEY=PASTE_A_DIFFERENT_RANDOM_SECRET_AT_LEAST_16_CHARACTERS
ADMIN_CHAT_ID=
TEAM_CHAT_IDS=
DB_PATH=/app/persist/hostel_bot.sqlite3
UPLOAD_DIR=/app/persist/uploads
MAX_UPLOAD_BYTES=20971520
CONVERSATION_GAP_MINUTES=30
UPDATE_PROCESSING_TTL_SECONDS=300
```

Generate secrets on your own computer with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use a different generated value for `WEBHOOK_SECRET` and `ADMIN_KEY`. Do not put these secrets in GitHub, screenshots, or a public chat.

For now, leave `ADMIN_CHAT_ID` and `TEAM_CHAT_IDS` blank. You can fill them after the service is live and you have obtained the chat IDs by messaging `/id` to the bot.

### 5. Generate the backend's HTTPS domain

1. Open the bot service's **Settings**.
2. Find **Networking → Public Networking**.
3. Choose **Generate Domain**.
4. Copy the generated `https://....up.railway.app` address.

This domain is required because Telegram webhooks need a public HTTPS endpoint. It does not create a website frontend. Your bot's user interface remains Telegram.

Add one more Railway variable:

```env
PUBLIC_BASE_URL=https://YOUR-GENERATED-DOMAIN.up.railway.app
```

Do not add `/telegram/webhook` to this variable; the registration script adds that path automatically.

Save variables and redeploy/restart the service.

### 6. Register the Telegram webhook once

You only need to do this once after the Railway service is deployed (repeat it only if the domain or webhook secret changes).

On your computer:

1. Copy `.env.example` to `.env` in your local project copy.
2. Put the same `BOT_TOKEN`, `WEBHOOK_SECRET`, and `PUBLIC_BASE_URL` values in local `.env` that you set in Railway.
3. Install the project requirements if needed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Register and verify the webhook:

```bash
python scripts/set_webhook.py
python scripts/check_webhook.py
```

After the command succeeds, Telegram will deliver new bot updates to Railway. Your laptop is not part of the running bot after this setup step and can be shut down.

### 7. Set the administrator and team IDs

1. Open the bot in Telegram and press **Start**.
2. Send `/id` and copy the numeric ID returned.
3. Add that ID as `ADMIN_CHAT_ID` in Railway Variables.
4. Repeat `/id` for each team member and enter the three IDs as comma-separated values in `TEAM_CHAT_IDS`.
5. Save/redeploy the Railway service.
6. From the admin Telegram account, send `/help` and verify that admin commands are displayed.

The administrator must start the bot privately before Telegram will allow the bot to message them.

### 8. Verify it works without your laptop

1. Turn off your laptop or disconnect it from the internet.
2. Use another phone/computer to open the Telegram bot.
3. Send `/start` and register a test complaint.
4. Confirm the admin Telegram account receives the ticket notification.
5. Send an attachment and confirm the bot stores it and forwards it to the admin.
6. Use `/reply TICKET_ID Your update` from the administrator account.
7. Check the status as the original user.
8. Restart/redeploy the Railway service and verify the ticket still exists. If it disappears, the Volume or `DB_PATH`/`UPLOAD_DIR` paths are configured incorrectly.

You can also check backend health at `https://YOUR-GENERATED-DOMAIN.up.railway.app/health`. This returns a small JSON status response; it is not a website interface.

## Costs and free-tier warning

Railway's current pricing documentation lists a Free plan with $1 of monthly usage credit and a Hobby plan with a $5/month base subscription, with metered resource charges. A bot that must stay available around the clock should not rely on an unverified free-credit allowance. Check the live billing page before deployment and set a usage alert/limit if available. Taxes and currency conversion may apply. Railway's persistent-volume documentation lists 0.5 GB on Free and Trial plans and 5 GB on Hobby.

References (check current terms before creating the service):

- Railway pricing: https://docs.railway.com/pricing/plans
- Railway persistent Volumes: https://docs.railway.com/volumes
- Railway public domains: https://docs.railway.com/networking/domains/working-with-domains
- Telegram Bot API webhook: https://core.telegram.org/bots/api#setwebhook

## Important limitations

- No website frontend is included or required.
- Railway is hosting the Python backend only. Telegram is the interface users interact with.
- If the cloud service is suspended, deleted, or runs out of billing credit, the bot stops responding.
- Do not run multiple replicas against the same SQLite database file; this project is configured for one bot service instance.
- Download and keep backups of the SQLite database and uploads folder.
- The project's synthetic test cases are not real conversations and do not satisfy the course's 25 genuine external-user conversation requirement.
