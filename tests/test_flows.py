"""Flows: schedules, graph checks, filters, dry runs, Radarr and Sonarr outputs, comparison, API."""
import time
from datetime import date, datetime, timedelta

import pytest

from app import db, settings
from app.arr import library
from app.flows import engine, graph, schedule, store
from app.flows.nodes import TYPES, ConfigError, Title
from app.lists import scrape
from app.lists.base import RawEntry
from app.media import normalize
from app.media import store as media_store
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
    for wrong, message in (("every:5m", "Minimum interval is 15 minutes"), ("every 6h", "every:6h"),
                           ("1 2 3", "five fields"), ("61 * * * *", "0–59"), ("0 0 31 2 *", "never occurs"),
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
    assert ("radarr", "Root folder: select one") in problems
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
        ports, detail = node.route(node.clean(config), item)
        return ",".join(ports), detail

    rating = {"metric": "imdb", "min": "7"}
    assert verdict("rating", rating, title(imdb_rating=7.4)) == ("match", "IMDb 7.4")
    assert verdict("rating", rating, title(imdb_rating=6.9)) == ("no_match", "IMDb 6.9")
    assert verdict("rating", rating, title()) == ("no_match", "No IMDb rating")
    assert verdict("rating", {**rating, "missing": "match"}, title())[0] == "match"
    assert verdict("rating", {"metric": "rt_critics", "min": 80}, title(rt_critics=85)) == ("match", "RT critics 85%")
    assert verdict("rating", {"metric": "imdb_votes", "min": 10000}, title(imdb_votes=9000)) == \
        ("no_match", "IMDb votes 9'000")
    with pytest.raises(ConfigError, match="0–100"):
        TYPES.types["rating"].clean({"metric": "rt_audience", "min": 120})

    recent = date.today() - timedelta(days=200)
    assert verdict("year", {"mode": "recent", "months": 12}, title(release_date=recent.isoformat()))[0] == "match"
    assert verdict("year", {"mode": "recent", "months": 6}, title(release_date=recent.isoformat()))[0] == "no_match"
    assert verdict("year", {"mode": "recent", "months": 6}, title()) == ("no_match", "No release date")
    assert verdict("year", {"mode": "range", "from": 2020}, title(year=2024)) == ("match", "2024")
    assert verdict("year", {"mode": "range", "from": 2020, "to": 2022}, title(year=2024))[0] == "no_match"
    with pytest.raises(ConfigError, match="start or end year"):
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
    assert TYPES.types["plex"].route({}, Title(1, {"kind": "movie"}, in_plex=True)) == (["match"], "In Plex")
    year = TYPES.types["year"]
    assert year.summary(year.clean({"mode": "recent", "months": 24})) == "Released in the last 2 years"
    genre = TYPES.types["genre"]
    assert genre.summary(genre.clean({"mode": "none", "genres": ["horror", "kids"]})) == "Has none of Horror, Kids"


def test_filters_with_several_outputs(world):
    rating = TYPES.types["rating"]
    # Older flat settings are one rule with the ports Match and No match.
    flat = rating.clean({"metric": "imdb", "min": 7})
    assert [rule["id"] for rule in flat["rules"]] == ["match"] and flat["each"] is False
    assert [(port.key, port.label) for port in rating.ports_for(flat)] == [("match", "Match"), ("no_match", "No match")]

    tiers = rating.clean({"rules": [{"id": "match", "metric": "imdb", "min": 8},
                                    {"id": "r2", "metric": "imdb", "min": 7, "label": "Good"},
                                    {"id": "r3", "metric": "rt_critics", "min": 90}]})
    assert [(port.key, port.label) for port in rating.ports_for(tiers)] == [
        ("match", "IMDb ≥ 8"), ("r2", "Good"), ("r3", "RT critics ≥ 90%"), ("no_match", "No match")]
    assert rating.summary(tiers) == "3 outputs, first that fits"

    def movie(**media) -> Title:
        return Title(1, {"kind": "movie", "title": "X", **media})

    # The first rule that fits wins; what the node saw is listed once per value.
    assert rating.route(tiers, movie(imdb_rating=8.3, rt_critics=95)) == (["match"], "IMDb 8.3")
    assert rating.route(tiers, movie(imdb_rating=7.4, rt_critics=95)) == (["r2"], "IMDb 7.4")
    assert rating.route(tiers, movie(imdb_rating=6.1, rt_critics=95)) == (["r3"], "IMDb 6.1, RT critics 95%")
    assert rating.route(tiers, movie(imdb_rating=6.1, rt_critics=40)) == (["no_match"], "IMDb 6.1, RT critics 40%")

    # Genres: a title goes to every rule it fits by default.
    genre = TYPES.types["genre"]
    split = genre.clean({"rules": [{"id": "match", "mode": "any", "genres": ["action"]},
                                   {"id": "r2", "mode": "any", "genres": ["comedy"]}]})
    assert split["each"] is True
    assert [port.label for port in genre.ports_for(split)] == ["Action", "Comedy", "No match"]
    assert genre.route(split, movie(genres="[28, 35]")) == (["match", "r2"], "Action, Comedy")
    assert genre.route({**split, "each": False}, movie(genres="[28, 35]"))[0] == ["match"]
    assert genre.route(split, movie(genres="[18]")) == (["no_match"], "Drama")

    with pytest.raises(ConfigError, match="Output 2: Genres: choose at least one"):
        genre.clean({"rules": [{"id": "match", "genres": ["action"]}, {"id": "r2", "genres": []}]})
    with pytest.raises(ConfigError, match="same id"):
        genre.clean({"rules": [{"id": "match", "genres": ["action"]}, {"id": "match", "genres": ["comedy"]}]})
    with pytest.raises(ConfigError, match="At most 12 outputs"):
        genre.clean({"rules": [{"id": f"r{n}", "genres": ["action"]} for n in range(13)]})

    # Connections from a removed output are dropped; those from an output with broken settings stay.
    graph_ = top10()
    graph_["nodes"].append({"id": "g", "type": "genre", "config": {"rules": [
        {"id": "match", "genres": ["action"]}, {"id": "r2", "genres": []}]}})
    graph_["edges"] += [{"source": "kind", "port": "movie", "target": "g"},
                        {"source": "g", "port": "r2", "target": "radarr"},
                        {"source": "g", "port": "r9", "target": "radarr"}]
    checked = graph.check(graph_)
    assert {"source": "g", "port": "r2", "target": "radarr"} in checked.graph["edges"]
    assert not any(edge["port"] == "r9" for edge in checked.graph["edges"])
    assert ("g", "Output 2: Genres: choose at least one") in {(p.node, p.message) for p in checked.problems}


# -- running -----------------------------------------------------------------------------------------

async def test_dry_run_reads_but_writes_nothing(lists):
    lists.radarr.add_movie(360920, "The Grinch", "the-grinch-360920")  # in Radarr without the tag, and in Plex
    db.get().execute("INSERT INTO items (key, type, title, tmdb_id, gone) VALUES ('g1', 'movie', 'The Grinch', '360920', 0)")
    lists.radarr.exclude(1008, "Russell Brand – Open Secret", 2026)  # on Radarr's exclusion list
    lists.sonarr.add_series(383365, "Lupin", "lupin", tags=[1])  # in Sonarr without the tag, not in Plex yet
    flow_id = store.create("Netflix CH", top10(radarr={"limit": 4}))
    result = await engine.run(flow_id)
    assert result["dry_run"] is True and result["outcome"] == "partial"  # the limit was reached
    assert (result["added"], result["tagged"], result["present"], result["skipped"], result["failed"]) == (9, 0, 2, 5, 0)
    assert result["message"] == "Dry run: 9 to add, 2 existing"
    assert lists.radarr.writes == lists.sonarr.writes == 0
    assert [tag["label"] for tag in lists.radarr.tags] == ["kids"]  # not even the tag is created

    movies = trace(result["run"], "radarr")
    assert movies["Guarding Stars"] == ("added", "HD-1080p, /data/movies")
    assert movies["The Grinch"] == ("present", "In Plex")  # in the library: no tags
    # Excluded in Radarr: synced at the start of the run, so the title stops at the source.
    assert "Russell Brand – Open Secret" not in movies
    assert trace(result["run"], "fp")["Russell Brand – Open Secret"] == (None, "Excluded")
    # The best positions come first: #1 to #4 minus the Grinch (#5 is there), then the limit.
    assert [title for title, (port, _) in movies.items() if port == "added"] == \
        ["Guarding Stars", "Steps", "Heartland", "The Christmas Switch"]
    assert movies["Woodwalkers 2"] == ("skipped", "Over the limit of 4 new titles per run")
    shows = trace(result["run"], "sonarr")
    assert shows["ChiaroScuro"] == ("skipped", "No TVDB ID")  # neither TMDB, MDBList nor Sonarr know one
    assert sorted(lists.sonarr.lookups) == ["tmdb:2006", "tmdb:2007", "tmdb:2009"]  # the shows without one
    assert shows["Lupin"] == ("present", "")  # there already: no tag afterwards
    assert shows["The Body"] == ("added", "Ultra-HD, /data/tv")
    assert trace(result["run"], "fp")["Lupin"] == ("out", "#4 TV Shows")

    run = db.get().one("SELECT * FROM flow_runs WHERE id=?", (result["run"],))
    assert run["status"] == "partial" and db.loads(run["notes"]) == ["Radarr: limit of 4 new titles reached"]
    assert lists.radarr.writes == 0  # the sync only read Radarr's list
    assert db.get().scalar("SELECT flow_run_id FROM parser_runs") == result["run"]
    detail = store.run_detail(result["run"])
    assert [node["id"] for node in detail["nodes"]] == ["fp", "kind", "radarr", "sonarr"]
    assert detail["nodes"][0]["entries"] == {"matched": 16, "uncertain": 2, "failed": 1, "excluded": 1}
    assert {p["key"]: p["count"] for p in detail["nodes"][1]["ports"]} == {"movie": 7, "show": 9}
    # Output cards name the quality profile as read from Radarr during the run.
    assert detail["nodes"][2]["facts"] == [{"label": "Profile", "value": "HD-1080p"},
                                           {"label": "Folder", "value": "/data/movies"},
                                           {"label": "Tags", "value": "top-10"}]
    assert detail["nodes"][2]["summary"] == "HD-1080p, /data/movies, tags top-10"
    assert detail["nodes"][1]["facts"] == []
    assert detail["sources"][0]["label"] == "Netflix Switzerland – Movies, TV Shows"
    titles = store.run_titles(result["run"], "present")
    assert titles["counts"] == {"all": 17, "added": 9, "tagged": 0, "present": 2, "skipped": 5, "failed": 0,
                                "filtered": 1}
    assert [row["media"]["title"] for row in titles["rows"]] == ["Lupin", "The Grinch"]
    lupin = titles["rows"][0]
    # The path names the list a title came from, not just the source.
    assert [(step["label"], step["port_label"], step["detail"]) for step in lupin["steps"]] == [
        ("Netflix Switzerland", "Titles", "#4 TV Shows"), ("Movie or show", "Shows", ""),
        ("Sonarr", "Already exists", "")]


async def test_tvdb_ids_where_tmdb_has_none(lists):
    """TMDB has no TVDB link for these shows; the list, MDBList or Sonarr (through TVDB) fill it in."""
    lists.sonarr.know(470006, "ChiaroScuro", 2026, tmdb=2006)  # TVDB links it to TMDB 2006
    lists.mdblist.ratings["show:2007"] = {"imdb": (6.8, 900)}
    lists.mdblist.tvdb[2007] = 470007  # MDBList knows Murder in Galicia's
    flow_id = store.create("Netflix CH", top10())
    result = await engine.run(flow_id)
    shows = trace(result["run"], "sonarr")
    assert shows["ChiaroScuro"] == ("added", "Ultra-HD, /data/tv")
    assert shows["Murder in Galicia"] == ("added", "Ultra-HD, /data/tv")
    assert shows["Alicia's Law"] == ("skipped", "No TVDB ID")
    tvdb = {row["tmdb_id"]: row["tvdb_id"] for row in db.get().query("SELECT tmdb_id, tvdb_id FROM media_cache")}
    assert (tvdb[2006], tvdb[2007], tvdb[2009]) == (470006, 470007, None)
    assert sorted(lists.sonarr.lookups) == ["tmdb:2006", "tmdb:2009"]  # MDBList had answered for 2007

    # Sonarr is asked once: not again for the one it found, not within a day for the one it did not.
    lists.sonarr.lookups.clear()
    await engine.run(flow_id)
    assert lists.sonarr.lookups == []

    # A refresh from TMDB keeps the TVDB ID found elsewhere.
    media_id = db.get().scalar("SELECT id FROM media_cache WHERE kind='show' AND tmdb_id=2006")
    await media_store.ensure("show", 2006, max_age_days=0)
    assert media_store.get(media_id)["tvdb_id"] == 470006

    # A list that carries the TVDB ID (Trakt, MDBList lists) fills it in while matching.
    _, match = await normalize.resolve("trakt", RawEntry(1, "Alicia's Law", 2026, "show", {"tmdb": 2009, "tvdb": 470009}))
    assert match.status == "matched" and media_store.get(match.media_id)["tvdb_id"] == 470009


async def test_flow_with_rating_tiers(lists):
    graph_ = top10()
    graph_["nodes"].append({"id": "tiers", "type": "rating", "config": {"rules": [
        {"id": "match", "metric": "imdb", "min": 7.4, "label": "Great"},
        {"id": "r2", "metric": "imdb", "min": 7}]}})
    graph_["edges"] = [edge for edge in graph_["edges"] if edge["port"] != "show"]
    graph_["edges"] += [{"source": "kind", "port": "show", "target": "tiers"},
                        {"source": "tiers", "port": "match", "target": "sonarr"}]
    flow_id = store.create("Tiers", graph_)
    result = await engine.run(flow_id)
    detail = store.run_detail(result["run"])
    tiers = next(node for node in detail["nodes"] if node["id"] == "tiers")
    assert [(port["label"], port["count"]) for port in tiers["ports"]] == [("Great", 1), ("IMDb ≥ 7", 1), ("No match", 7)]
    assert trace(result["run"], "tiers")["Lupin"] == ("match", "IMDb 7.5")
    assert trace(result["run"], "sonarr") == {"Lupin": ("added", "Ultra-HD, /data/tv")}
    rows = store.run_titles(result["run"], "all", "Black Doves")["rows"]
    assert [(step["label"], step["port_label"]) for step in rows[0]["steps"]][-1] == ("Rating", "IMDb ≥ 7")
    view = store.view(store.get(flow_id))
    assert [port["label"] for port in view["ports"]["tiers"]] == ["Great", "IMDb ≥ 7", "No match"]


async def test_live_run_adds_once_and_never_tags_afterwards(lists):
    lists.radarr.add_movie(360920, "The Grinch", "the-grinch-360920")
    for tmdb_id, name, year in ((1001, "Guarding Stars", 2026), (1002, "Steps", 2026)):
        lists.radarr.know(tmdb_id, name, year)
    lists.sonarr.know(480001, "The Law of Beasts", 2026)
    graph_ = top10(radarr={"limit": 2, "search": False}, sonarr={"monitor": "future", "limit": 1})
    flow_id = store.create("Netflix CH", graph_)
    store.update(flow_id, dry_run=False)
    first = await engine.run(flow_id)
    assert first["dry_run"] is False and (first["added"], first["tagged"], first["present"]) == (3, 0, 1)
    assert first["message"] == "3 added, 1 existing"
    top10_tag = next(tag["id"] for tag in lists.radarr.tags if tag["label"] == "top-10")
    added = {body["tmdbId"]: body for body in lists.radarr.added}
    assert list(added) == [1001, 1002]
    assert added[1001]["qualityProfileId"] == 1 and added[1001]["rootFolderPath"] == "/data/movies"
    assert added[1001]["tags"] == [top10_tag] and added[1001]["minimumAvailability"] == "released"
    assert added[1001]["addOptions"] == {"searchForMovie": False, "monitor": "movieOnly"}
    assert added[1001]["titleSlug"] == "guarding-stars-1001" and added[1001]["monitored"] is True
    grinch = next(row for row in lists.radarr.titles if row["tmdbId"] == 360920)
    assert trace(first["run"], "radarr")["The Grinch"] == ("present", "")
    assert lists.radarr.tag_labels(grinch) == [] and lists.radarr.edits == []  # tags only go on with a new title
    series = lists.sonarr.added[0]
    assert series["tvdbId"] == 480001 and series["addOptions"]["monitor"] == "future"
    assert series["seasonFolder"] is True and series["seriesType"] == "standard" and series["monitored"] is True
    shows = trace(first["run"], "sonarr")
    assert shows["The Body"] == ("skipped", "Over the limit of 1 new titles per run")

    # Second run: what was added is there now; only the next titles move.
    writes = lists.radarr.writes
    second = await engine.run(flow_id)
    movies = trace(second["run"], "radarr")
    assert movies["Guarding Stars"] == ("present", "")
    assert movies["The Grinch"] == ("present", "")
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
    assert compare["lists"][0]["name"] == "Netflix CH"  # the list's title on MDBList
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
    assert db.loads(run["notes"])[:2] == ["Exclusions: Radarr rejects the API key", db.loads(run["notes"])[1]]
    assert db.loads(run["notes"])[1].startswith("FlixPatrol: ")
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
    assert checked["problems"] == [] and checked["summaries"]["radarr"] == "HD-1080p, /data/movies, tags top-10"

    updated = client.put(f"/api/flows/{flow_id}", json={"graph": top10(), "schedule": None}).json()
    assert updated["version"] == 2 and updated["problems"] == [] and updated["schedule_text"] == "Manual"
    assert updated["outputs"] == ["Radarr", "Sonarr"] and updated["next_run_at"] is None
    assert client.put(f"/api/flows/{flow_id}", json={"graph": top10()}).json()["version"] == 2  # unchanged
    assert client.put(f"/api/flows/{flow_id}", json={"schedule": "every:1m"}).json()["detail"] == \
        "Minimum interval is 15 minutes"
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



def test_dashboard_lists_flows_adds_and_schedule(lists, client):  # noqa: F811
    lists.radarr.add_movie(360920, "The Grinch", "the-grinch-360920")
    for tmdb_id, name, year in ((1001, "Guarding Stars", 2026), (1002, "Steps", 2026)):
        lists.radarr.know(tmdb_id, name, year)
    lists.sonarr.know(480001, "The Law of Beasts", 2026)
    graph_ = top10(radarr={"limit": 2, "search": False, "compare": ["dominik/netflix-ch"]}, sonarr={"limit": 1})
    flow_id = store.create("Netflix CH", graph_, "every:6h")
    store.create("Not run", top10())
    store.update(flow_id, dry_run=False)
    job = wait_job(client, client.post(f"/api/flows/{flow_id}/run", json={}).json()["job"])
    assert job["result"]["added"] == 3

    overview = client.get("/api/overview").json()
    cards = {card["name"]: card for card in overview["flows"]}
    assert cards["Not run"]["last_run"] is None and cards["Not run"]["compare"] is None
    card = cards["Netflix CH"]
    assert card["dry_run"] is False and card["last_run"]["added"] == 3 and card["next_run_at"]
    assert card["compare"]["lists"][0]["name"] == "Netflix CH"
    assert card["compare"]["both"] == 2 and card["compare"]["flow_only"] > 0  # Radarr's and Sonarr's added up
    adds = overview["flow_adds"]
    assert [(add["media"]["title"], add["service"]) for add in adds] == \
        [("The Law of Beasts", "sonarr"), ("Steps", "radarr"), ("Guarding Stars", "radarr")]
    assert adds[0]["flow_name"] == "Netflix CH" and adds[0]["run_id"] == job["result"]["run"] and adds[0]["at"]

    # A dry run adds nothing to the strip; neither does a title added twice.
    store.update(flow_id, dry_run=True)
    wait_job(client, client.post(f"/api/flows/{flow_id}/run", json={}).json()["job"])
    assert len(client.get("/api/overview").json()["flow_adds"]) == 3

    rows = {row["key"]: row for row in overview["schedule"]}
    assert rows["sync"]["interval"] == 15 * 60 and rows["sync"]["state"] == "on" and rows["sync"]["next"]
    assert rows["exclusions"]["interval"] == 6 * 3600  # Radarr and Sonarr are set up
    settings.set("automation", False)
    settings.set("folder_check_hours", 0)
    rows = {row["key"]: row for row in client.get("/api/overview").json()["schedule"]}
    assert rows["setcheck"]["state"] == "paused" and rows["setcheck"]["next"] is None
    assert rows["folders"]["state"] == "off" and rows["folders"]["interval"] is None

# -- exclusions in step with Radarr and Sonarr ---------------------------------------------------------

async def test_exclusions_follow_radarr_and_sonarr_both_ways(lists):
    from app.lists import exclusions, runs

    lists.radarr.exclude(360920, "The Grinch", 2018)
    lists.radarr.exclude(999001, "Never Listed", 2001)  # no title row in Programarr
    lists.sonarr.exclude(383365, "Lupin")
    power = await media_store.ensure("show", 2010)  # excluded in Programarr before the sync existed
    db.get().execute("INSERT INTO exclusions (media_id, scope, created_at) VALUES (?, 'global', ?)", (power, db.now()))

    results = await exclusions.sync()
    assert results["radarr"]["new"] == 2 and results["sonarr"]["written"] == 1
    assert lists.sonarr.excluded == [383365, 276562]  # Programarr's exclusion went to Sonarr
    rows = {row["title"]: (row["id"] is not None, row["service"], row["pending"]) for row in exclusions.exclusions()}
    assert rows == {"Power": (True, "sonarr", False), "The Grinch": (False, "radarr", False),
                    "Never Listed": (False, "radarr", False), "Lupin": (False, "sonarr", False)}

    # Previews and flows skip what Radarr and Sonarr exclude.
    first = await runs.preview("flixpatrol", runs.prepare("flixpatrol", NETFLIX_CH))
    statuses = {row["raw_title"]: row["status"] for row in db.get().query(
        "SELECT raw_title, status FROM parser_entries WHERE run_id=?", (first["run"],))}
    assert statuses["The Grinch"] == statuses["Lupin"] == statuses["Power"] == "excluded"

    # Taken off Radarr's list: included here after the next sync, stored entries follow.
    lists.radarr.exclusions = [row for row in lists.radarr.exclusions if row["external"] != 360920]
    await exclusions.sync()
    assert db.get().scalar("SELECT status FROM parser_entries WHERE run_id=? AND raw_title='The Grinch'",
                           (first["run"],)) == "matched"

    # Included in Programarr: off Sonarr's list.
    await exclusions.unexclude_arr("sonarr", 383365)
    assert lists.sonarr.excluded == [276562]
    assert db.get().scalar("SELECT status FROM parser_entries WHERE run_id=? AND raw_title='Lupin'",
                           (first["run"],)) == "matched"

    # Radarr unreachable: the exclusion holds here at once and goes there with the next sync.
    lists.radarr.down = True
    grinch = media_store.find("movie", 360920)
    await exclusions.exclude(None, None, grinch["id"], None)
    assert next(row for row in exclusions.exclusions() if row["title"] == "The Grinch")["pending"] is True
    assert (await exclusions.sync())["radarr"]["error"] == "Radarr: Unavailable"
    lists.radarr.down = False
    writes = lists.radarr.writes
    await exclusions.sync(write=False)  # as in a dry run: read only
    assert lists.radarr.writes == writes and 360920 not in lists.radarr.excluded
    assert next(row for row in exclusions.exclusions() if row["title"] == "The Grinch")["pending"] is True
    lists.radarr.paged = False  # an older Radarr without /paged
    await exclusions.sync()
    assert sorted(lists.radarr.excluded) == [360920, 999001]
    assert next(row for row in exclusions.exclusions() if row["title"] == "The Grinch")["pending"] is False

    # Included while Radarr is down: not excluded here any more, removed there with the next sync.
    lists.radarr.down = True
    await exclusions.unexclude_media(grinch["id"])
    assert not normalize.excluded(grinch["id"])
    lists.radarr.down = False
    await exclusions.sync()
    assert lists.radarr.excluded == [999001]


def test_exclusions_api(lists, client):  # noqa: F811
    lists.radarr.exclude(999001, "Never Listed", 2001)
    job = wait_job(client, client.post("/api/exclusions/sync").json()["job"])
    assert job["status"] == "done" and job["result"]["outcome"] == "ok"
    assert job["result"]["message"] == "Radarr: 1 new; Sonarr: no changes"
    page = client.get("/api/exclusions").json()
    assert page["services"] == ["radarr", "sonarr"] and page["synced_at"] and page["errors"] == []
    assert [(row["title"], row["service"], row["media"]) for row in page["rows"]] == [("Never Listed", "radarr", None)]
    assert client.delete("/api/exclusions/arr/radarr/999001").json()["ok"] is True
    assert lists.radarr.excluded == [] and client.get("/api/exclusions").json()["rows"] == []
