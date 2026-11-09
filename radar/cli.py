"""Mali CLI. Upotreba:

python -m radar.cli set-filter filter.json     # MVP filter po ključnim rečima (Faza 1)
python -m radar.cli show-filter
python -m radar.cli set-profile profil.txt     # profil rečima -> LLM -> nova verzija
python -m radar.cli show-profile
python -m radar.cli feedback <opportunity_id> up|down|less_like_this
"""

import json
import sys
from pathlib import Path

from radar.config import get_settings
from radar.db import connect, kv_set
from radar.profile import get_current_profile, parse_profile, save_profile
from radar.providers import make_embedder, make_llm
from radar.rank import FILTER_KEY, KeywordFilter, get_filter


def main(argv: list[str]) -> int:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "set-filter" and len(args) == 1:
        filt = KeywordFilter(**json.loads(Path(args[0]).read_text(encoding="utf-8")))
        with connect() as conn:
            kv_set(conn, FILTER_KEY, filt.model_dump())
        print("filter sačuvan")
        return 0
    if cmd == "show-filter" and not args:
        with connect() as conn:
            print(get_filter(conn).model_dump_json(indent=2))
        return 0
    if cmd == "set-profile" and len(args) == 1:
        s = get_settings()
        if not (s.has_llm and s.has_embedder):
            print("Trebaju GEMINI_API_KEY (ili ANTHROPIC_API_KEY + VOYAGE_API_KEY)")
            return 1
        text = Path(args[0]).read_text(encoding="utf-8")
        profile = parse_profile(make_llm(s, s.rerank_model), text)
        with connect() as conn:
            stored = save_profile(conn, make_embedder(s), text, profile)
        print(f"profil verzija {stored.id}:\n{profile.model_dump_json(indent=2)}")
        return 0
    if cmd == "show-profile" and not args:
        with connect() as conn:
            stored = get_current_profile(conn)
        print(
            f"verzija {stored.id}:\n{stored.profile.model_dump_json(indent=2)}"
            if stored
            else "nema profila (koristi se MVP filter)"
        )
        return 0
    if cmd == "feedback" and len(args) == 2 and args[1] in ("up", "down", "less_like_this"):
        with connect() as conn:
            conn.execute(
                "insert into feedback (opportunity_id, signal) values (%s, %s)",
                (int(args[0]), args[1]),
            )
            conn.commit()
        print("zabeleženo")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
