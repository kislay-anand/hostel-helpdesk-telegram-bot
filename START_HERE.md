# Start here — Telegram-only Hostel Helpdesk Bot

This is a **Telegram bot project**, not a website project. Users interact with it only in Telegram. A cloud-hosted Python backend receives Telegram webhook updates so your laptop does not need to stay switched on.

## Do this in order

1. Extract this folder and open it in VS Code.
2. Read `README.md` and `TEAM_TASKS.md`.
3. Create the bot with `@BotFather` and keep its token private.
4. Push the project to GitHub. Do not commit `.env`, SQLite files, backups, or user-uploaded attachments.
5. Follow **`TELEGRAM_ONLY_HOSTING.md`** to deploy the backend on Railway, attach persistent storage, add secrets, and generate a public HTTPS backend domain.
6. Register the Telegram webhook once using `scripts/set_webhook.py` from your computer. After successful registration, Telegram sends updates to the cloud service and your laptop can be shut down.
7. Obtain the admin and three team chat IDs using `/id`; set `ADMIN_CHAT_ID` and `TEAM_CHAT_IDS` in Railway Variables and redeploy.
8. Test complaint creation, ticket status, attachment forwarding, admin reply, admin close and analytics from Telegram.
9. Run `pytest -q` and `python scripts/evaluate_methods.py` locally before submitting.

## No website frontend

The only public URL is a backend endpoint used by Telegram:

```text
https://YOUR-RAILWAY-DOMAIN/telegram/webhook
```

The `/health` route returns JSON for the host's health check. There is no customer-facing website to design, build, or maintain.

## Submission warning

The AI comparison uses synthetic labelled examples. It does not count toward the course requirement for 25 genuine external-user conversations. The project records conversation statistics, but the team must collect and report actual genuine usage honestly.
