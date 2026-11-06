"""Postavi Telegram webhook na Cloudflare Worker (jednom, posle `wrangler deploy`).

Upotreba:
    TELEGRAM_BOT_TOKEN=... TELEGRAM_SECRET_TOKEN=... \
        uv run python scripts/set_webhook.py https://praksa-radar.<nalog>.workers.dev/telegram

TELEGRAM_SECRET_TOKEN je isti string koji si dao Worker-u
(`wrangler secret put TELEGRAM_SECRET_TOKEN`); Telegram ga šalje u zaglavlju
X-Telegram-Bot-Api-Secret-Token, a Worker odbija sve zahteve bez njega.
"""

import os
import sys

import httpx


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].startswith("https://"):
        print(__doc__)
        return 2
    token, secret = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_SECRET_TOKEN"]
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": argv[0],
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        },
        timeout=20,
    )
    print(resp.status_code, resp.text)
    info = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=20).json()
    print({k: info["result"].get(k) for k in ("url", "pending_update_count", "last_error_message")})
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
