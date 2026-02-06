#!/usr/bin/env python3
"""Concordia247 generator.

Objetivo (Modo 1): cada tanda crea *borradores* (posts en el repo) y un PR.

Este script:
- Lee `_data/sources.yml` (RSS o sitemap)
- Deduplica con `_data/seen.json`
- Aplica filtros:
  - foco Concordia (URL o título/descripcion contiene "concord")
  - bloqueos → `revisar.md` (denuncias/menores/crimen/acusaciones) por heurística
- Genera posts Jekyll en `_posts/`

Nota: el resumen es heurístico (meta description / primer párrafo). Si después
querés redacción “más humana”, lo mejor es sumar un LLM vía GitHub Secrets.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup
import feedparser

ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "_posts"
DATA = ROOT / "_data"

HEADERS = {"User-Agent": "Concordia247Bot/0.1 (+https://eugegomez2027.github.io/concordia247/)"}

BLOCK_URL_FRAGMENTS = ["/policial", "/policiales", "/judicial", "/tribun", "/crimen", "/homic", "/abuso", "/viol", "/denunc"]
BLOCK_KEYWORDS = [
    # crimen / policiales
    "policía",
    "policial",
    "crimen",
    "homicidio",
    "asesin",
    "robo",
    "asalto",
    "tiroteo",
    "detenido",
    "allanamiento",
    # menores
    "menor",
    "adolescente",
    "niño",
    "niña",
    # acusaciones/denuncias
    "denuncia",
    "denunció",
    "acusación",
    "acusó",
    "imputado",
    "imputaron",
    "presunto",
]

FOCUS_RE = re.compile(r"concord", re.IGNORECASE)


@dataclass
class Item:
    source: str
    url: str
    title: str | None = None
    published: dt.datetime | None = None


def load_sources() -> list[dict[str, Any]]:
    return yaml.safe_load((DATA / "sources.yml").read_text(encoding="utf-8"))


def load_seen() -> set[str]:
    p = DATA / "seen.json"
    if not p.exists():
        return set()
    obj = json.loads(p.read_text(encoding="utf-8"))
    return set(obj.get("seen", []))


def save_seen(seen: set[str]) -> None:
    (DATA / "seen.json").write_text(
        json.dumps({"seen": sorted(seen)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_text(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def parse_sitemap(feed_url: str, source_name: str, hours: int = 12) -> list[Item]:
    xml = fetch_text(feed_url)
    soup = BeautifulSoup(xml, "xml")
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out: list[Item] = []
    for urltag in soup.find_all("url"):
        loc = (urltag.loc.text or "").strip() if urltag.loc else ""
        if not loc:
            continue
        lastmod = (urltag.lastmod.text or "").strip() if urltag.lastmod else ""
        published = None
        if lastmod:
            try:
                published = dt.datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=dt.timezone.utc)
            except Exception:
                published = None
        if published and published < cutoff:
            continue
        out.append(Item(source=source_name, url=loc, published=published))
    return out


def parse_rss(feed_url: str, source_name: str) -> list[Item]:
    d = feedparser.parse(feed_url)
    out: list[Item] = []
    for e in d.entries:
        link = getattr(e, "link", None)
        if not link:
            continue
        title = getattr(e, "title", "").strip() or None
        out.append(Item(source=source_name, url=link, title=title))
    return out


def looks_blocked(url: str, title: str | None, description: str | None) -> str | None:
    u = url.lower()
    for frag in BLOCK_URL_FRAGMENTS:
        if frag in u:
            return f"URL contiene '{frag}'"
    hay = " ".join([title or "", description or ""]).lower()
    for kw in BLOCK_KEYWORDS:
        if kw in hay:
            return f"contiene keyword '{kw}'"
    return None


def focus_ok(url: str, title: str | None, description: str | None) -> bool:
    # prioridad: Concordia explícito
    if FOCUS_RE.search(url):
        return True
    hay = " ".join([title or "", description or ""])[:300]
    return bool(FOCUS_RE.search(hay))


def extract_title_desc(html: str) -> tuple[str | None, str | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    if not title and soup.title and soup.title.text:
        title = soup.title.text.strip()

    desc = None
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        desc = md["content"].strip()

    # intento de primer párrafo
    first_p = None
    p = soup.find("p")
    if p and p.get_text(strip=True):
        first_p = p.get_text(" ", strip=True)

    return title, desc, first_p


def slug_from_url(url: str) -> str:
    s = re.sub(r"https?://", "", url)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80]


def build_post(title_raw: str, source: str, url: str, desc: str | None, first_p: str | None) -> str:
    # Título “tipo diario”
    t = title_raw.strip()
    if len(t) > 120:
        t = t[:117].rstrip() + "…"
    title = f"Qué se sabe: {t}"

    summary = desc or first_p or ""
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > 320:
        summary = summary[:317].rstrip() + "…"

    body_parts: list[str] = []
    if summary:
        body_parts.append(summary)
        body_parts.append("")

    body_parts.append("- **Datos clave:** (en desarrollo)")
    body_parts.append("- **Contexto local:** (en desarrollo)")
    body_parts.append(f"- **Fuente:** [{source}]({url})")

    fm = {
        "layout": "post",
        "title": title,
        "author": "Redacción Concordia247",
        "source": source,
        "canonical_url": url,
    }

    front = "---\n" + "\n".join([f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items()]) + "\n---\n\n"
    return front + "\n".join(body_parts).rstrip() + "\n"


def append_revisar(lines: list[str]) -> None:
    revisar = ROOT / "revisar.md"
    if revisar.exists():
        txt = revisar.read_text(encoding="utf-8").rstrip() + "\n"
    else:
        txt = "# REVISAR\n\n"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    txt += f"\n\n## Tanda {stamp}\n"
    txt += "\n".join(lines) + "\n"
    revisar.write_text(txt, encoding="utf-8")


def main() -> int:
    POSTS.mkdir(parents=True, exist_ok=True)

    sources = load_sources()
    seen = load_seen()

    candidates: list[Item] = []
    for s in sources:
        if s.get("feed") == "rss":
            candidates.extend(parse_rss(s["feed_url"], s["name"]))
        elif s.get("feed") == "sitemap":
            candidates.extend(parse_sitemap(s["feed_url"], s["name"]))

    # orden: más nuevos primero si hay published
    candidates.sort(key=lambda it: it.published or dt.datetime.now(dt.timezone.utc), reverse=True)

    revisar_lines: list[str] = []
    new_posts = 0
    for it in candidates:
        if it.url in seen:
            continue

        try:
            html = fetch_text(it.url)
        except Exception:
            continue

        title, desc, first_p = extract_title_desc(html)
        title = it.title or title or it.url

        # foco
        if not focus_ok(it.url, title, desc):
            seen.add(it.url)
            continue

        # bloqueos
        reason = looks_blocked(it.url, title, desc)
        if reason:
            revisar_lines.append(f"- {title} — {it.url} _(bloqueado: {reason})_")
            seen.add(it.url)
            continue

        # crear post
        slug = slug_from_url(it.url)
        date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        out = POSTS / f"{date}-{slug}.md"
        if out.exists():
            seen.add(it.url)
            continue

        out.write_text(build_post(title, it.source, it.url, desc, first_p), encoding="utf-8")
        seen.add(it.url)
        new_posts += 1

        # por ahora limitamos por tanda (para no inundar): 5
        if new_posts >= 5:
            break

    if revisar_lines:
        append_revisar(revisar_lines)

    save_seen(seen)
    print(f"new_posts={new_posts} revisar={len(revisar_lines)} seen={len(seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
