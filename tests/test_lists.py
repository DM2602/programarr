"""List sources and matching: FlixPatrol like the script, MDBList, TMDB, Trakt, corrections, exclusions, API."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cache, config, db, settings
from app.lists import exclusions, flixpatrol, runs, scrape
from app.lists.base import RawEntry, SourceError
from app.media import mdblist, normalize, store
from tests.fakes.lists import EXPECTED

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "flixpatrol"
NETFLIX_CH = {"platform": "netflix", "region": "switzerland", "sections": ["movies", "shows"], "limit": 10}


@pytest.fixture
def lists(world):
    world.fill_lists()
    scrape.reset()
    return world


def entries_of(run_id):
    return db.get().query(
        "SELECT e.*, m.tmdb_id FROM parser_entries e LEFT JOIN media_cache m ON m.id=e.media_id "
        "WHERE run_id=? ORDER BY position", (run_id,))


# -- FlixPatrol pages ------------------------------------------------------------------------------

def test_top10_page_sections_and_rows():
    sections = flixpatrol.parse_top10((FIXTURES / "top10-netflix-switzerland.html").read_text(encoding="utf-8"))
    assert list(sections) == ["movies", "shows", "kids-movies", "kids-shows"]
    assert all(len(rows) == 10 for rows in sections.values())
    movies = sections["movies"]
    assert [row["rank"] for row in movies] == list(range(1, 11))
    assert movies[0] == {"rank": 1, "title": "Guarding Stars", "path": "/title/guarding-stars/", "change": 0,
                         "days": 5, "points": None, "original": True}
    assert (movies[3]["change"], movies[4]["change"], movies[4]["days"]) == (1, -1, 51)
    assert movies[2]["path"] == "/title/heartland-2026/" and movies[7]["title"] == "Russell Brand: Open Secret"
    assert sections["shows"][4]["title"] == "The Empress"
    assert sections["kids-movies"][7]["days"] is None  # empty days cell


def test_world_overview_page():
    platforms = flixpatrol.parse_overview((FIXTURES / "top10-world.html").read_text(encoding="utf-8"))
    assert set(platforms) >= {"netflix", "disney", "amazon-prime", "hbo-max", "apple-tv", "paramount-plus"}
    assert all(len(rows) == 10 for sections in platforms.values() for rows in sections.values())
    assert list(platforms["google"]) == ["movies"]
    netflix = platforms["netflix"]["movies"]
    assert netflix[0] == {"rank": 1, "title": "Guarding Stars", "path": "/title/guarding-stars/", "change": None,
                          "days": None, "points": 882, "original": True}
    assert platforms["disney"]["shows"][0]["title"] == "Percy Jackson and the Olympians"
    # The per-country tables further down share the headings' wording but are not TOP 10 sections.
    assert flixpatrol.section_of("TOP Movies on Netflix on November 24, 2026") == "movies"
    assert flixpatrol.section_of("TOP Movies and TV Shows on Netflix on November 24, 2026 by country") is None


def test_heading_rules():
    assert flixpatrol.section_of("TOP 10 Movies") == "movies"
    assert flixpatrol.section_of("TOP 10 TV Shows") == "shows"
    assert flixpatrol.section_of("TOP 10 Kids TV Shows") == "kids-shows"
    assert flixpatrol.section_of("TOP 10 Overall") == "overall"
    # The tables by day below the TOP 10 only hold posters; their headings must not count.
    assert flixpatrol.section_of("Top ranked Movies") is None
    assert flixpatrol.section_of("TOP Movies and TV Shows on Netflix in Switzerland on November 23, 2026 by day") is None
    assert flixpatrol.section_of("Netflix TOP 10 in Switzerland on November 23, 2026") is None


def test_title_pages_give_kind_and_year():
    empress = flixpatrol.parse_title((FIXTURES / "title-the-empress.html").read_text(encoding="utf-8"))
    assert empress == {"kind": "show", "year": 2022, "premiere": "2022-09-29", "country": "DE"}
    grinch = flixpatrol.parse_title((FIXTURES / "title-the-grinch.html").read_text(encoding="utf-8"))
    assert grinch == {"kind": "movie", "year": 2018, "premiere": "2018-11-08", "country": "US"}
    # JSON-LD says "Movie" for shows too; the info grid wins. Without a date the address gives the year,
    # but FlixPatrol's 1900 means unknown.
    assert flixpatrol.parse_title("<html></html>", "/title/heartland-2026/")["year"] == 2026
    assert flixpatrol.parse_title("<html></html>", "/title/spike-1900/")["year"] is None
    linked = flixpatrol.parse_title('<a href="https://www.imdb.com/title/tt2709692/">IMDb</a>', "/title/x/")
    assert linked["imdb"] == "tt2709692"


def test_match_keys_and_fingerprints():
    assert normalize.match_key("Russell Brand: Open Secret") == normalize.match_key("Russell Brand – Open Secret")
    assert normalize.match_key("Alicia's Law") == "aliciaslaw"
    assert normalize.match_key("Tom & Jerry") == normalize.match_key("Tom and Jerry")
    assert normalize.match_key("Amélie") == "amelie"
    entry = RawEntry(1, "The Body", 2026, "show")
    assert normalize.fingerprint(entry) == "show|thebody|2026"
    assert normalize.fingerprint(RawEntry(1, "x", None, None, ids={"imdb": "tt1"})) == "any|imdb:tt1"


# -- matching like the script -------------------------------------------------------------------------

async def test_flixpatrol_netflix_switzerland_like_script(lists):
    result = await runs.preview("flixpatrol", runs.prepare("flixpatrol", NETFLIX_CH))
    run = db.get().one("SELECT * FROM parser_runs WHERE id=?", (result["run"],))
    assert run["status"] == "ok" and run["label"] == "Netflix Switzerland – Movies, TV Shows"
    rows = entries_of(result["run"])
    assert [row["position"] for row in rows] == list(range(1, 21))
    assert [row["raw_kind"] for row in rows] == ["movie"] * 10 + ["show"] * 10
    outcome = {row["raw_title"]: (row["status"], row["method"], row["tmdb_id"]) for row in rows}
    assert outcome == EXPECTED
    by_title = {row["raw_title"]: row for row in rows}
    assert by_title["The Empress"]["raw_year"] == 2022  # from the saved title page
    assert by_title["The Grinch"]["raw_year"] == 2018
    killing = db.loads(by_title["How to Make a Killing"]["candidates"])
    assert [c["tmdb_id"] for c in killing[:2]] == [1009, 1010] and by_title["How to Make a Killing"]["error"]
    dog = db.loads(by_title["A Dog's Perfect Christmas"]["candidates"])
    assert dog[0]["tmdb_id"] == 1011 and dog[0]["similarity"] >= 0.6
    grinch_extra = db.loads(by_title["The Grinch"]["extra"])
    assert (grinch_extra["days"], grinch_extra["change"], grinch_extra["original"]) == (51, -1, False)
    assert (run["matched"], run["uncertain"], run["failed"], run["excluded"], run["raw_count"]) == (17, 2, 1, 0, 20)
    assert result["message"] == "Netflix Switzerland – Movies, TV Shows: 17 of 20 matched, 3 unresolved"
    # Ratings come from MDBList in one batch per kind.
    grinch = store.find("movie", 360920)
    assert (grinch["imdb_rating"], grinch["imdb_votes"], grinch["rt_critics"], grinch["rt_audience"]) == (6.3, 140000, 60, 61)
    assert grinch["imdb_id"] == "tt2709692" and grinch["ratings_at"]
    assert [call for call in lists.mdblist.calls if call.startswith("POST")] == ["POST /tmdb/movie", "POST /tmdb/show"]


async def test_second_run_uses_caches(lists):
    params = runs.prepare("flixpatrol", NETFLIX_CH)
    await runs.preview("flixpatrol", params)
    pages, searches, mdb = len(lists.flixpatrol.requests), len(lists.tmdb.searches), len(lists.mdblist.calls)
    assert pages == 21  # TOP 10 page and 20 title pages
    result = await runs.preview("flixpatrol", params)
    assert len(lists.flixpatrol.requests) == pages and len(lists.tmdb.searches) == searches
    assert len(lists.mdblist.calls) == mdb  # ratings are fresh
    assert {row["raw_title"]: row["status"] for row in entries_of(result["run"])} == \
        {title: value[0] for title, value in EXPECTED.items()}


async def test_world_lists_from_the_overview(lists):
    params = runs.prepare("flixpatrol", {"platform": "disney", "region": "world", "sections": ["movies", "shows"],
                                         "limit": 10})
    result = await runs.preview("flixpatrol", params)
    run = db.get().one("SELECT * FROM parser_runs WHERE id=?", (result["run"],))
    assert run["label"] == "Disney+ World – Movies, TV Shows" and run["status"] == "ok"
    rows = entries_of(result["run"])
    assert len(rows) == 20 and rows[10]["raw_title"] == "Percy Jackson and the Olympians"
    assert db.loads(rows[10]["extra"])["points"] == 665
    assert lists.flixpatrol.requests[0] == "/top10/"
    # Same overview for the next platform; kids lists are not in it, the platform's world page is tried.
    lists.flixpatrol.requests.clear()
    netflix = await runs.preview("flixpatrol", runs.prepare("flixpatrol", {
        "platform": "netflix", "region": "world", "sections": ["movies", "kids-movies"], "limit": 2}))
    assert lists.flixpatrol.requests[0] == "/top10/netflix/world/"
    notes = db.loads(db.get().scalar("SELECT notes FROM parser_runs WHERE id=?", (netflix["run"],)))
    assert notes == ["Not on this page: Kids Movies. Found: Movies, TV Shows"]
    hulu = await runs.preview("flixpatrol", runs.prepare("flixpatrol", {
        "platform": "hulu", "region": "world", "sections": ["movies"], "limit": 10}))
    assert hulu["outcome"] == "error" and "hulu/world" in hulu["message"]


async def test_kids_sections_and_limits(lists):
    params = runs.prepare("flixpatrol", {"platform": "netflix", "region": "switzerland",
                                         "sections": ["kids-shows", "movies"], "limit": 3})
    assert params["sections"] == ["movies", "kids-shows"]  # in the order of the page
    result = await runs.preview("flixpatrol", params)
    rows = entries_of(result["run"])
    assert [row["raw_title"] for row in rows] == ["Guarding Stars", "Steps", "Heartland", "Doozie's Doghouse",
                                                 "Win the Mall", "Hot Wheels Let's Race: Attack of the T-Rex"]
    assert [db.loads(row["extra"])["section"] for row in rows] == ["movies"] * 3 + ["kids-shows"] * 3


async def test_flixpatrol_errors_and_cloudflare(lists):
    with pytest.raises(SourceError, match="Region"):
        runs.prepare("flixpatrol", {**NETFLIX_CH, "region": "United States!"})
    with pytest.raises(SourceError, match="Sections"):
        runs.prepare("flixpatrol", {**NETFLIX_CH, "sections": []})
    with pytest.raises(SourceError, match="1–10"):
        runs.prepare("flixpatrol", {**NETFLIX_CH, "limit": 50})
    missing = await runs.preview("flixpatrol", runs.prepare("flixpatrol", {**NETFLIX_CH, "region": "narnia"}))
    assert missing["outcome"] == "error" and "no TOP 10 page" in missing["message"]
    overall = await runs.preview("flixpatrol", runs.prepare("flixpatrol", {**NETFLIX_CH, "sections": ["overall"]}))
    assert overall["outcome"] == "error" and "Found: Movies, TV Shows, Kids Movies, Kids TV Shows" in overall["message"]
    lists.flixpatrol.challenge = True
    cache.drop("fp:")
    blocked = await runs.preview("flixpatrol", runs.prepare("flixpatrol", NETFLIX_CH))
    assert blocked["outcome"] == "error" and "FlareSolverr" in blocked["message"]
    settings.set("flaresolverr_url", "https://flaresolverr.test")
    solved = await runs.preview("flixpatrol", runs.prepare("flixpatrol", NETFLIX_CH))
    assert solved["outcome"] == "ok" and lists.flare.calls == 1  # the clearance cookie is reused afterwards
    assert db.get().scalar("SELECT COUNT(*) FROM parser_runs WHERE status='error'") == 3


# -- other sources ----------------------------------------------------------------------------------

async def test_mdblist_list_trusts_imdb_over_wrong_tmdb(lists):
    result = await runs.preview("mdblist", runs.prepare("mdblist", {"list": "https://mdblist.com/lists/dominik/netflix-ch",
                                                                     "limit": 10}))
    rows = entries_of(result["run"])
    # The list's order counts, not MDBList's popularity rank; the place counts among movies or shows.
    assert [(row["raw_title"], row["status"], row["method"], row["tmdb_id"], db.loads(row["extra"])["rank"])
            for row in rows] == [
        ("The Grinch", "matched", "id", 360920, 1),
        ("Guarding Stars", "matched", "id", 1001, 2),
        ("Lupin", "matched", "id", 96677, 1),
    ]
    assert lists.tmdb.searches == []  # IDs, no title search
    # The run carries the list's title from MDBList, and so does every later description.
    assert db.get().scalar("SELECT label FROM parser_runs WHERE id=?", (result["run"],)) == "Netflix CH"
    assert runs.adapter("mdblist").describe({"list": "dominik/netflix-ch"}) == "Netflix CH"


async def test_trakt_and_tmdb_sources(lists):
    trending = await runs.preview("trakt", runs.prepare("trakt", {"chart": "trending", "type": "movies", "limit": 2}))
    assert [row["tmdb_id"] for row in entries_of(trending["run"])] == [1001, 360920]
    user_list = await runs.preview("trakt", runs.prepare("trakt", {
        "chart": "list", "type": "both", "list": "https://trakt.tv/users/dominik/lists/christmas", "limit": 20}))
    assert [(row["raw_kind"], row["tmdb_id"]) for row in entries_of(user_list["run"])] == [("movie", 360920), ("show", 96677)]
    shows_only = await runs.preview("trakt", runs.prepare("trakt", {"chart": "list", "type": "shows",
                                                                    "list": "dominik/christmas", "limit": 20}))
    assert [row["tmdb_id"] for row in entries_of(shows_only["run"])] == [96677]
    with pytest.raises(SourceError, match="per type"):
        runs.prepare("trakt", {"chart": "popular", "type": "both", "limit": 5})
    chart = await runs.preview("tmdb", runs.prepare("tmdb", {"chart": "trending-movie-week", "limit": 2}))
    assert [row["tmdb_id"] for row in entries_of(chart["run"])] == [1001, 1002]
    tmdb_list = await runs.preview("tmdb", runs.prepare("tmdb", {"chart": "list",
                                                                "list": "https://www.themoviedb.org/list/8001", "limit": 20}))
    assert [(row["raw_kind"], row["tmdb_id"]) for row in entries_of(tmdb_list["run"])] == [("movie", 360920), ("show", 2005)]
    settings.set("trakt_client_id", "")
    with pytest.raises(SourceError, match="Trakt client ID"):
        runs.prepare("trakt", {"chart": "trending", "type": "movies", "limit": 2})


async def test_ratings_one_by_one_when_batches_are_refused(lists):
    lists.mdblist.batch = False
    media_id = await store.ensure("show", 96677)
    assert await mdblist.refresh_ratings([media_id]) == 1
    row = store.get(media_id)
    assert (row["imdb_rating"], row["rt_critics"], row["rt_audience"]) == (7.5, 95, 89)
    assert lists.mdblist.calls[-2:] == ["POST /tmdb/show", "GET /tmdb/show/96677"]
    other = await store.ensure("show", 2003)
    await mdblist.refresh_ratings([other])
    assert lists.mdblist.calls[-1] == "GET /tmdb/show/2003"  # no second batch attempt today
    assert await mdblist.refresh_ratings([media_id, other]) == 0  # fresh


async def test_ratings_when_the_batch_address_is_unknown(lists, monkeypatch):
    """A 404 or an unreadable batch answer must not leave the titles without ratings for a week."""
    original = mdblist.request

    async def no_batch(method, path, params=None, body=None, key=None):
        if method == "POST":
            return None  # 404
        return await original(method, path, params, body, key)

    monkeypatch.setattr(mdblist, "request", no_batch)
    media_id = await store.ensure("movie", 360920)
    await mdblist.refresh_ratings([media_id])
    assert store.get(media_id)["imdb_rating"] == 6.3 and cache.get("mdblist:single")
    unknown = await store.ensure("movie", 1002)  # not on MDBList: asked once, then left for a week
    await mdblist.refresh_ratings([unknown])
    assert store.get(unknown)["ratings_at"] and store.get(unknown)["imdb_rating"] is None


# -- corrections and exclusions ---------------------------------------------------------------------

async def test_fix_ignore_exclude_and_undo(lists):
    params = runs.prepare("flixpatrol", NETFLIX_CH)
    first = (await runs.preview("flixpatrol", params))["run"]
    problems = {row["title"]: row for row in runs.problems()}
    assert set(problems) == {"How to Make a Killing", "A Dog's Perfect Christmas", "The Altruists"}
    assert problems["The Altruists"]["lists"][0]["label"] == "Netflix Switzerland – Movies, TV Shows"
    assert runs.problem_count() == 3
    killing = problems["How to Make a Killing"]
    result = await runs.fix(killing["id"], "movie", 1010)
    assert result["changed"] == 1
    fixed = db.get().one("SELECT * FROM parser_entries WHERE id=?", (killing["id"],))
    assert (fixed["status"], fixed["method"]) == ("matched", "fixed")
    runs.ignore(problems["The Altruists"]["id"])
    assert runs.problem_count() == 1
    run = db.get().one("SELECT * FROM parser_runs WHERE id=?", (first,))
    assert (run["matched"], run["uncertain"], run["failed"], run["excluded"]) == (18, 1, 0, 1)
    # Exclude a matched title: excluded now and in the next run.
    grinch = store.find("movie", 360920)
    await exclusions.exclude(None, None, grinch["id"], "Seen it")
    assert lists.radarr.excluded == [360920]  # on Radarr's import list exclusions too
    second = (await runs.preview("flixpatrol", params))["run"]
    outcome = {row["raw_title"]: (row["status"], row["method"], row["tmdb_id"]) for row in entries_of(second)}
    assert outcome["How to Make a Killing"] == ("matched", "fixed", 1010)
    assert outcome["The Altruists"] == ("excluded", "ignored", None)
    assert outcome["The Grinch"] == ("excluded", "exact", 360920)
    assert [(row["title"], row["service"]) for row in exclusions.exclusions()] == [("The Grinch", "radarr")]
    # Undo: the exclusion (off Radarr's list too), then the corrections.
    await exclusions.unexclude(exclusions.exclusions()[0]["id"])
    assert lists.radarr.excluded == []
    assert db.get().scalar("SELECT COUNT(*) FROM parser_entries WHERE status='excluded' AND method='exact'") == 0
    overrides = {row["title"]: row for row in runs.overrides()}
    assert overrides["How to Make a Killing"]["media"]["tmdb_id"] == 1010 and overrides["The Altruists"]["ignore"]
    assert await runs.remove_override(overrides["The Altruists"]["id"]) == 2
    assert db.get().scalar("SELECT COUNT(*) FROM parser_entries WHERE raw_title='The Altruists' AND status='failed'") == 2
    assert runs.problem_count() == 2


async def test_prune_keeps_the_last_run_of_each_list(lists):
    params = runs.prepare("tmdb", {"chart": "trending-movie-week", "limit": 2})
    old = (await runs.preview("tmdb", params))["run"]
    newer = (await runs.preview("tmdb", params))["run"]
    db.get().execute("UPDATE parser_runs SET started_at=? ", (db.now() - 90 * 86400,))
    assert runs.prune() == 1
    assert [row["id"] for row in db.get().query("SELECT id FROM parser_runs")] == [newer]
    assert db.get().scalar("SELECT COUNT(*) FROM parser_entries WHERE run_id=?", (old,)) == 0


# -- API ------------------------------------------------------------------------------------------------

@pytest.fixture
def client(lists):
    config.SCHEDULER = False
    from app.main import create_app

    with TestClient(create_app(), headers={"X-Programarr": "1"}) as test_client:
        yield test_client


def wait_job(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("Job runs too long")


def test_lists_api(client, lists):
    sources = {source["key"]: source for source in client.get("/api/lists/sources").json()["sources"]}
    assert list(sources) == ["flixpatrol", "mdblist", "tmdb", "trakt"]
    assert all(source["missing"] is None for source in sources.values())
    fields = {field["key"]: field for field in sources["trakt"]["fields"]}
    assert fields["period"]["when"] == {"chart": ["watched", "played", "collected"]}
    bad = client.post("/api/lists/preview", json={"source": "flixpatrol", "params": {**NETFLIX_CH, "limit": 0}})
    assert bad.status_code == 400 and bad.json()["detail"] == "Titles per section: 1–10"
    started = client.post("/api/lists/preview", json={"source": "flixpatrol", "params": NETFLIX_CH}).json()
    job = wait_job(client, started["job"])
    assert job["status"] == "done" and job["kind"] == "preview" and job["result"]["outcome"] == "ok"
    run_id = job["result"]["run"]
    runs_page = client.get("/api/lists/runs").json()["runs"]
    assert runs_page[0]["id"] == run_id and runs_page[0]["matched"] == 17
    detail = client.get(f"/api/lists/runs/{run_id}").json()
    assert detail["params"] == NETFLIX_CH and detail["notes"] == [] and len(detail["entries"]) == 20
    grinch = next(entry for entry in detail["entries"] if entry["title"] == "The Grinch")
    assert grinch["media"]["poster"] == "/img/tmdb/pmovie360920.jpg" and grinch["media"]["imdb_rating"] == 6.3
    assert grinch["url"] == "https://flixpatrol.test/title/the-grinch/" and grinch["plex_key"] is None
    image = client.get(grinch["media"]["poster"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert client.get("/img/tmdb/secret.txt").headers["content-type"].startswith("image/svg")
    problems = client.get("/api/lists/problems").json()["rows"]
    assert {row["title"] for row in problems} == {"How to Make a Killing", "A Dog's Perfect Christmas", "The Altruists"}
    assert client.get("/api/overview").json()["todo"]["match_open"] == 3
    dog = next(row for row in problems if row["title"] == "A Dog's Perfect Christmas")
    assert dog["candidates"][0]["tmdb_id"] == 1011 and dog["candidates"][0]["poster"]
    search = client.get("/api/media/search", params={"q": "perfect dog christmas", "kind": "movie"}).json()["results"]
    assert search[0]["tmdb_id"] == 1011
    fixed = client.post(f"/api/lists/entries/{dog['id']}/fix", json={"kind": "movie", "tmdb_id": 1011}).json()
    assert fixed["changed"] == 1 and fixed["message"] == "Match saved"
    assert client.post(f"/api/lists/entries/{dog['id']}/fix", json={"kind": "movie", "tmdb_id": 424242}).status_code == 400
    matches = client.get("/api/lists/matches", params={"status": "matched", "q": "dog"}).json()
    assert matches["total"] == 1 and matches["rows"][0]["method"] == "fixed"
    assert client.get("/api/lists/matches").json()["total"] == 20
    overrides = client.get("/api/lists/overrides").json()["rows"]
    assert overrides[0]["title"] == "A Dog's Perfect Christmas" and overrides[0]["media"]["tmdb_id"] == 1011
    excluded = client.post("/api/exclusions", json={"kind": "movie", "tmdb_id": 1001, "reason": "Not for us"})
    assert excluded.json()["message"] == "Excluded"
    rows = client.get("/api/exclusions").json()["rows"]
    assert rows[0]["media"]["title"] == "Guarding Stars" and rows[0]["reason"] == "Not for us"
    assert client.get(f"/api/lists/runs/{run_id}").json()["excluded"] == 1
    assert client.delete(f"/api/exclusions/media/{rows[0]['media']['id']}").json()["ok"]
    assert client.delete(f"/api/exclusions/media/{rows[0]['media']['id']}").status_code == 404
    assert client.get("/api/status").json()["counts"]["match_open"] == 2
    assert client.get("/api/lists/matches", params={"status": "open"}).json()["total"] == 2
    assert client.post(f"/api/lists/entries/{dog['id']}/reset").json()["changed"] == 1
    assert client.post(f"/api/lists/entries/{dog['id']}/reset").status_code == 400
    assert client.get("/api/lists/overrides").json()["rows"] == []
    again = wait_job(client, client.post(f"/api/lists/runs/{run_id}/again").json()["job"])
    assert again["result"]["run"] > run_id
    assert client.get(f"/api/lists/runs/{again['result']['run']}").json()["previous"] == run_id
    mine = client.get("/api/lists/mdblist/mine").json()["lists"]
    assert mine == [{"id": 11, "name": "Netflix CH", "slug": "netflix-ch", "user": "dominik", "items": 3,
                     "dynamic": False, "mediatype": "movie"}]
    media = client.get(f"/api/media/{grinch['media']['id']}").json()
    assert media["title"] == "The Grinch" and client.get("/api/media/999999").status_code == 404


def test_connection_tests_and_settings(client, lists):
    mdb = client.post("/api/test/mdblist", json={}).json()
    assert mdb["ok"] and mdb["message"].startswith("Connected as dominik")
    assert client.post("/api/test/mdblist", json={"value": "wrong"}).json() == {"ok": False, "message": "Invalid API key"}
    assert client.post("/api/test/trakt", json={}).json() == {"ok": True, "message": "Trakt client ID is valid"}
    assert not client.post("/api/test/trakt", json={"value": "nope"}).json()["ok"]
    values = client.get("/api/settings").json()["values"]
    assert values["mdblist_key"] is True and values["trakt_client_id"] is True and values["flixpatrol_delay"] == 0
    saved = client.post("/api/settings", json={"ratings_days": 3, "flixpatrol_base": "https://flixpatrol.test/",
                                               "mdblist_key": False}).json()
    assert set(saved["saved"]) == {"ratings_days", "flixpatrol_base", "mdblist_key"}
    assert settings.get("flixpatrol_base") == "https://flixpatrol.test" and settings.get("mdblist_key") == ""
    sources = {source["key"]: source for source in client.get("/api/lists/sources").json()["sources"]}
    assert sources["mdblist"]["missing"] == "Set an MDBList API key under Settings › Lists"
