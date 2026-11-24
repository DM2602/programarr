# Programarr

Self-hosted companion for Plex: posters now, content flows, collections and the home screen later.
Plex stays the source of truth. Posters are written as files next to the media ("Use local assets"),
nothing is uploaded to or locked in Plex.

Status: phase 2. Posters (formerly Litfass) plus list sources: FlixPatrol, MDBList, TMDB and Trakt
are read and matched to TMDB as previews, with a match log, corrections and exclusions. Nothing from
lists is written to Plex yet; that comes with content flows (phase 3).

## Run with Docker

```sh
cp .env.example .env      # set MEDIA_PATH, PUID/PGID, optionally APP_PASSWORD
docker compose up -d --build
```

Open `http://<host>:8796` and follow the setup on the dashboard:

1. Settings › Plex: address, token, libraries, path mapping if Plex sees the media under another path
2. Settings › Sources: MediUX token, ThePosterDB account, optionally TMDB and fanart.tv
3. Settings › Automation: preferred designers
4. Dashboard: Finish and sync
5. Optional, for lists: Settings › Lists (MDBList API key for ratings, Trakt client ID), TMDB key under Sources

In Plex, turn on "Use local assets" for each library (Advanced).

### Automation

Off by default. While it is off, Programarr syncs the library but writes posters only when you click;
scheduled set checks are skipped. Turn it on under Settings › Automation.

### Access

- `APP_PASSWORD` set: the web app asks for it.
- Not set: put Programarr behind a proxy with sign-in (e.g. Pangolin SSO).
- Webhooks (`/api/hooks/…`) are protected by their token and need an exception in the proxy.

### Lists

Lists › Lists reads a source once and shows how every entry was matched. FlixPatrol titles are
matched like the FlixPatrol script did: exact title and year, then year ±1, then the title without a
year; TMDB's alternative titles count too. Anything else lands in Lists › Match log with candidates.
A fix or an ignore holds for future runs; excluded titles never appear in a list.

### Data

Database, archive and logs live in `/data` (`./data` in the compose file). Back up that folder.

## Development

Backend (Python 3.13):

```sh
pip install -r requirements-dev.txt
python -m tests.demo            # simulated Plex and sources on :8796
pytest
```

Web app (Node 22):

```sh
cd frontend
npm ci
npm run dev                     # :5173, API calls go to :8796
npm run build                   # into app/web/dist, served by FastAPI
```

After API changes, refresh the types:

```sh
python -m app.openapi > frontend/openapi.json
cd frontend && npm run types
```

Browser test (needs a build and Chrome or Playwright's Chromium):

```sh
python tests/e2e/run_e2e.py
```

## License

GPL-3.0
