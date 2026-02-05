#!/usr/bin/env python3
"""Concordia247 generator (MVP).

- Lee _data/sources.yml
- (MVP) No scrapea todavía: deja estructura para RSS.
- Mantiene revisar.md

Siguiente paso: conectar feeds RSS, filtrar Concordia/ER->Concordia, deduplicar,
crear posts en _posts como borrador PR.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    revisar = ROOT / "revisar.md"
    if revisar.exists():
        txt = revisar.read_text(encoding="utf-8")
    else:
        txt = "# REVISAR\n\n"

    if "Última actualización:" in txt:
        lines = [l for l in txt.splitlines() if not l.startswith("Última actualización:")]
        txt = "\n".join(lines).rstrip() + "\n"

    txt = txt.rstrip() + f"\n\nÚltima actualización: {stamp}\n"
    revisar.write_text(txt, encoding="utf-8")
    print(f"updated {revisar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
