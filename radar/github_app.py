"""GitHub App za agente: dozvole samo za sadržaj i PR-ove u ovom repou.

Zašto App, a ne GITHUB_TOKEN: PR otvoren sa GITHUB_TOKEN-om ne pokreće CI. App token je i
uži (samo ovaj repo, samo contents + pull requests) i kratkotrajan (1 h).
"""

import time

import httpx
import jwt

from radar.config import Settings
from radar.github_api import API, _headers
from radar.http import request_with_retry


class AppNotConfigured(RuntimeError):
    pass


def app_jwt(app_id: str, private_key: str, now: float | None = None) -> str:
    now = int(now if now is not None else time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 9 * 60, "iss": app_id},  # GitHub traži exp <= 10 min
        private_key.replace("\\n", "\n"),
        algorithm="RS256",
    )


def installation_token(settings: Settings, client: httpx.Client) -> str:
    if not (settings.gh_app_id and settings.gh_app_installation_id and settings.gh_app_private_key):
        raise AppNotConfigured(
            "GitHub App nije podešen (GH_APP_ID/GH_APP_INSTALLATION_ID/GH_APP_PRIVATE_KEY)"
        )
    token = app_jwt(settings.gh_app_id, settings.gh_app_private_key)
    resp = request_with_retry(
        client,
        "POST",
        f"{API}/app/installations/{settings.gh_app_installation_id}/access_tokens",
        headers=_headers(token),
        json={"permissions": {"contents": "write", "pull_requests": "write"}},
    )
    return resp.json()["token"]


def remote_url(repo: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def open_pr(
    client: httpx.Client,
    token: str,
    repo: str,
    head: str,
    title: str,
    body: str,
    base: str = "main",
) -> tuple[int, str]:
    resp = request_with_retry(
        client,
        "POST",
        f"{API}/repos/{repo}/pulls",
        headers=_headers(token),
        json={"title": title, "head": head, "base": base, "body": body},
    )
    data = resp.json()
    return data["number"], data["html_url"]


def get_pr(client: httpx.Client, token: str, repo: str, number: int) -> dict:
    resp = request_with_retry(
        client, "GET", f"{API}/repos/{repo}/pulls/{number}", headers=_headers(token)
    )
    return resp.json()
