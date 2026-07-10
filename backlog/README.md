# Backlog

`sktorrent-films.jsonl` is expected to use the same row shape as
`prehrajto-sync`:

```json
{"id": 56413, "title": "Lví král", "year": 1994, "quality": "720p", "cr_film_id": 49, "cr_slug": "lvi-kral", "url": "https://online.sktorrent.eu/media/videos//h264/56413_720p.mp4"}
```

The CDN hostname may be a placeholder. `src/resolve_sktorrent_cdn.py` probes
`online1..30.sktorrent.eu` and returns the first live edge.

