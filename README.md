# Programarr

Self-hosted companion for Plex: posters now, content flows, collections and the home screen later.
Plex stays the source of truth. Posters are written as files next to the media ("Use local assets"),
nothing is uploaded to or locked in Plex.

Status: phase 3. Posters (formerly Litfass), list sources (FlixPatrol, MDBList, TMDB, Trakt) matched
to TMDB with a match log, corrections and exclusions, and flows that hand list titles to Radarr and
Sonarr. Collections and the home screen follow in phase 4.

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
6. Optional, for flows: Settings › Services (Radarr and Sonarr address and API key)

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

Lists › Exclusions is kept in step with the import list exclusions of Radarr (movies) and Sonarr
(shows) in both directions: a title excluded there is excluded here, an exclusion here is written
there, and removing it on either side removes it on the other. The sync runs before every flow run,
every 6 hours and with Sync on the page. If Radarr or Sonarr cannot be reached, the change waits for
the next sync.

### Flows

Flows › Flows connects sources, filters (rating, release, genre, country, language, list position,
seasons, movie or show, in Plex) and Radarr or Sonarr in an editor. A filter sends a title to Match or
No match; rating, release, genre, country, language, list position and season filters can have
several outputs (e.g. one per genre or rating tier), each with its own rule. A title that fits several
goes to the first or to each of them, as set on the filter. Radarr and Sonarr add new titles with the
profile, root folder and tags of their node, at most the set number per run. Titles in Plex are left
alone, titles Radarr or Sonarr already have only get the missing tags, and excluded titles never get
through. Sonarr needs a TVDB ID and Sonarr v4.

New flows run as a dry run: each run shows what would be added, title by title, without writing
anything to Radarr or Sonarr. "Go live" switches a flow to real runs. An output can compare its titles with MDBList lists,
e.g. the import lists in Radarr and Sonarr, until the flow gives the same result. Flows run by hand,
on an interval (`every:6h`) or by cron (`0 6 * * *`, container time zone). Runs are kept 60 days
(Settings › Lists), the last run of every flow stays.

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
