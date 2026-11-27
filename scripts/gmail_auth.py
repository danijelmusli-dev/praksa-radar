"""Jednokratni lokalni OAuth (gmail.readonly). Ispiše refresh token.

Upotreba: GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... uv run python scripts/gmail_auth.py
OAuth client mora biti tipa "Desktop"; redirect ide na lokalni port.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize the radar to read Gmail alerts")
    parser.add_argument("--client-json", type=Path, help="Google Desktop OAuth client download")
    parser.add_argument(
        "--github-repo", help="Store credentials directly in GitHub Actions secrets"
    )
    args = parser.parse_args()
    if args.client_json:
        client = json.loads(args.client_json.read_text(encoding="utf-8")).get("installed")
        if not client:
            parser.error("Download a Desktop app OAuth client.")
        cid, secret = client["client_id"], client["client_secret"]
    else:
        cid, secret = os.environ["GMAIL_CLIENT_ID"], os.environ["GMAIL_CLIENT_SECRET"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())

    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state") != [state]:
                self.send_error(400)
                return
            result["code"] = q.get("code", [""])[0]
            result["state"] = q.get("state", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Gotovo, mozes da zatvoris ovaj prozor.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    params = {
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "code_challenge": challenge.rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    with server:
        server.timeout = 1
        print("Approve read-only Gmail access in the browser.", flush=True)
        if not webbrowser.open(url):
            raise SystemExit("Unable to open the authorization browser.")
        deadline = time.monotonic() + 600
        while not result and time.monotonic() < deadline:
            server.handle_request()

    if result.get("state") != state or not result.get("code"):
        raise SystemExit("OAuth neuspesan (state/code)")
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code_verifier": verifier,
            "code": result["code"],
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
    )
    r.raise_for_status()
    tokens = r.json()
    if args.github_repo:
        check = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/labels",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
        )
        check.raise_for_status()
        for name, value in {
            "GMAIL_CLIENT_ID": cid,
            "GMAIL_CLIENT_SECRET": secret,
            "GMAIL_REFRESH_TOKEN": tokens["refresh_token"],
        }.items():
            subprocess.run(
                ["gh", "secret", "set", name, "--repo", args.github_repo],
                input=value,
                text=True,
                check=True,
            )
        print("Gmail API verified. GitHub Actions secrets saved.")
        if not any(label.get("name") == "radar" for label in check.json()["labels"]):
            print("Create the radar label in Gmail and apply it to incoming alerts.")
    else:
        print("GMAIL_REFRESH_TOKEN=" + tokens["refresh_token"])


if __name__ == "__main__":
    main()
