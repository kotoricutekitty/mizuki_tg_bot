#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Export sanitized fixture summaries from a legacy SQLite database.")
    parser.add_argument("--db", required=True, help="Path to db.sqlite")
    parser.add_argument("--out", default="-", help="Output JSON path, or '-' for stdout")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, url, status, media_paths, message_id, author_name, title, text, canonical_url FROM submissions ORDER BY id"
    ).fetchall()

    fixtures = []
    for row in rows:
        try:
            media_paths = json.loads(row["media_paths"] or "[]")
        except json.JSONDecodeError:
            media_paths = []
        url = row["url"] or ""
        parsed = urlparse(url)
        canonical = urlparse(row["canonical_url"] or "")
        fixtures.append(
            {
                "source_id": row["id"],
                "host": parsed.netloc.lower().replace("www.", ""),
                "path_hint": parsed.path[:48],
                "status": row["status"],
                "media_count": len(media_paths),
                "media_exts": [os.path.splitext(path)[1].lower() for path in media_paths[:10]],
                "has_message_id": row["message_id"] is not None,
                "has_author": bool(row["author_name"]),
                "has_title": bool(row["title"]),
                "text_len": len(row["text"] or ""),
                "canonical_host": canonical.netloc.lower().replace("www.", ""),
            }
        )
    conn.close()

    payload = {
        "source": "sanitized_sqlite_summary",
        "total": len(fixtures),
        "fixtures": fixtures,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
