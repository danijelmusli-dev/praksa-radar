"""CI guard za PR-ove agenata (grane agent/*): odbija diff van dozvoljenih putanja.

    python scripts/check_agent_diff.py <base-ref> <head-branch>

Za grane koje nisu agent/* izlazi sa 0. Pokreće se u CI-ju bez ikakvih tajni.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.agents.guard import check_diff, mode_for_branch  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    base, branch = argv
    mode = mode_for_branch(branch)
    if mode is None:
        print(f"{branch}: nije agent grana, guard se preskače")
        return 0
    paths = [p for p in git("diff", "--name-only", f"{base}...HEAD").splitlines() if p]
    errors = check_diff(
        paths,
        lambda p: git("show", f"{base}:{p}"),
        lambda p: Path(p).read_text(encoding="utf-8"),
        mode,
    )
    if not paths:
        errors.append("PR agenta je prazan")
    for e in errors:
        print("ODBIJENO:", e)
    print(f"{branch} ({mode}): {len(paths)} datoteka, {len(errors)} prekršaja")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
