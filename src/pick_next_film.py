#!/usr/bin/env python3
"""Pick the next film from the sktorrent backlog."""
from __future__ import annotations

import json
import os
import sys
import datetime as dt
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKLOG = REPO_ROOT / "backlog" / "sktorrent-films.jsonl"
STATE = REPO_ROOT / "state" / "uploaded.json"
NATIVE_ORIGINS = {"cs", "sk"}
RETRYABLE_FAILURE_PREFIXES = ("download_failed", "upload_failed")
FAILED_RETRY_DELAY_MINUTES = 30
FAILED_MAX_ATTEMPTS = 4


def load_backlog(path: Path = BACKLOG) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_state(path: Path = STATE) -> dict:
    return json.loads(path.read_text())


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def failure_retryable(failure: dict, now: dt.datetime | None = None) -> bool:
    reason = failure.get("reason") or ""
    if not reason.startswith(RETRYABLE_FAILURE_PREFIXES):
        return False
    if int(failure.get("attempt_count") or 1) >= FAILED_MAX_ATTEMPTS:
        return False
    failed_at = parse_iso(failure.get("failed_at"))
    if not failed_at:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    return now - failed_at >= dt.timedelta(minutes=FAILED_RETRY_DELAY_MINUTES)


def excluded_ids(state: dict, extra: set[int] | None = None) -> set[int]:
    done = {u["cr_film_id"] for u in state.get("uploads", [])}
    failed = {
        f["cr_film_id"]
        for f in state.get("failed_attempts", [])
        if not failure_retryable(f)
    }
    reserved = {r["cr_film_id"] for r in state.get("in_progress", [])}
    return done | failed | reserved | (extra or set())


def _require_cs_audio() -> bool:
    value = os.environ.get("REQUIRE_CS_AUDIO", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def _has_cz_sk_subtitles(film: dict) -> bool:
    for subtitle in film.get("sktorrent_subtitles") or []:
        if (subtitle.get("lang") or "").lower() in ("cs", "sk"):
            return True
    return False


def _has_burned_in_subs(film: dict) -> bool:
    return bool(film.get("subs_burned_in"))


def pick_next(
    state: dict,
    backlog_rows: list[dict],
    extra_exclude: set[int] | None = None,
) -> dict | None:
    excluded = excluded_ids(state, extra_exclude)
    require_cs = _require_cs_audio()
    for row in backlog_rows:
        if row.get("cr_film_id") in excluded:
            continue
        if require_cs:
            has_cs_audio = row.get("detected_language") in ("cs", "sk")
            has_subtitles = _has_cz_sk_subtitles(row) or _has_burned_in_subs(row)
            if not has_cs_audio and not has_subtitles:
                continue
        return row
    return None


def display_name(film: dict) -> str:
    title = film["title"]
    year = film["year"]
    original_language = film.get("original_language")
    audio = film.get("detected_language")
    suffix = "CZ"
    if audio not in ("cs", "sk") and (_has_cz_sk_subtitles(film) or _has_burned_in_subs(film)):
        suffix = "CZ titulky"
    elif original_language not in NATIVE_ORIGINS and original_language is not None:
        suffix = "CZ Dabing"
    return f"{title} ({year}) {suffix}.mp4"


def main() -> int:
    state = load_state()
    rows = load_backlog()
    picked = pick_next(state, rows)
    if picked is None:
        print("No film to upload")
        return 1
    print(json.dumps({"film": picked, "display_name": display_name(picked)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
