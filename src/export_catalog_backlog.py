#!/usr/bin/env python3
"""Export every currently playable CR film and its upload source candidates."""
from __future__ import annotations

import argparse
import gzip
import json
import os
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras


LANG_PRIORITY = {
    "CZ_DUB": 0,
    "CZ_NATIVE": 1,
    "CZ_SUB": 2,
    "SK_DUB": 3,
    "SK_SUB": 4,
    "UNKNOWN": 5,
    "EN": 6,
}


SQL = """
    SELECT f.id AS cr_film_id, f.slug AS cr_slug, f.title, f.original_title,
           f.year, f.lang AS film_lang, f.description, f.tmdb_id, f.imdb_id,
           p.slug AS provider, vs.external_id, vs.title AS source_title,
           vs.duration_sec, vs.resolution_hint, vs.filesize_bytes,
           vs.view_count, vs.lang_class, vs.cdn, vs.is_primary, vs.metadata
      FROM films f
      JOIN video_sources vs ON vs.film_id = f.id AND vs.is_alive
      JOIN video_providers p ON p.id = vs.provider_id
     WHERE EXISTS (
         SELECT 1 FROM video_sources visible
          WHERE visible.film_id = f.id AND visible.is_alive
     )
     ORDER BY f.id
"""


def resolution_score(value: str | None) -> int:
    value = (value or "").lower()
    if "2160" in value or "4k" in value:
        return 4
    if "1080" in value:
        return 3
    if "720" in value:
        return 2
    if "480" in value:
        return 1
    return 0


def source_sort_key(row: dict) -> tuple:
    return (
        LANG_PRIORITY.get(row["lang_class"], 99),
        0 if row["is_primary"] else 1,
        -resolution_score(row["resolution_hint"]),
        -(row["view_count"] or 0),
    )


def display_name(title: str, year: int | None, lang_class: str) -> str:
    base = f"{title} ({year})" if year else title
    suffix = {
        "CZ_DUB": " CZ Dabing",
        "CZ_NATIVE": " CZ",
        "CZ_SUB": " CZ titulky",
        "SK_DUB": " SK",
        "SK_SUB": " SK titulky",
    }.get(lang_class, "")
    return f"{base}{suffix}"


def sktorrent_url(source: dict) -> str:
    metadata = source.get("metadata") or {}
    qualities = [item.strip() for item in (metadata.get("qualities") or "").split(",") if item.strip()]
    quality = next((q for q in ("720p", "480p", "HD", "SD") if q in qualities), None)
    quality = quality or (qualities[0] if qualities else "720p")
    return f"https://online.sktorrent.eu/media/videos//h264/{source['external_id']}_{quality}.mp4"


def build_films(rows: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["cr_film_id"]].append(row)

    films: list[dict] = []
    for film_id, sources in grouped.items():
        first = sources[0]
        by_provider: dict[str, list[dict]] = defaultdict(list)
        for source in sources:
            by_provider[source["provider"]].append(source)
        for provider_sources in by_provider.values():
            provider_sources.sort(key=source_sort_key)

        preferred = next(
            (by_provider[p][0] for p in ("prehrajto", "sktorrent", "sledujteto") if by_provider[p]),
            sources[0],
        )
        film = {
            "cr_film_id": film_id,
            "cr_slug": first["cr_slug"],
            "title": first["title"],
            "original_title": first["original_title"],
            "year": first["year"],
            "film_lang": first["film_lang"],
            "description": first["description"],
            "tmdb_id": first["tmdb_id"],
            "imdb_id": first["imdb_id"],
            "catalog_visible": True,
            "display_name": display_name(first["title"], first["year"], preferred["lang_class"]),
        }

        if by_provider["prehrajto"]:
            film["candidates"] = [
                {
                    "upload_id": source["external_id"],
                    "url": (source.get("metadata") or {}).get("url")
                        or f"https://prehraj.to/{source['external_id']}",
                    "upload_title": source["source_title"],
                    "lang_class": source["lang_class"],
                    "resolution_hint": source["resolution_hint"],
                    "duration_sec": source["duration_sec"],
                    "filesize_bytes": source["filesize_bytes"],
                    "view_count": source["view_count"],
                }
                for source in by_provider["prehrajto"]
            ]

        if by_provider["sktorrent"]:
            source = by_provider["sktorrent"][0]
            film["sktorrent_source"] = {
                "id": int(source["external_id"]),
                "url": sktorrent_url(source),
                "lang_class": source["lang_class"],
                "cdn": source["cdn"],
            }

        if by_provider["sledujteto"]:
            source = by_provider["sledujteto"][0]
            film["sledujteto_source"] = {
                "file_id": int(source["external_id"]),
                "cdn": source["cdn"],
                "lang_class": source["lang_class"],
                "filesize_bytes": source["filesize_bytes"],
            }
        films.append(film)
    return films


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--out", type=Path, default=Path("backlog/catalog-films.jsonl.gz"))
    args = parser.parse_args()
    if not args.db_url:
        raise SystemExit("DATABASE_URL or --db-url is required")

    with psycopg2.connect(args.db_url) as connection:
        connection.set_session(readonly=True)
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(SQL)
            rows = [dict(row) for row in cursor.fetchall()]

    films = build_films(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        for film in films:
            handle.write(json.dumps(film, ensure_ascii=False) + "\n")
    print(f"Wrote {len(films)} playable films to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
