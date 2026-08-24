#!/usr/bin/env python3
"""Resolve a Sledujteto file id to its range-enabled video stream URL."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)
REFERER = "https://www.sledujteto.cz/"


def _add_file_link(host: str, file_id: int, timeout: int = 15) -> dict | None:
    url = f"https://{host}.sledujteto.cz/services/add-file-link"
    payload = json.dumps({"params": {"id": int(file_id)}}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "requested-with-angularjs": "true",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode())
                return None if result.get("error") else result
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(2)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            return None
    return None


def resolve(file_id: int, cdn_host: str | None = None) -> str | None:
    """Return a stream URL signed by the CDN host that stores the file."""
    if cdn_host and cdn_host != "www":
        data = _add_file_link(cdn_host, file_id)
        if data and data.get("video_url"):
            return data["video_url"]

    data = _add_file_link("www", file_id)
    if not data or not data.get("video_url"):
        return None
    video_url = data["video_url"]
    host = (urlparse(video_url).hostname or "").split(".")[0]
    if host in ("", "www"):
        return video_url
    signed = _add_file_link(host, file_id)
    return signed.get("video_url") if signed and signed.get("video_url") else video_url
