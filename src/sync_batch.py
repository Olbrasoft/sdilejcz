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
RESERVATION_TTL_MINUTES = 480


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


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def git_sync_latest() -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run(["git", "fetch", "origin", "main"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=REPO_ROOT, check=True)
    else:
        subprocess.run(["git", "pull", "--ff-only", "origin", "main"], cwd=REPO_ROOT, check=True)


def clean_expired_reservations(state: dict) -> None:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=RESERVATION_TTL_MINUTES)
    kept = []
    for item in state.get("in_progress", []):
        reserved_at = parse_iso(item.get("reserved_at"))
        if reserved_at and reserved_at >= cutoff:
            kept.append(item)
    state["in_progress"] = kept


def state_transaction(reason: str, mutate, attempts: int = 8):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if os.environ.get("COMMIT_AFTER_EACH_UPLOAD", "").strip().lower() in ("1", "true", "yes", "on"):
                git_sync_latest()
            state = load_state()
            clean_expired_reservations(state)
            changed, result = mutate(state)
            if not changed:
                return state, result
            save_state(state)
            if os.environ.get("COMMIT_AFTER_EACH_UPLOAD", "").strip().lower() in ("1", "true", "yes", "on"):
                commit_progress(reason)
            return state, result
        except Exception as exc:
            last_error = exc
            log(f"step=state-transaction-retry attempt={attempt}/{attempts} reason={reason!r} err={exc}")
            time.sleep(min(attempt, 10))
    raise RuntimeError(f"state transaction failed after {attempts} attempts: {last_error}")


def set_account(state: dict):
    if state.get("account") == "sdilej.cz":
        return False, None
    state["account"] = "sdilej.cz"
    return True, None


def commit_progress(reason: str) -> None:
    if os.environ.get("COMMIT_AFTER_EACH_UPLOAD", "").strip().lower() not in ("1", "true", "yes", "on"):
        return

    state = load_state()
    total_uploads = len(state.get("uploads", []))
    commands = [
        ["git", "add", "state/uploaded.json", "state/sync.log"],
        ["git", "diff", "--cached", "--quiet"],
    ]
    subprocess.run(commands[0], cwd=REPO_ROOT, check=True)
    diff = subprocess.run(commands[1], cwd=REPO_ROOT)
    if diff.returncode == 0:
        return
    message = f"chore(sync): {reason} - total uploads now {total_uploads}"
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    log(f"step=git-pushed reason={reason!r} total_uploads={total_uploads}")


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


def remove_reservation(state: dict, cr_film_id: int, worker_id: str) -> None:
    state["in_progress"] = [
        item for item in state.get("in_progress", [])
        if not (item.get("cr_film_id") == cr_film_id and item.get("worker_id") == worker_id)
    ]


def reserve_next_film(backlog: list[dict], worker_id: str, run_id: str, extra_exclude: set[int]) -> tuple[dict, dict | None]:
    picked: dict | None = None

    def mutate(state: dict):
        nonlocal picked
        picked = pick_next(state, backlog, extra_exclude)
        if picked is None:
            return False, None
        state.setdefault("in_progress", []).append({
            "cr_film_id": picked["cr_film_id"],
            "cr_slug": picked.get("cr_slug"),
            "title": picked["title"],
            "year": picked["year"],
            "sktorrent_id": picked.get("id"),
            "worker_id": worker_id,
            "run_id": run_id,
            "reserved_at": now_iso(),
        })
        return True, picked

    return state_transaction(f"reserve worker={worker_id}", mutate)


def record_failure(film: dict, reason: str, worker_id: str, timing: dict | None = None) -> None:
    def mutate(state: dict):
        remove_reservation(state, film["cr_film_id"], worker_id)
        if any(f.get("cr_film_id") == film["cr_film_id"] for f in state.get("failed_attempts", [])):
            return True, None
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
        return True, None

    state_transaction(f"failure cr_film_id={film['cr_film_id']}", mutate)


def process_one(film: dict, session, worker_id: str) -> bool:
    name = display_name(film)
    cr_film_id = film["cr_film_id"]
    start_total = time.monotonic()
    log(f"step=film-start cr_film_id={cr_film_id} name={name!r}")

    t = time.monotonic()
    resolved = resolve_cdn(film["url"])
    cdn_sec = round(time.monotonic() - t, 1)
    if not resolved:
        log(f"step=cdn-resolve failed cr_film_id={cr_film_id}")
        record_failure(film, "cdn_resolve_failed", worker_id, {"cdn_resolve_sec": cdn_sec})
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
            film,
            f"download_failed: {exc}",
            worker_id,
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
            film,
            f"upload_failed: {exc}",
            worker_id,
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
    entry = {
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
        "worker_id": worker_id,
        "timing": {
            "cdn_resolve_sec": cdn_sec,
            "download_sec": download_sec,
            "upload_sec": upload_sec,
            "total_sec": total_sec,
        },
    }

    def mutate(state: dict):
        remove_reservation(state, cr_film_id, worker_id)
        if any(u.get("cr_film_id") == cr_film_id for u in state.get("uploads", [])):
            return True, None
        state.setdefault("uploads", []).append(entry)
        return True, None

    state, _ = state_transaction(f"upload cr_film_id={cr_film_id}", mutate)
    log(f"step=state-saved cr_film_id={cr_film_id} total_uploads={len(state.get('uploads', []))}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=None,
        help="Stop picking new films after this many minutes; current film is allowed to finish.",
    )
    parser.add_argument("--worker-id", default=os.environ.get("WORKER_ID", "single"))
    args = parser.parse_args()
    batch_started = time.monotonic()
    max_runtime_sec = args.max_runtime_minutes * 60 if args.max_runtime_minutes else None

    email = os.environ.get("SDILEJ_EMAIL")
    password = os.environ.get("SDILEJ_PASSWORD")
    if not email or not password:
        log("ERROR: SDILEJ_EMAIL and SDILEJ_PASSWORD must be set")
        return 2

    if not BACKLOG.is_file():
        log(f"ERROR: missing backlog file: {BACKLOG}")
        return 2

    backlog = load_backlog()
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    worker_id = args.worker_id
    state = load_state()
    log(
        f"step=batch-start worker={worker_id} run_id={run_id} count={args.count} backlog={len(backlog)} "
        f"uploads={len(state.get('uploads', []))} failed={len(state.get('failed_attempts', []))}"
    )

    session = login(email, password)
    state_transaction("set account", set_account)

    succeeded = 0
    failed = 0
    extra_exclude: set[int] = set()
    for index in range(args.count):
        elapsed_sec = time.monotonic() - batch_started
        if max_runtime_sec is not None and elapsed_sec >= max_runtime_sec:
            log(
                f"step=batch-stop runtime-limit elapsed={round(elapsed_sec, 1)}s "
                f"limit={round(max_runtime_sec, 1)}s"
            )
            break
        log(f"step=iteration {index + 1}/{args.count}")
        state, film = reserve_next_film(backlog, worker_id, run_id, extra_exclude)
        if film is None:
            log("step=batch-end no candidate")
            break
        extra_exclude.add(film["cr_film_id"])
        if process_one(film, session, worker_id):
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
