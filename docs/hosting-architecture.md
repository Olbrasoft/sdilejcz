# Hosting Architecture

The sync is designed to run on GitHub Actions, matching the `prehrajto-sync`
operational model.

Each workflow run:

1. Checks out this repository.
2. Installs Python dependencies.
3. Picks the next missing film from the backlog.
4. Resolves a live SK Torrent CDN edge.
5. Downloads the file into `/tmp`.
6. Uploads it to Sdilej.cz.
7. Commits `state/uploaded.json` and `state/sync.log`.

GitHub hosted runners have enough disk for one 720p film at a time. If SK Torrent
blocks GitHub runner IP ranges, switch the workflow to a Czech or EU
self-hosted runner.

