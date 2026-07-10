# sdilejcz

Automated mirror from `sktorrent.eu` to `sdilej.cz`.

The shape intentionally follows `prehrajto-sync`: pick the next missing film from
a JSONL backlog, resolve a live SK Torrent CDN edge, download the video into
`/tmp`, upload it to Sdilej.cz, and append the result to `state/uploaded.json`.

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export SDILEJ_EMAIL=...
export SDILEJ_PASSWORD=...
python src/sdilej_upload.py /path/to/video.mp4 "Film Name (2026) CZ.mp4"
```

## Batch Sync

```bash
export SDILEJ_EMAIL=...
export SDILEJ_PASSWORD=...
python src/sync_batch.py --count 1
```

Inputs:

- `backlog/sktorrent-films.jsonl` - candidate films, same schema as
  `prehrajto-sync`.
- `state/uploaded.json` - successful uploads and failures.

GitHub Actions expects these repository secrets:

- `SDILEJ_EMAIL`
- `SDILEJ_PASSWORD`

The scheduled workflow runs every 15 minutes, but uses a concurrency lock and a
320-minute runtime window. In practice that means one runner keeps uploading
films sequentially for most of the 6-hour GitHub Actions limit, and the next
scheduled run takes over when the current one finishes.

## Upload Flow

Sdilej.cz uses the Blueimp jQuery File Upload stack. The browser page posts to
`https://uploadweb2.sdilej.cz/upload/index.php` with:

- session cookies from `https://sdilej.cz`
- form field `user_id`
- file field `files[]`
- optional `Content-Range` header for chunked uploads

See `docs/upload-flow.md` for the observed flow.
