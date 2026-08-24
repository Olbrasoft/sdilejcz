#!/usr/bin/env python3
"""Reconcile persisted upload state with completed files on Sdilej.cz."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path

from pick_next_film import STATE, display_name, load_backlog, load_state, parse_iso
from sdilej_upload import file_id_from_url, list_account_files, login


DEFAULT_GRACE_HOURS = 12


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconcile(
    state: dict,
    backlog: list[dict],
    account_files: dict[int, dict[str, str]],
    *,
    grace_hours: int = DEFAULT_GRACE_HOURS,
) -> dict[str, int]:
    initial_in_progress = len(state.get("in_progress", []))
    initial_failures = len(state.get("failed_attempts", []))
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=grace_hours)
    kept: list[dict] = []
    missing: list[dict] = []
    represented_file_ids: set[int] = set()

    for upload in state.get("uploads", []):
        file_id = file_id_from_url(upload.get("sdilej_url"))
        uploaded_at = parse_iso(upload.get("uploaded_at"))
        within_grace = bool(uploaded_at and uploaded_at >= cutoff)
        if file_id in account_files or (file_id is not None and within_grace):
            kept.append(upload)
            if file_id in account_files:
                represented_file_ids.add(file_id)
            continue
        stale = dict(upload)
        stale["status"] = "missing_on_account"
        stale["detected_missing_at"] = now_iso()
        missing.append(stale)

    backlog_by_name: dict[str, list[dict]] = defaultdict(list)
    for film in backlog:
        backlog_by_name[display_name(film)].append(film)

    restored = 0
    kept_film_ids = {item.get("cr_film_id") for item in kept}
    for file_id, account_file in account_files.items():
        if file_id in represented_file_ids:
            continue
        matches = backlog_by_name.get(account_file["name"], [])
        if len(matches) != 1:
            continue
        film = matches[0]
        cr_film_id = film["cr_film_id"]
        if cr_film_id in kept_film_ids:
            continue
        kept.append({
            "cr_film_id": cr_film_id,
            "cr_slug": film.get("cr_slug"),
            "title": film["title"],
            "year": film.get("year"),
            "source": "account_reconciliation",
            "sdilej_url": account_file["url"],
            "sdilej_name": account_file["name"],
            "uploaded_at": now_iso(),
            "status": "uploaded",
        })
        kept_film_ids.add(cr_film_id)
        restored += 1

    retry_ids = {item.get("cr_film_id") for item in missing}
    retry_ids.update(film["cr_film_id"] for film in backlog if film["cr_film_id"] not in kept_film_ids)
    state["uploads"] = kept
    state.setdefault("missing_on_account", []).extend(missing)
    state["failed_attempts"] = [
        item for item in state.get("failed_attempts", [])
        if item.get("cr_film_id") not in retry_ids
    ]
    state["in_progress"] = []
    removed_failures = initial_failures - len(state["failed_attempts"])
    if missing or restored or initial_in_progress or removed_failures:
        state["account_reconciled_at"] = now_iso()
        state["last_updated"] = now_iso()
    return {
        "account_files": len(account_files),
        "kept_uploads": len(kept),
        "missing_uploads": len(missing),
        "restored_uploads": restored,
        "retry_films": len(retry_ids),
        "removed_failures": removed_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--grace-hours", type=int, default=DEFAULT_GRACE_HOURS)
    args = parser.parse_args()
    email = os.environ.get("SDILEJ_EMAIL")
    password = os.environ.get("SDILEJ_PASSWORD")
    if not email or not password:
        raise SystemExit("SDILEJ_EMAIL and SDILEJ_PASSWORD must be set")

    session = login(email, password)
    state = load_state(args.state)
    stats = reconcile(
        state,
        load_backlog(),
        list_account_files(session),
        grace_hours=args.grace_hours,
    )
    args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
