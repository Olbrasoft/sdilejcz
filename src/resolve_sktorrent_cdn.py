#!/usr/bin/env python3
"""Resolve a live SK Torrent CDN edge URL."""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request


EDGE_RANGE = range(1, 31)
TIMEOUT = 8.0
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://sktorrent.eu/",
}


def candidates(url: str):
    yield url
    base = re.sub(r"https?://(online\d*\.)?sktorrent\.eu", "", url)
    for n in EDGE_RANGE:
        candidate = f"https://online{n}.sktorrent.eu{base}"
        if candidate != url:
            yield candidate


def head_ok(url: str) -> bool:
    request = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            length = int(response.headers.get("Content-Length", "0"))
            return response.status in (200, 206) and length > 1_000_000
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def resolve(url: str) -> str | None:
    for candidate in candidates(url):
        if head_ok(candidate):
            return candidate
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <sktorrent_url>", file=sys.stderr)
        return 2
    resolved = resolve(sys.argv[1])
    if not resolved:
        print("ERROR: no live CDN edge found", file=sys.stderr)
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

