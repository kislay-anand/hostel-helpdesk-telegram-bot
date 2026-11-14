"""Long-polling runner for LOCAL testing (no public URL needed).

    python -m app.polling

It deletes any registered webhook, then fetches updates with getUpdates and feeds them to the same
handler the webhook uses. Do not run it at the same time as the deployed webhook for the same bot token.
"""
from __future__ import annotations

import asyncio
import logging

from .config import get_settings
from .main import App, BOT_COMMANDS

log = logging.getLogger("helpbot.polling")


async def main() -> None:
    core = App(get_settings())
    await core.tg.call("deleteWebhook", {"drop_pending_updates": False})
    await core.tg.call("setMyCommands", {"commands": BOT_COMMANDS})
    me = await core.tg.call("getMe")
    log.info("polling as @%s — send /start to it. Ctrl+C to stop.", me["username"])
    offset = None
    try:
        while True:
            try:
                updates = await core.tg.call("getUpdates", {"timeout": 25, "offset": offset,
                                                            "allowed_updates": ["message", "callback_query", "my_chat_member"]})
            except Exception as e:  # noqa: BLE001
                log.warning("getUpdates failed (%s); retrying in 3s", e)
                await asyncio.sleep(3)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                await core.process(u)
    finally:
        await core.tg.close()


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
