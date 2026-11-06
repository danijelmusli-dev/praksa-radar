"""Tanak omotač oko git-a za agente 1 i 2 (grane agent/*, nikad direktno na main)."""

import subprocess
from pathlib import Path

GIT_TIMEOUT = 120


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, path: str | Path, secrets: tuple[str, ...] = ()):
        self.path = Path(path)
        self._secrets = [s for s in secrets if s]

    def _scrub(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        return text

    def run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            encoding="utf-8",
        )
        if check and proc.returncode != 0:
            raise GitError(self._scrub(f"git {args[0]} nije uspeo: {proc.stderr.strip()[:300]}"))
        return proc.stdout

    def changed_paths(self) -> list[str]:
        """Sve izmenjene i nove datoteke u radnom stablu (posix putanje)."""
        paths = []
        for line in self.run("status", "--porcelain", "-uall").splitlines():
            entry = line[3:]
            paths.append(entry.split(" -> ")[-1].strip().strip('"'))
        return sorted(set(paths))

    def discard_changes(self) -> None:
        self.run("reset", "--hard", "HEAD")
        self.run("clean", "-fd")

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").strip()

    def current_branch(self) -> str:
        return self.run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def commit_on_new_branch(self, branch: str, message: str, name: str, email: str) -> str:
        if not branch.startswith("agent/"):
            raise GitError("agenti smeju da pišu samo na grane agent/*")
        self.run("checkout", "-b", branch)
        self.run("add", "-A")
        self.run("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", message)
        return self.head()

    def push(self, remote_url: str, branch: str) -> None:
        if not branch.startswith("agent/"):
            raise GitError("agenti smeju da pišu samo na grane agent/*")
        self.run("push", remote_url, f"HEAD:refs/heads/{branch}")

    def revert(self, sha: str, branch: str, name: str, email: str) -> str:
        """Nova grana agent/* sa revert commit-om (merge commit se vraća sa -m 1)."""
        if not branch.startswith("agent/"):
            raise GitError("agenti smeju da pišu samo na grane agent/*")
        parents = self.run("rev-list", "--parents", "-n", "1", sha).split()
        self.run("checkout", "-b", branch)
        cfg = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        mainline = ["-m", "1"] if len(parents) > 2 else []
        self.run(*cfg, "revert", "--no-edit", *mainline, sha)
        return self.head()

    def diff_names(self, base: str) -> list[str]:
        return [p for p in self.run("diff", "--name-only", f"{base}...HEAD").splitlines() if p]

    def show(self, ref: str, path: str) -> str:
        return self.run("show", f"{ref}:{path}", check=False)
