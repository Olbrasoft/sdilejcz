#!/usr/bin/env python3
"""Sdilej.cz upload client.

Run:
    SDILEJ_EMAIL=... SDILEJ_PASSWORD=... \
        python3 src/sdilej_upload.py /path/to/file.mp4 ["Display Name.mp4"]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from typing import BinaryIO

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience dependency
    load_dotenv = None


BASE_URL = "https://sdilej.cz"
LOGIN_URL = f"{BASE_URL}/sql.php"
LOGIN_PAGE = f"{BASE_URL}/prihlasit"
UPLOAD_PAGE = f"{BASE_URL}/upload"
UPLOAD_URL = "https://uploadweb2.sdilej.cz/upload/index.php"
DEFAULT_CHUNK_SIZE = 2 * 1024 * 1024
DEFAULT_CHUNK_RETRIES = 5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)


class SdilejError(RuntimeError):
    """Raised when Sdilej.cz returns an unexpected response."""


def _load_dotenv() -> None:
    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def login(email: str, password: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    prime = session.get(LOGIN_PAGE, timeout=30)
    prime.raise_for_status()

    response = session.post(
        LOGIN_URL,
        data={"login": email, "heslo": password},
        headers={"Referer": LOGIN_PAGE},
        allow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()

    profile = session.get(f"{BASE_URL}/nastaveni", allow_redirects=False, timeout=30)
    if profile.status_code != 200 or "SDILEJ" not in session.cookies:
        raise SdilejError(f"Login failed: status={profile.status_code} url={response.url}")

    return session


def fetch_user_id(session: requests.Session) -> str:
    response = session.get(UPLOAD_PAGE, timeout=30)
    response.raise_for_status()
    match = re.search(r'name=["\']user_id["\']\s+value=["\'](\d+)["\']', response.text)
    if not match:
        raise SdilejError("Upload page did not contain a user_id field")
    return match.group(1)


def _parse_upload_response(response: requests.Response) -> dict:
    response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise SdilejError(f"Upload response is not JSON: {response.text[:300]!r}") from exc

    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise SdilejError(f"Upload response is missing files[]: {payload!r}")
    return files[0]


def _post_chunk(
    session: requests.Session,
    *,
    user_id: str,
    display_name: str,
    chunk: bytes,
    mime_type: str,
    start: int,
    end: int,
    total: int,
    max_retries: int = DEFAULT_CHUNK_RETRIES,
) -> dict:
    headers = {
        "Referer": UPLOAD_PAGE,
        "Origin": BASE_URL,
        "Content-Range": f"bytes {start}-{end}/{total}",
    }
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            files = {"files[]": (display_name, chunk, mime_type)}
            response = session.post(
                UPLOAD_URL,
                data={"user_id": user_id},
                files=files,
                headers=headers,
                timeout=180,
            )
            return _parse_upload_response(response)
        except (requests.RequestException, SdilejError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            time.sleep(attempt)
    raise SdilejError(f"Chunk upload failed after {max_retries} attempts: {last_error}")


def _read_chunk(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if not data:
        raise EOFError
    return data


def upload_file(
    session: requests.Session,
    path: Path,
    *,
    display_name: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not path.is_file():
        raise FileNotFoundError(path)

    user_id = fetch_user_id(session)
    final_name = display_name or path.name
    total = path.stat().st_size
    mime_type = mimetypes.guess_type(final_name)[0] or "application/octet-stream"

    last: dict | None = None
    with path.open("rb") as handle:
        start = 0
        while start < total:
            chunk = _read_chunk(handle, min(chunk_size, total - start))
            end = start + len(chunk) - 1
            last = _post_chunk(
                session,
                user_id=user_id,
                display_name=final_name,
                chunk=chunk,
                mime_type=mime_type,
                start=start,
                end=end,
                total=total,
            )
            start = end + 1

    if not last or "url" not in last:
        raise SdilejError(f"Final upload response did not include url: {last!r}")
    return last


def upload_with_credentials(
    path: Path,
    *,
    display_name: str | None = None,
    email: str | None = None,
    password: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict:
    _load_dotenv()
    email = email or os.environ.get("SDILEJ_EMAIL")
    password = password or os.environ.get("SDILEJ_PASSWORD")
    if not email or not password:
        raise SdilejError("SDILEJ_EMAIL and SDILEJ_PASSWORD must be set")

    session = login(email, password)
    return upload_file(session, path, display_name=display_name, chunk_size=chunk_size)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("display_name", nargs="?")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args()

    result = upload_with_credentials(
        args.path,
        display_name=args.display_name,
        chunk_size=args.chunk_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
