# sdilejcz

Automated mirror of the playable `ceskarepublika.wiki` film catalog to
`sdilej.cz`.

The shape intentionally follows `prehrajto-sync`: pick the next missing film
from a JSONL backlog, resolve the best available provider, download the video
into `/tmp`, upload it to Sdilej.cz, and append the result to
`state/uploaded.json`.

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

- `backlog/catalog-films.jsonl.gz` - all films currently visible on
  `ceskarepublika.wiki`, exported read-only from production with Přehraj.to,
  SK Torrent, and Sledujteto source fallbacks.
- `backlog/sktorrent-films.jsonl` - candidate films, same schema as
  `prehrajto-sync`, retained as a legacy fallback.
- `state/uploaded.json` - successful uploads and failures.

GitHub Actions expects these repository secrets:

- `SDILEJ_EMAIL`
- `SDILEJ_PASSWORD`
- `CZ_PROXY_URL`
- `CZ_PROXY_KEY`

The scheduled workflow runs every 15 minutes, but uses a concurrency lock and a
320-minute runtime window. Before starting four parallel upload workers it
reconciles the state against completed files shown by the authenticated
Sdilej.cz file manager. Missing or incomplete uploads return to the queue.
Permanent source failures fall through from Přehraj.to to SK Torrent and then
Sledujteto when those alternatives exist.

## Upload Flow

Sdilej.cz uses the Blueimp jQuery File Upload stack. The browser page posts to
`https://uploadweb2.sdilej.cz/upload/index.php` with:

- session cookies from `https://sdilej.cz`
- form field `user_id`
- file field `files[]`
- optional `Content-Range` header for chunked uploads

See `docs/upload-flow.md` for the observed flow.
