"""Jednokratni lokalni OAuth (gmail.readonly). Ispiše refresh token.

Upotreba: GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... uv run python scripts/gmail_auth.py
OAuth client mora biti tipa "Desktop"; redirect ide na lokalni port.
"""

import os
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}"


def main() -> None:
    cid, secret = os.environ["GMAIL_CLIENT_ID"], os.environ["GMAIL_CLIENT_SECRET"]
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = q.get("code", [""])[0]
            result["state"] = q.get("state", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Gotovo, mozes da zatvoris ovaj prozor.")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    params = {
        "client_id": cid,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    print("Otvaram browser:", url)
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

    if result.get("state") != state or not result.get("code"):
        raise SystemExit("OAuth neuspesan (state/code)")
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": result["code"],
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        },
    )
    r.raise_for_status()
    print("GMAIL_REFRESH_TOKEN=" + r.json()["refresh_token"])


if __name__ == "__main__":
    main()
