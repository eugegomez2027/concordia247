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

Nota: el resumen es heurístico. Si después querés una redacción “más humana”,
lo ideal es sumar un LLM vía GitHub Secrets.
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

ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "_posts"
DATA = ROOT / "_data"

HEADERS = {"User-Agent": "Concordia247Bot/0.1 (+https://eugegomez2027.github.io/concordia247/)"}

BLOCK_URL_FRAGMENTS = [
    "/policial",
    "/policiales",
    "/judicial",
    "/tribun",
    "/crimen",
    "/homic",
    "/abuso",
    "/viol",
    "/denunc",
]
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

# Si la fuente es hiper-local (Concordia), aceptamos aunque el título/desc no mencione "Concordia".
LOCAL_SOURCE_HOST_HINTS = [
    "concordia24.com.ar",
    "diarioelsol.com.ar",
    "elheraldo.com.ar",
    "diarioriouruguay.com",
    "diariojunio.com.ar",
    "concordia.gob.ar",
]


@dataclass
class Item:
    source: str
    source_type: str
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


def parse_sitemap(feed_url: str, source_name: str, source_type: str, hours: int = 12) -> list[Item]:
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
        out.append(Item(source=source_name, source_type=source_type, url=loc, published=published))
    return out


def parse_rss(feed_url: str, source_name: str, source_type: str) -> list[Item]:
    import feedparser  # lazy import (solo necesario en el workflow de GitHub Actions)

    d = feedparser.parse(feed_url)
    out: list[Item] = []
    for e in d.entries:
        link = getattr(e, "link", None)
        if not link:
            continue
        title = getattr(e, "title", "").strip() or None
        out.append(Item(source=source_name, source_type=source_type, url=link, title=title))
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
    u = url.lower()

    # 1) Fuentes hiper-locales: aceptamos directo.
    if any(h in u for h in LOCAL_SOURCE_HOST_HINTS):
        return True

    # 2) Si no, exigimos Concordia explícito.
    if FOCUS_RE.search(u):
        return True

    hay = " ".join([title or "", description or ""])[:300]
    return bool(FOCUS_RE.search(hay))


def extract_title_desc(html: str) -> tuple[str | None, str | None, str | None, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    # ---- title ----
    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        tw = soup.find("meta", attrs={"name": "twitter:title"})
        if tw and tw.get("content"):
            title = tw["content"].strip()
    if not title and soup.title and soup.title.text:
        title = soup.title.text.strip()

    # ---- description ----
    desc = None
    # prioridad: OG/Twitter suelen estar mejor que meta description
    for meta in [
        ("meta", {"property": "og:description"}, "content"),
        ("meta", {"name": "twitter:description"}, "content"),
        ("meta", {"name": "description"}, "content"),
    ]:
        tag = soup.find(meta[0], attrs=meta[1])
        if tag and tag.get(meta[2]):
            val = tag[meta[2]].strip()
            if val:
                desc = val
                break

    # ---- elegir un contenedor "principal" para evitar sidebar/"lo más visto" ----
    container = soup.find("article") or soup.find("main") or soup

    def is_noise_paragraph(t: str) -> bool:
        tl = t.lower()
        noise_markers = [
            "te puede interesar",
            "lo más visto",
            "más visto",
            "suscrib",
            "iniciar sesión",
            "publicidad",
            "compartir",
            "facebook",
            "instagram",
            "twitter",
        ]
        if any(m in tl for m in noise_markers):
            return True
        # párrafos que son básicamente links/URLs
        if re.fullmatch(r"https?://\S+", t.strip()):
            return True
        return False

    # ---- first paragraph (mejorado) ----
    first_p = None
    # Tomamos el primer <p> "largo" para evitar cosas tipo "Suscribite" / menús.
    for p in container.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if not txt:
            continue
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) < 80:
            continue
        if is_noise_paragraph(txt):
            continue
        first_p = txt
        break

    # ---- extra paragraphs (para resumen "periodístico") ----
    extra_paras: list[str] = []
    for p in container.find_all("p"):
        txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(txt) < 120:
            continue
        if is_noise_paragraph(txt):
            continue
        extra_paras.append(txt)
        if len(extra_paras) >= 10:
            break

    return title, desc, first_p, extra_paras


def slug_from_url(url: str) -> str:
    s = re.sub(r"https?://", "", url)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80]


def make_press_bullets(desc: str | None, first_p: str | None, extra_paras: list[str]) -> tuple[str, list[str]]:
    """Genera un lead + 3-4 bullets "tipo diario" sin LLM.

    Mejora vs v0:
    - deduplica oraciones
    - evita basura de sidebar/"te puede interesar" (ya filtrado en extracción)
    """

    chunks: list[str] = []
    if desc:
        chunks.append(desc)
    if first_p:
        chunks.append(first_p)
    chunks.extend(extra_paras[:5])

    text = re.sub(r"\s+", " ", " ".join([c for c in chunks if c])).strip()

    # Split simple por puntuación fuerte.
    sents = re.split(r"(?<=[\.\!\?])\s+", text)
    sents = [re.sub(r"\s+", " ", s).strip(" -–•\t") for s in sents]
    sents = [s for s in sents if len(s) >= 40]

    lead = (sents[0] if sents else (desc or first_p or "")).strip()
    if not lead:
        lead = (
            "No se pudo extraer un resumen automático de esta fuente (estructura/metadata insuficiente). "
            "Ver Fuente para el texto completo."
        )
    if len(lead) > 240:
        lead = lead[:237].rstrip() + "…"

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"\s+", " ", s).strip()
        # normalización liviana para dedupe
        s = re.sub(r"[\W_]+", " ", s)
        return s.strip()

    seen_norm: set[str] = set()
    bullets: list[str] = []

    for s in sents[1:20]:
        if len(bullets) >= 4:
            break
        if not s:
            continue
        # evitar duplicar el lead
        if lead and s[:70] in lead:
            continue
        ns = norm(s)
        if not ns or ns in seen_norm:
            continue
        # dedupe por prefijo (frases casi idénticas)
        if any(ns.startswith(prev[:80]) or prev.startswith(ns[:80]) for prev in seen_norm):
            continue
        seen_norm.add(ns)
        if len(s) > 190:
            s = s[:187].rstrip() + "…"
        bullets.append(s)

    if not bullets:
        bullets = ["Seguir la fuente para detalles completos."]

    return lead, bullets


def build_post(
    title_raw: str,
    source: str,
    url: str,
    desc: str | None,
    first_p: str | None,
    extra_paras: list[str],
) -> str:
    # Título “tipo diario”
    t = (title_raw or "").strip() or url
    if len(t) > 120:
        t = t[:117].rstrip() + "…"
    title = t

    lead, bullets = make_press_bullets(desc, first_p, extra_paras)

    body_parts: list[str] = []

    # Si no hay texto usable, igual publicamos un post "cerrado" sin placeholders.
    if lead:
        body_parts.append(lead)
        body_parts.append("")

    body_parts.append("## Resumen (4 puntos)")
    for b in bullets:
        body_parts.append(f"- {b}")
    body_parts.append("")

    body_parts.append(f"**Fuente:** [{source}]({url})")

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
    # Asegurar que exista el directorio de salida (Git no guarda carpetas vacías)
    POSTS.mkdir(parents=True, exist_ok=True)

    sources = load_sources()
    seen = load_seen()

    candidates: list[Item] = []
    for s in sources:
        stype = s.get("type", "media")
        if s.get("feed") == "rss":
            candidates.extend(parse_rss(s["feed_url"], s["name"], stype))
        elif s.get("feed") == "sitemap":
            candidates.extend(parse_sitemap(s["feed_url"], s["name"], stype))

    # orden: más nuevos primero si hay published
    candidates.sort(key=lambda it: it.published or dt.datetime.now(dt.timezone.utc), reverse=True)

    revisar_lines: list[str] = []
    new_posts = 0

    # cuota por tipo de fuente: priorizar medios sobre "official"
    max_official = 1
    official_posts = 0

    for it in candidates:
        if it.url in seen:
            continue

        try:
            html = fetch_text(it.url)
        except Exception:
            continue

        title, desc, first_p, extra_paras = extract_title_desc(html)
        title = it.title or title or it.url

        # cuota por tipo (official)
        if it.source_type == "official" and official_posts >= max_official:
            # no marcamos como seen: que pueda entrar en una tanda futura
            continue

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

        out.write_text(build_post(title, it.source, it.url, desc, first_p, extra_paras), encoding="utf-8")
        seen.add(it.url)
        new_posts += 1
        if it.source_type == "official":
            official_posts += 1

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
