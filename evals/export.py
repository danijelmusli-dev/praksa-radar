"""Izvezi oglase iz baze za ručno označavanje eval skupa.

    python -m evals.export 50 > evals/dataset.todo.jsonl

Svaki red ima "relevant": null; zameni sa true/false (i po želji dodaj "type" i "deadline"),
pa sačuvaj kao evals/dataset.jsonl. Oglasi koje je korisnik ocenio 👎 a imali su visoku ocenu
su najbolji kandidati (najpre idu oni).
"""

import json
import sys

from radar.db import connect


def main(argv: list[str]) -> int:
    limit = int(argv[0]) if argv else 50
    with connect() as conn:
        rows = conn.execute(
            "select o.title, o.org, o.location, o.raw_text, s.relevance, o.url from opportunities o"
            " left join scores s on s.opportunity_id = o.id"
            " left join feedback f on f.opportunity_id = o.id and f.signal <> 'up'"
            " order by (f.id is not null and coalesce(s.relevance, 0) >= 7) desc, o.first_seen desc"
            " limit %s",
            (limit,),
        ).fetchall()
    for title, org, location, text, relevance, url in rows:
        print(
            json.dumps(
                {
                    "title": title, "org": org, "location": location,
                    "text": (text or "")[:3000], "relevant": None,
                    "model_relevance": relevance, "url": url,
                },
                ensure_ascii=False,
            )
        )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
