"""Flows: schedules, graph checks, filters, dry runs, Radarr and Sonarr outputs, comparison, API."""
import time
from datetime import date, datetime, timedelta

import pytest

from app import db, settings
from app.arr import library
from app.flows import engine, graph, schedule, store
from app.flows.nodes import TYPES, ConfigError, Title
from app.lists import scrape
from tests.test_web import client, wait_job  # noqa: F401 - fixture and helper

NETFLIX_CH = {"platform": "netflix", "region": "switzerland", "sections": ["movies", "shows"], "limit": 10}


@pytest.fixture
def lists(world):
    world.fill_lists()
    scrape.reset()
    library.reset()
    return world


def top10(radarr: dict | None = None, sonarr: dict | None = None) -> dict:
    """Netflix Switzerland, movies to Radarr and shows to Sonarr, like the script and the import lists."""
    return {
        "nodes": [
            {"id": "fp", "type": "flixpatrol", "config": NETFLIX_CH, "position": {"x": 0, "y": 0}},
            {"id": "kind", "type": "type", "config": {}},
            {"id": "radarr", "type": "radarr", "config": {
                "quality_profile": 1, "root_folder": "/data/movies", "tags": ["Top 10"], **(radarr or {})}},
            {"id": "sonarr", "type": "sonarr", "config": {
                "quality_profile": 4, "root_folder": "/data/tv", "tags": ["top-10"], **(sonarr or {})}},
        ],
        "edges": [
            {"source": "fp", "port": "out", "target": "kind"},
            {"source": "kind", "port": "movie", "target": "radarr"},
            {"source": "kind", "port": "show", "target": "sonarr"},
        ],
    }


def trace(run_id: int, node: str) -> dict[str, tuple[str, str]]:
    rows = db.get().query("SELECT t.port, t.detail, m.title FROM flow_trace t JOIN media_cache m ON m.id=t.media_id "
                          "WHERE t.run_id=? AND t.node_id=?", (run_id, node))
    return {row["title"]: (row["port"], row["detail"]) for row in rows}


# -- schedules ---------------------------------------------------------------------------------------

def test_schedules():
    assert schedule.normalise("") is None and schedule.normalise("manual") is None
    assert schedule.normalise(" Every:6H ") == "every:6h"
    assert schedule.describe("every:6h") == "Every 6 hours" and schedule.describe("every:1d") == "Every day"
    assert schedule.describe("0 6 * * *") == "Daily at 06:00"
    assert schedule.describe("30 7 * * mon") == "Mondays at 07:30"
    assert schedule.describe("0 */4 * * *") == "Cron 0 */4 * * *"
    for wrong, message in (("every:5m", "at most every 15 minutes"), ("every 6h", "every:6h"),
                           ("1 2 3", "five fields"), ("61 * * * *", "0–59"), ("0 0 31 2 *", "never comes"),
                           ("0 0 * * funday", "not valid")):
        with pytest.raises(schedule.ScheduleError, match=message):
            schedule.normalise(wrong)
    start = datetime(2026, 11, 24, 21, 13).timestamp()
    assert schedule.next_run("every:6h", start) == start + 6 * 3600
    assert datetime.fromtimestamp(schedule.next_run("0 6 * * *", start)) == datetime(2026, 11, 25, 6, 0)
    assert datetime.fromtimestamp(schedule.next_run("30 */6 * * *", start)) == datetime(2026, 11, 25, 0, 30)
    assert datetime.fromtimestamp(schedule.next_run("0 8 * * 1", start)) == datetime(2026, 11, 30, 8, 0)
    # Day and weekday both set: either one counts, like cron.
    assert datetime.fromtimestamp(schedule.next_run("0 0 1 * 5", start)) == datetime(2026, 11, 27, 0, 0)
    assert schedule.parse_cron("0 0 * * 5-7").weekdays == {0, 5, 6}
    assert schedule.next_run(None, start) is None


# -- graph checks ------------------------------------------------------------------------------------

def test_graph_checks(world):
    checked = graph.check(top10())
    assert checked.ok and checked.order == ["fp", "kind", "radarr", "sonarr"]
    assert checked.configs["radarr"]["tags"] == ["top-10"]  # Radarr's form of a tag
    assert checked.configs["radarr"]["limit"] == 20 and checked.configs["sonarr"]["monitor"] == "all"

    empty = graph.check({"nodes": [], "edges": []})
    assert [p.message for p in empty.problems] == ["Add a source", "Add Radarr or Sonarr"]

    broken = top10(radarr={"root_folder": "", "tags": ["Top 10!"]})
    broken["nodes"].append({"id": "x", "type": "nope"})
    broken["nodes"].append({"id": "r", "type": "rating", "config": {"metric": "imdb", "min": 11}})
    broken["edges"] += [
        {"source": "radarr", "port": "added", "target": "fp"},  # outputs have no ports, sources no input
        {"source": "kind", "port": "maybe", "target": "sonarr"},
        {"source": "gone", "port": "out", "target": "kind"},
    ]
    checked = graph.check(broken)
    problems = {(p.node, p.message) for p in checked.problems}
    assert ("x", "Unknown node type: nope") in problems
    assert ("radarr", "Root folder: choose one") in problems
    assert ("r", "IMDb rating: 0–10") in problems
    assert ("r", "Connect an input") in problems and ("r", "Connect an output") in problems
    assert len(checked.graph["edges"]) == 3  # the broken connections are dropped

    loop = top10()
    loop["nodes"].append({"id": "y", "type": "year", "config": {"mode": "recent", "months": 12}})
    loop["edges"] += [{"source": "kind", "port": "movie", "target": "y"}, {"source": "y", "port": "match", "target": "kind"}]
    checked = graph.check(loop)
    assert [p.message for p in checked.problems] == ["These nodes form a loop: Movie or show, Release"]

    settings.set("radarr_url", "")
    assert ("radarr", "Set up Radarr under Settings › Services") in {(p.node, p.message) for p in graph.check(top10()).problems}


def test_filters(world):
    def title(**media) -> Title:
        row = {"kind": "movie", "title": "X", "genres": "[]", "countries": "[]"}
        row.update(media)
        return Title(1, row, media.pop("rank", None))

    def verdict(kind: str, config: dict, item: Title) -> tuple[str, str]:
        node = TYPES.types[kind]
        result = node.test(node.clean(config), item)
        return result.port, result.detail

    rating = {"metric": "imdb", "min": "7"}
    assert verdict("rating", rating, title(imdb_rating=7.4)) == ("match", "IMDb 7.4 ≥ 7")
    assert verdict("rating", rating, title(imdb_rating=6.9)) == ("no_match", "IMDb 6.9 < 7")
    assert verdict("rating", rating, title()) == ("no_match", "No IMDb rating")
    assert verdict("rating", {**rating, "missing": "match"}, title())[0] == "match"
    assert verdict("rating", {"metric": "rt_critics", "min": 80}, title(rt_critics=85)) == ("match", "RT critics 85% ≥ 80%")
    assert verdict("rating", {"metric": "imdb_votes", "min": 10000}, title(imdb_votes=9000))[1] == "IMDb votes 9'000 < 10'000"
    with pytest.raises(ConfigError, match="0–100"):
        TYPES.types["rating"].clean({"metric": "rt_audience", "min": 120})

    recent = date.today() - timedelta(days=200)
    assert verdict("year", {"mode": "recent", "months": 12}, title(release_date=recent.isoformat()))[0] == "match"
    assert verdict("year", {"mode": "recent", "months": 6}, title(release_date=recent.isoformat()))[0] == "no_match"
    assert verdict("year", {"mode": "recent", "months": 6}, title()) == ("no_match", "No release date")
    assert verdict("year", {"mode": "range", "from": 2020}, title(year=2024)) == ("match", "2024")
    assert verdict("year", {"mode": "range", "from": 2020, "to": 2022}, title(year=2024))[0] == "no_match"
    with pytest.raises(ConfigError, match="first or last year"):
        TYPES.types["year"].clean({"mode": "range"})

    drama_crime = title(genres="[18, 80]")
    assert verdict("genre", {"mode": "any", "genres": ["drama"]}, drama_crime) == ("match", "Crime, Drama")
    assert verdict("genre", {"mode": "all", "genres": ["drama", "comedy"]}, drama_crime)[0] == "no_match"
    assert verdict("genre", {"mode": "only", "genres": ["drama", "crime", "thriller"]}, drama_crime)[0] == "match"
    assert verdict("genre", {"mode": "none", "genres": ["horror", "animation"]}, drama_crime)[0] == "match"
    # Shows have merged genres on TMDB: Sci-Fi & Fantasy counts as science fiction.
    scifi_show = title(kind="show", genres="[10765]")
    assert verdict("genre", {"mode": "any", "genres": ["science-fiction"]}, scifi_show) == ("match", "Sci-Fi & Fantasy")
    with pytest.raises(ConfigError, match="choose at least one"):
        TYPES.types["genre"].clean({"mode": "any", "genres": []})

    korean = title(countries='["KR"]', language="ko")
    assert verdict("country", {"mode": "any", "codes": ["kr", "jp"]}, korean) == ("match", "KR")
    assert verdict("country", {"mode": "none", "codes": ["KR"]}, korean)[0] == "no_match"
    assert verdict("language", {"mode": "none", "codes": ["KO"]}, korean) == ("no_match", "ko")
    assert verdict("language", {"mode": "any", "codes": ["en"]}, title()) == ("no_match", "Unknown")
    with pytest.raises(ConfigError, match="two-letter codes"):
        TYPES.types["country"].clean({"mode": "any", "codes": ["USA"]})

    assert verdict("rank", {"max": 5}, Title(1, {"kind": "movie"}, 3)) == ("match", "#3")
    assert verdict("rank", {"max": 5}, Title(1, {"kind": "movie"}, 8))[0] == "no_match"
    assert verdict("seasons", {"max": 2}, title(kind="show", seasons=3)) == ("no_match", "3 seasons")
    assert verdict("seasons", {"min": 1, "max": 2}, title(kind="show", seasons=1)) == ("match", "1 season")
    assert verdict("seasons", {"max": 2}, title()) == ("no_match", "Movie")
    assert verdict("type", {}, title(kind="show")) == ("show", "")
    plex = TYPES.types["plex"]
    assert plex.test({}, Title(1, {"kind": "movie"}, in_plex=True)).port == "match"
    assert TYPES.types["year"].summary(TYPES.types["year"].clean({"mode": "recent", "months": 24})) == \
        "Released in the last 2 years"
    assert TYPES.types["genre"].summary({"mode": "none", "genres": ["horror", "kids"]}) == "Has none of Horror, Kids"


# -- running -----------------------------------------------------------------------------------------

async def test_dry_run_reads_but_writes_nothing(lists):
    lists.radarr.add_movie(360920, "The Grinch", "the-grinch-360920")  # there already, without the tag
    lists.radarr.excluded.append(1008)  # Russell Brand: on Radarr's exclusion list
    lists.sonarr.add_series(383365, "Lupin", "lupin", tags=[1])
    flow_id = store.create("Netflix CH", top10(radarr={"limit": 4}))
    result = await engine.run(flow_id)
    assert result["dry_run"] is True and result["outcome"] == "partial"  # the limit was reached
    assert (result["added"], result["tagged"], result["skipped"], result["failed"]) == (9, 2, 6, 0)
    assert result["message"] == "Dry run: 9 to add, 2 to tag"
    assert lists.radarr.writes == lists.sonarr.writes == 0
    assert [tag["label"] for tag in lists.radarr.tags] == ["kids"]  # not even the tag is created

    movies = trace(result["run"], "radarr")
    assert movies["Guarding Stars"] == ("added", "HD-1080p, /data/movies")
    assert movies["The Grinch"] == ("tagged", "Tags: top-10")
    assert movies["Russell Brand – Open Secret"] == ("skipped", "On the Radarr exclusion list")
    # The best positions come first: #1 to #4 minus the Grinch (#5 is there), then the limit.
    assert [title for title, (port, _) in movies.items() if port == "added"] == \
        ["Guarding Stars", "Steps", "Heartland", "The Christmas Switch"]
    assert movies["Woodwalkers 2"] == ("skipped", "Over the limit of 4 new titles per run")
    shows = trace(result["run"], "sonarr")
    assert shows["ChiaroScuro"] == ("skipped", "No TVDB ID")
    assert shows["Lupin"] == ("tagged", "Tags: top-10")
    assert shows["The Body"] == ("added", "Ultra-HD, /data/tv")
    assert trace(result["run"], "fp")["Lupin"] == ("out", "#4 TV Shows")

    run = db.get().one("SELECT * FROM flow_runs WHERE id=?", (result["run"],))
    assert run["status"] == "partial" and db.loads(run["notes"]) == ["Radarr: limit of 4 new titles reached"]
    assert db.get().scalar("SELECT flow_run_id FROM parser_runs") == result["run"]
    detail = store.run_detail(result["run"])
    assert [node["id"] for node in detail["nodes"]] == ["fp", "kind", "radarr", "sonarr"]
    assert detail["nodes"][0]["entries"] == {"matched": 17, "uncertain": 2, "failed": 1, "excluded": 0}
    assert {p["key"]: p["count"] for p in detail["nodes"][1]["ports"]} == {"movie": 8, "show": 9}
    assert detail["sources"][0]["label"] == "Netflix Switzerland – Movies, TV Shows"
    titles = store.run_titles(result["run"], "tagged")
    assert titles["counts"] == {"all": 17, "added": 9, "tagged": 2, "present": 0, "skipped": 6, "failed": 0,
                                "filtered": 0}
    assert [row["media"]["title"] for row in titles["rows"]] == ["Lupin", "The Grinch"]
    lupin = titles["rows"][0]
    assert [(step["label"], step["port_label"], step["detail"]) for step in lupin["steps"]] == [
        ("FlixPatrol", "Titles", "#4 TV Shows"), ("Movie or show", "Shows", ""), ("Sonarr", "Tags added", "Tags: top-10")]


async def test_live_run_adds_once_and_then_only_tags(lists):
    lists.radarr.add_movie(360920, "The Grinch", "the-grinch-360920")
    for tmdb_id, name, year in ((1001, "Guarding Stars", 2026), (1002, "Steps", 2026)):
        lists.radarr.know(tmdb_id, name, year)
    lists.sonarr.know(480001, "The Law of Beasts", 2026)
    graph_ = top10(radarr={"limit": 2, "search": False}, sonarr={"monitor": "future", "limit": 1})
    flow_id = store.create("Netflix CH", graph_)
    store.update(flow_id, dry_run=False)
    first = await engine.run(flow_id)
    assert first["dry_run"] is False and (first["added"], first["tagged"]) == (3, 1)
    assert first["message"] == "3 added, 1 tagged"
    top10_tag = next(tag["id"] for tag in lists.radarr.tags if tag["label"] == "top-10")
    added = {body["tmdbId"]: body for body in lists.radarr.added}
    assert list(added) == [1001, 1002]
    assert added[1001]["qualityProfileId"] == 1 and added[1001]["rootFolderPath"] == "/data/movies"
    assert added[1001]["tags"] == [top10_tag] and added[1001]["minimumAvailability"] == "released"
    assert added[1001]["addOptions"] == {"searchForMovie": False, "monitor": "movieOnly"}
    assert added[1001]["titleSlug"] == "guarding-stars-1001" and added[1001]["monitored"] is True
    grinch = next(row for row in lists.radarr.titles if row["tmdbId"] == 360920)
    assert lists.radarr.tag_labels(grinch) == ["top-10"]  # only the tag; profile untouched
    assert lists.radarr.edits == [{"movieIds": [grinch["id"]], "tags": [top10_tag], "applyTags": "add"}]
    series = lists.sonarr.added[0]
    assert series["tvdbId"] == 480001 and series["addOptions"]["monitor"] == "future"
    assert series["seasonFolder"] is True and series["seriesType"] == "standard" and series["monitored"] is True
    shows = trace(first["run"], "sonarr")
    assert shows["The Body"] == ("skipped", "Over the limit of 1 new titles per run")

    # Second run: what was added is there now, the Grinch has its tag; only the next titles move.
    writes = lists.radarr.writes
    second = await engine.run(flow_id)
    movies = trace(second["run"], "radarr")
    assert movies["Guarding Stars"] == ("present", "Already in Radarr")
    assert movies["The Grinch"] == ("present", "Already in Radarr")
    assert movies["Heartland"] == ("failed", "Radarr does not know /movie/lookup/tmdb")  # not in its lookup
    assert lists.radarr.writes == writes and second["outcome"] == "partial"

    # The Discord summary lists the added titles; a dry run sends nothing unless switched on.
    assert any(message["embeds"][0]["title"] == "Netflix CH" and "Guarding Stars (2026)" in
               message["embeds"][0]["description"] for message in lists.discord.messages)
    count = len(lists.discord.messages)
    store.update(flow_id, dry_run=True)
    await engine.run(flow_id)
    assert len(lists.discord.messages) == count


async def test_compare_with_the_mdblist_import_list(lists):
    graph_ = top10(radarr={"compare": ["https://mdblist.com/lists/dominik/netflix-ch"]},
                   sonarr={"compare": ["dominik/netflix-ch"]})
    flow_id = store.create("Netflix CH", graph_)
    result = await engine.run(flow_id)
    detail = store.run_detail(result["run"])
    radarr = next(node for node in detail["nodes"] if node["id"] == "radarr")
    compare = radarr["compare"]
    assert compare["lists"][0]["ref"] == "dominik/netflix-ch" and compare["lists"][0]["error"] is None
    # The list holds the Grinch and Guarding Stars (wrong TMDB id there, the IMDb id decides).
    assert compare["both"] == 2 and compare["list_only"] == []
    assert len(compare["flow_only"]) == 6 and compare["flow_only"][0]["kind"] == "movie"
    sonarr = next(node for node in detail["nodes"] if node["id"] == "sonarr")["compare"]
    assert sonarr["both"] == 1 and len(sonarr["flow_only"]) == 8
    assert len(detail["sources"]) == 2  # FlixPatrol and the list, read once for both outputs


async def test_failing_source_and_unreachable_radarr(lists):
    settings.set("radarr_key", "wrong")
    lists.flixpatrol.challenge = True
    flow_id = store.create("Netflix CH", top10())
    result = await engine.run(flow_id)
    run = db.get().one("SELECT * FROM flow_runs WHERE id=?", (result["run"],))
    assert run["status"] == "partial" and result["added"] == 0
    assert db.loads(run["notes"])[0].startswith("FlixPatrol: ")
    lists.flixpatrol.challenge = False
    scrape.reset()
    result = await engine.run(flow_id)
    assert result["failed"] == 8 and set(trace(result["run"], "radarr").values()) == {("failed", "Radarr rejects the API key")}


async def test_errors_are_reported_once(lists):
    flow_id = store.create("Broken", {"nodes": [{"id": "a", "type": "radarr", "config": {}}], "edges": []})
    with pytest.raises(engine.FlowError, match="Fix the flow first"):
        engine.start(flow_id)
    first = await engine.run(flow_id, trigger="schedule")
    second = await engine.run(flow_id, trigger="schedule")
    assert first["outcome"] == second["outcome"] == "error"
    errors = [message for message in lists.discord.messages if message["embeds"][0]["title"].startswith("Flow failed")]
    assert len(errors) == 1
    assert store.failed_count() == 1  # a tile on the dashboard


def test_prune_keeps_the_last_run_of_each_flow(world):
    flow_id = store.create("A", top10())
    database = db.get()
    old = db.now() - 90 * 86400
    ids = [database.execute("INSERT INTO flow_runs (flow_id, flow_version, dry_run, started_at, status) "
                            "VALUES (?,1,1,?, 'ok')", (flow_id, old + n)).lastrowid for n in range(3)]
    database.execute("INSERT INTO parser_runs (flow_run_id, source, source_ref, started_at, status) "
                     "VALUES (?, 'mdblist', 'mdblist:x', ?, 'ok')", (ids[0], old))
    assert store.prune() == 2
    assert [row["id"] for row in database.query("SELECT id FROM flow_runs")] == [ids[2]]
    assert database.scalar("SELECT flow_run_id FROM parser_runs") == store.GONE


# -- scheduler and API -------------------------------------------------------------------------------

def test_scheduled_flows_start_when_due(lists, client):  # noqa: F811
    flow_id = store.create("Every hour", top10(), "every:1h")
    flow = store.get(flow_id)
    assert flow["next_run_at"] == pytest.approx(db.now() + 3600, abs=5)
    assert engine.start_due(db.now()) == 0
    db.get().update("flows", "id", flow_id, {"next_run_at": db.now() - 1})
    assert engine.start_due(db.now()) == 1
    assert engine.busy(flow_id) and engine.start_due(db.now()) == 0
    assert store.get(flow_id)["next_run_at"] > db.now() + 3000
    job_id = engine._jobs[flow_id]
    job = wait_job(client, job_id)
    assert job["status"] == "done" and job["result"]["dry_run"] is True
    assert db.get().scalar("SELECT trigger FROM flow_runs") == "schedule"
    store.update(flow_id, enabled=False)
    assert store.get(flow_id)["next_run_at"] is None


def test_api(lists, client):  # noqa: F811
    types = client.get("/api/flows/nodes").json()["types"]
    by_type = {node["type"]: node for node in types}
    assert by_type["flixpatrol"]["category"] == "source" and not by_type["flixpatrol"]["inputs"]
    assert [port["label"] for port in by_type["plex"]["ports"]] == ["In Plex", "Not in Plex"]
    assert by_type["radarr"]["fields"][0]["dynamic"] == "radarr.profiles" and by_type["radarr"]["missing"] is None

    created = client.post("/api/flows", json={"name": "  Netflix   CH ", "schedule": "every:6h"}).json()
    assert created["name"] == "Netflix CH" and created["dry_run"] is True and created["schedule_text"] == "Every 6 hours"
    assert [p["message"] for p in created["problems"]] == ["Add a source", "Add Radarr or Sonarr"]
    flow_id = created["id"]
    assert client.post(f"/api/flows/{flow_id}/run", json={}).json()["detail"] == "Fix the flow first: Add a source"
    assert client.put(f"/api/flows/{flow_id}", json={"dry_run": False}).status_code == 400
    checked = client.post("/api/flows/check", json={"graph": top10()}).json()
    assert checked["problems"] == [] and checked["summaries"]["radarr"] == "/data/movies, tags top-10"

    updated = client.put(f"/api/flows/{flow_id}", json={"graph": top10(), "schedule": None}).json()
    assert updated["version"] == 2 and updated["problems"] == [] and updated["schedule_text"] == "Manual"
    assert updated["outputs"] == ["Radarr", "Sonarr"] and updated["next_run_at"] is None
    assert client.put(f"/api/flows/{flow_id}", json={"graph": top10()}).json()["version"] == 2  # unchanged
    assert client.put(f"/api/flows/{flow_id}", json={"schedule": "every:1m"}).json()["detail"] == \
        "Run at most every 15 minutes"
    check = client.get("/api/flows/schedule", params={"value": "0 6 * * *"}).json()
    assert check["ok"] and check["text"] == "Daily at 06:00" and check["next_run_at"] > time.time()

    options = client.get("/api/arr/radarr/options").json()
    assert options["profiles"][0] == {"id": 1, "name": "HD-1080p"} and options["root_folders"][0]["path"] == "/data/movies"
    settings.set("sonarr_url", "")
    assert client.get("/api/arr/sonarr/options").json()["configured"] is False
    settings.set("sonarr_url", "http://sonarr.test:8989")

    job = wait_job(client, client.post(f"/api/flows/{flow_id}/run", json={}).json()["job"])
    assert job["status"] == "done" and job["result"]["dry_run"] is True and job["result"]["added"] == 14
    run_id = job["result"]["run"]
    listed = client.get("/api/flows").json()["flows"][0]
    assert listed["last_run"]["id"] == run_id and listed["last_run"]["added"] == 14 and listed["graph"] is None
    detail = client.get(f"/api/flows/runs/{run_id}").json()
    assert detail["current"] is True and detail["flow_name"] == "Netflix CH" and detail["trigger"] == "manual"
    page = client.get(f"/api/flows/runs/{run_id}/titles", params={"show": "skipped"}).json()
    assert [row["media"]["title"] for row in page["rows"]] == ["Alicia's Law", "ChiaroScuro", "Murder in Galicia"]
    assert client.get(f"/api/flows/runs/{run_id}/titles", params={"q": "grinch"}).json()["total"] == 1
    assert client.get("/api/flows/runs", params={"flow": flow_id}).json()["total"] == 1

    live = client.put(f"/api/flows/{flow_id}", json={"dry_run": False}).json()
    assert live["dry_run"] is False
    assert client.delete(f"/api/flows/{flow_id}").json()["ok"] is True
    assert client.get(f"/api/flows/{flow_id}").status_code == 404
    assert client.get(f"/api/flows/runs/{run_id}").status_code == 404
    assert db.get().scalar("SELECT COUNT(*) FROM parser_runs WHERE flow_run_id > 0") == 0
    # Flow runs stay out of the recent previews on the Lists page.
    assert client.get("/api/lists/runs", params={"previews": True}).json()["runs"] == []
