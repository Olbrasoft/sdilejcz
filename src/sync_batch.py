#!/usr/bin/env python3
"""Upload N sktorrent films to Sdilej.cz."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pick_next_film import BACKLOG, STATE, display_name, load_backlog, load_state, pick_next
from resolve_sktorrent_cdn import resolve as resolve_cdn
from sdilej_upload import login, upload_file


REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = REPO_ROOT / "state" / "sync.log"
TMP_DIR = Path("/tmp")
MIN_FILE_SIZE = 10_000_000


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as handle:
        handle.write(line + "\n")


def save_state(state: dict) -> None:
    state["last_updated"] = now_iso()
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def safe_filename(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_")


def download(url: str, dest: Path, timeout_sec: int = 540) -> int:
    command = [
        "curl",
        "-fL",
        url,
        "-H",
        "User-Agent: Mozilla/5.0",
        "-H",
        "Referer: https://sktorrent.eu/",
        "--max-time",
        str(timeout_sec),
        "--speed-time",
        "60",
        "--speed-limit",
        "10000",
        "-s",
        "-S",
        "-o",
        str(dest),
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit={proc.returncode} stderr={proc.stderr.strip()[:200]}")
    size = dest.stat().st_size
    if size < MIN_FILE_SIZE:
        raise RuntimeError(f"file too small: {size} B")
    return size


def record_failure(state: dict, film: dict, reason: str, timing: dict | None = None) -> None:
    entry = {
        "cr_film_id": film["cr_film_id"],
        "cr_slug": film.get("cr_slug"),
        "title": film["title"],
        "year": film["year"],
        "sktorrent_id": film.get("id"),
        "reason": reason,
        "failed_at": now_iso(),
    }
    if timing:
        entry["timing"] = timing
    state.setdefault("failed_attempts", []).append(entry)
    save_state(state)


def process_one(film: dict, session, state: dict) -> bool:
    name = display_name(film)
    cr_film_id = film["cr_film_id"]
    start_total = time.monotonic()
    log(f"step=film-start cr_film_id={cr_film_id} name={name!r}")

    t = time.monotonic()
    resolved = resolve_cdn(film["url"])
    cdn_sec = round(time.monotonic() - t, 1)
    if not resolved:
        log(f"step=cdn-resolve failed cr_film_id={cr_film_id}")
        record_failure(state, film, "cdn_resolve_failed", {"cdn_resolve_sec": cdn_sec})
        return False
    log(f"step=cdn-resolve done cr_film_id={cr_film_id} dur={cdn_sec}s url={resolved}")

    tmp_path = TMP_DIR / safe_filename(name)
    t = time.monotonic()
    try:
        size = download(resolved, tmp_path)
        download_sec = round(time.monotonic() - t, 1)
        log(f"step=download done cr_film_id={cr_film_id} size={size} dur={download_sec}s")
    except Exception as exc:
        download_sec = round(time.monotonic() - t, 1)
        if tmp_path.exists():
            tmp_path.unlink()
        log(f"step=download failed cr_film_id={cr_film_id} err={exc}")
        record_failure(
            state,
            film,
            f"download_failed: {exc}",
            {"cdn_resolve_sec": cdn_sec, "download_sec": download_sec},
        )
        return False

    t = time.monotonic()
    try:
        result = upload_file(session, tmp_path, display_name=name)
        upload_sec = round(time.monotonic() - t, 1)
        log(f"step=upload done cr_film_id={cr_film_id} dur={upload_sec}s url={result['url']}")
    except Exception as exc:
        upload_sec = round(time.monotonic() - t, 1)
        log(f"step=upload failed cr_film_id={cr_film_id} err={exc}")
        record_failure(
            state,
            film,
            f"upload_failed: {exc}",
            {
                "cdn_resolve_sec": cdn_sec,
                "download_sec": download_sec,
                "upload_sec": upload_sec,
            },
        )
        return False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    total_sec = round(time.monotonic() - start_total, 1)
    state.setdefault("uploads", []).append(
        {
            "cr_film_id": cr_film_id,
            "cr_slug": film.get("cr_slug"),
            "title": film["title"],
            "year": film["year"],
            "sktorrent_id": film.get("id"),
            "sdilej_url": result["url"],
            "sdilej_name": result.get("name"),
            "uploaded_at": now_iso(),
            "status": "uploaded",
            "size_bytes": size,
            "timing": {
                "cdn_resolve_sec": cdn_sec,
                "download_sec": download_sec,
                "upload_sec": upload_sec,
                "total_sec": total_sec,
            },
        }
    )
    save_state(state)
    log(f"step=state-saved cr_film_id={cr_film_id} total_uploads={len(state['uploads'])}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()

    email = os.environ.get("SDILEJ_EMAIL")
    password = os.environ.get("SDILEJ_PASSWORD")
    if not email or not password:
        log("ERROR: SDILEJ_EMAIL and SDILEJ_PASSWORD must be set")
        return 2

    if not BACKLOG.is_file():
        log(f"ERROR: missing backlog file: {BACKLOG}")
        return 2

    state = load_state()
    backlog = load_backlog()
    log(
        f"step=batch-start count={args.count} backlog={len(backlog)} "
        f"uploads={len(state.get('uploads', []))} failed={len(state.get('failed_attempts', []))}"
    )

    session = login(email, password)
    state["account"] = email
    save_state(state)

    succeeded = 0
    failed = 0
    extra_exclude: set[int] = set()
    for index in range(args.count):
        log(f"step=iteration {index + 1}/{args.count}")
        film = pick_next(state, backlog, extra_exclude)
        if film is None:
            log("step=batch-end no candidate")
            break
        extra_exclude.add(film["cr_film_id"])
        if process_one(film, session, state):
            succeeded += 1
        else:
            failed += 1

    log(
        f"step=batch-end succeeded={succeeded} failed={failed} "
        f"total_uploads={len(state.get('uploads', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

