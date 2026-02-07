#!/usr/bin/env python3
"""Re-redacta posts recientes usando la lógica actual del generador.

Uso:
  python3 script/refresh_latest.py --date 2026-02-07

Reescribe los .md de esa fecha en _posts (mantiene frontmatter, recalcula cuerpo).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from generate import fetch_text, extract_title_desc, build_post

ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "_posts"


def parse_frontmatter(md: str) -> tuple[dict, str]:
    if not md.startswith("---"):
        return {}, md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}, md
    fm_raw = parts[1]
    body = parts[2].lstrip("\n")
    fm = yaml.safe_load(fm_raw) or {}
    return fm, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()

    date = args.date
    pat = re.compile(rf"^{re.escape(date)}-.*\\.md$")

    files = sorted([p for p in POSTS.iterdir() if p.is_file() and pat.match(p.name)])
    if not files:
        print("No matching posts")
        return 0

    changed = 0
    for p in files:
        md = p.read_text(encoding="utf-8")
        fm, _body = parse_frontmatter(md)

        url = fm.get("canonical_url")
        title_raw = fm.get("title") or p.stem
        source = fm.get("source") or "Fuente"

        if not url:
            continue

        try:
            html = fetch_text(url)
        except Exception:
            continue

        title, desc, first_p, extra_paras = extract_title_desc(html)
        title_final = title or title_raw

        out = build_post(title_final, source, url, desc, first_p, extra_paras)
        if out != md:
            p.write_text(out, encoding="utf-8")
            changed += 1

    print(f"refreshed={changed}/{len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
