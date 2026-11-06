"""GitHub API: javni repoi i README-i (za /projekti sync)."""

import base64

import httpx

from radar.http import request_with_retry

API = "https://api.github.com"
MAX_REPOS = 30


def _headers(token: str) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "praksa-radar",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def list_public_repos(client: httpx.Client, user: str, token: str = "") -> list[dict]:
    """Moji javni repoi bez fork-ova i arhiviranih, najskorije ažurirani prvi."""
    resp = request_with_retry(
        client,
        "GET",
        f"{API}/users/{user}/repos",
        params={"type": "owner", "sort": "pushed", "per_page": 100},
        headers=_headers(token),
    )
    repos = [r for r in resp.json() if not r.get("fork") and not r.get("archived")]
    return repos[:MAX_REPOS]


def fetch_readme(client: httpx.Client, full_name: str, token: str = "") -> tuple[str, str] | None:
    """(tekst README-a, sha) ili None ako repo nema README."""
    try:
        resp = request_with_retry(
            client, "GET", f"{API}/repos/{full_name}/readme", headers=_headers(token)
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    data = resp.json()
    text = base64.b64decode(data["content"]).decode("utf-8", "replace")
    return text, data["sha"]


def dispatch_event(
    settings, event_type: str, payload: dict, client: httpx.Client | None = None
) -> bool:
    """repository_dispatch iz Actions (GITHUB_TOKEN sa contents: write). Ne baca izuzetak."""
    if not (settings.github_token and settings.github_repository):
        return False
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        resp = client.post(
            f"{API}/repos/{settings.github_repository}/dispatches",
            headers=_headers(settings.github_token),
            json={"event_type": event_type, "client_payload": payload},
        )
        return resp.status_code == 204
    except httpx.HTTPError:
        return False
    finally:
        if own:
            client.close()
