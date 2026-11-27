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

With automation on, Programarr also keeps the folders and Plex in sync:

- A poster selected or uploaded in Plex (Plex locks it) is saved as the file in the folder, then unlocked.
- A poster file added or replaced in the folder is read by Plex.
- Titles without a file get the poster Plex shows as file, an hour after they appear (Settings › Files:
  off, movies, or movies and shows). Titles under Review are left alone.
- Every poster file has a copy in `/data/backup`, kept by TMDB, TVDB or IMDb ID. A deleted poster is
  restored from it, also when a title is deleted and downloaded again; a linked set is linked again.
  "Remove file" on a title removes the copy too.

Match existing › Import from Plex does this for the whole library at once.

What reads the disks: the sync with Plex (Settings › Automation › Schedule, default every 15 minutes)
asks Plex and reads only the folders of titles that are new or that Plex changed. All folders are read
once a day ("Check all folders every", 0 turns it off). With the Sonarr and Radarr webhooks
(Settings › Webhooks; On File Import, On File Upgrade, On Rename) a title's folder is read right after
an import, so a poster Radarr deleted with the old release is restored within two minutes; with them,
a sync every few hours is enough.

### Access

- `APP_PASSWORD` set: the web app asks for it.
- Not set: put Programarr behind a proxy with sign-in (e.g. Pangolin SSO).
- Webhooks (`/api/hooks/…`) are protected by their token and need an exception in the proxy.

### Lists

Lists › Lists reads a source once and shows how every entry was matched. FlixPatrol titles are
matched like the FlixPatrol script did: exact title and year, then year ±1, then the title without a
year; TMDB's alternative titles count too. Anything else lands in Lists › Match log with candidates.
A fix or a skip holds for future runs; excluded titles never appear in a list.

Lists › Exclusions is kept in sync with the import list exclusions of Radarr (movies) and Sonarr
(shows) in both directions: a title excluded there is excluded here, an exclusion here is written
there, and removing it on either side removes it on the other. The sync runs before every flow run,
every 6 hours and with Sync on the page. If Radarr or Sonarr cannot be reached, the change stays
pending until the next sync.

### Flows

Flows › Flows connects sources, filters (rating, release, genre, country, language, list position,
seasons, movie or show, in Plex) and Radarr or Sonarr in an editor. A filter sends a title to Match or
No match; rating, release, genre, country, language, list position and season filters can have
several outputs (e.g. one per genre or rating tier), each with its own rule. A title matching several
outputs goes to the first or to all of them (Use first, Use all on the filter). Radarr and Sonarr add new titles with the
profile, root folder and tags of their node, at most the set number per run. Titles already in Plex,
Radarr or Sonarr are left as they are, tags included: tags only go on with a new title. Excluded titles
never get through. Sonarr needs Sonarr v4 and a TVDB ID: from TMDB, or where TMDB has none, from the
list itself, from MDBList or from Sonarr's own lookup by TMDB or IMDb ID.

New flows run as a dry run: each run shows what would be added, title by title, without writing
anything to Radarr or Sonarr. "Go live" switches a flow to real runs. An output can compare its titles with MDBList lists,
e.g. the import lists in Radarr and Sonarr, until the flow gives the same result. Flows run manually,
on an interval (`every:6h`) or by cron (`0 6 * * *`, container time zone). Runs are kept 60 days
(Settings › Lists), the last run of every flow stays.

### Dashboard

To do (Review, pending seasons, posters Plex does not show, unresolved matches, failed flows), recently
set posters, every flow with its last run and MDBList comparison, titles added by live runs, library
facts, the schedule of the regular jobs and recent jobs.

### Data

Database, archive, poster backup and logs live in `/data` (`./data` in the compose file). Back up that folder.

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
