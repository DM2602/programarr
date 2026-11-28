"""Collections: the flow output, the queue, order and visibility in Plex, posters and other collections."""
from datetime import datetime

import pytest

from app import db, settings
from app.arr import library as arr_library
from app.collections import posters
from app.collections import store as collections
from app.collections import sync
from app.flows import engine, graph, store
from app.lists import scrape
from app.posters import library, tasks
from tests.fakes.images import poster_for
from tests.test_web import client, wait_job  # noqa: F401 - fixture and helper


@pytest.fixture
async def plex_world(world):
    """Netflix titles on TMDB; Guarding Stars and The Grinch in Plex, Steps downloading in Radarr."""
    world.fill_lists()
    scrape.reset()
    arr_library.reset()
    settings.set("automation", False)
    world.plex.add_movie("Guarding Stars", 2026, "1001")
    world.plex.add_movie("The Grinch", 2018, "360920")
    world.plex.add_show("Lupin", 2021, "96677", "383365", seasons=[1])
    world.radarr.add_movie(1002, "Steps", "steps-1002", has_file=False)
    await world.enable_libraries()
    await library.sync()
    return world


def trending(collection: dict | None = None, radarr: dict | None = None) -> dict:
    """TMDB's trending movies into a collection of the movie library."""
    nodes = [
        {"id": "tmdb", "type": "tmdb", "config": {"chart": "trending-movie-week", "limit": 20}},
        {"id": "col", "type": "collection", "config": {"library": "1", "name": "Trending {month}", **(collection or {})}},
    ]
    edges = [{"source": "tmdb", "port": "out", "target": "col"}]
    if radarr is not None:
        nodes.append({"id": "radarr", "type": "radarr", "config": {
            "quality_profile": 1, "root_folder": "/data/movies", "search": False, **radarr}})
        edges.append({"source": "tmdb", "port": "out", "target": "radarr"})
    return {"nodes": nodes, "edges": edges}


def trace(run_id: int, node: str) -> dict[str, tuple[str, str]]:
    rows = db.get().query("SELECT t.port, t.detail, m.title FROM flow_trace t JOIN media_cache m ON m.id=t.media_id "
                          "WHERE t.run_id=? AND t.node_id=?", (run_id, node))
    return {row["title"]: (row["port"], row["detail"]) for row in rows}


def month() -> str:
    return collections.MONTHS[datetime.now().month - 1]


def states(collection_id: int) -> dict[str, str]:
    rows = db.get().query("SELECT m.title, ci.state FROM collection_items ci JOIN media_cache m ON m.id=ci.media_id "
                          "WHERE ci.collection_id=? ORDER BY ci.position", (collection_id,))
    return {row["title"]: row["state"] for row in rows}


# -- names and order ---------------------------------------------------------------------------------

def test_names_and_minimal_moves():
    assert collections.check_template("  Top 10   {list} ") == "Top 10 {list}"
    with pytest.raises(collections.TemplateError, match="Name the collection"):
        collections.check_template("  ")
    with pytest.raises(collections.TemplateError, match=r"Unknown placeholder \{week\}"):
        collections.check_template("Best of {week}")
    when = datetime(2026, 11, 28, 12).timestamp()
    assert collections.resolve("Top 10 {list}", "Netflix Switzerland", when) == "Top 10 Netflix Switzerland"
    assert collections.resolve("{month} {year}: {list}", None, when) == "November 2026:"
    assert collections.resolve("{list}", None, when) == "Collection"
    # The longest run already in order stays; only the others move.
    assert sync._stable(["a", "b", "c", "d"], ["a", "c", "b", "d"]) in ({"a", "b", "d"}, {"a", "c", "d"})
    assert sync._stable(["a", "b", "c"], ["c", "b", "a"]) in ({"a"}, {"b"}, {"c"})
    assert sync._stable(["a", "b"], []) == set()


def test_graph_checks(world):
    checked = graph.check(trending())
    assert ("col", "Library: select one of the libraries under Settings › Plex") in \
        {(p.node, p.message) for p in checked.problems}
    db.get().execute("INSERT INTO libraries (key, title, type, enabled) VALUES ('1', 'Filme', 'movie', 1)")
    assert graph.check(trending()).ok
    wrong = graph.check(trending({"name": "Top {week}"}))
    assert [p.message for p in wrong.problems] == ["Name: Unknown placeholder {week}: use {list}, {month} or {year}"]
    source = {"nodes": [{"id": "lib", "type": "plex_library", "config": {"library": "9"}},
                        {"id": "col", "type": "collection", "config": {"library": "1", "name": "All"}}],
              "edges": [{"source": "lib", "port": "out", "target": "col"}]}
    assert [p.message for p in graph.check(source).problems] == [
        "Library: select one of the libraries under Settings › Plex"]


# -- runs -------------------------------------------------------------------------------------------

async def test_dry_run_then_live_run_and_the_queue(plex_world):
    world = plex_world
    flow_id = store.create("Trending", trending())
    collection = collections.for_node(flow_id, "col")
    assert collection["name_template"] == "Trending {month}" and collection["sync_mode"] == "mirror"

    dry = await engine.run(flow_id)
    assert dry["dry_run"] is True and dry["outcome"] == "ok"
    assert dry["message"] == "Dry run: collections: 2 to add, 1 queued"
    assert world.plex.collections == {} and world.plex.collection_calls == []
    assert states(collection["id"]) == {}  # nothing written
    shown = trace(dry["run"], "col")
    assert shown == {"Guarding Stars": ("added", ""), "Steps": ("queued", "In Radarr, not in Plex yet"),
                     "The Grinch": ("added", "")}
    detail = store.run_detail(dry["run"])
    output = detail["nodes"][1]
    assert output["summary"] == f"Trending {month()}"
    assert {port["key"]: (port["label"], port["count"]) for port in output["ports"]} == {
        "added": ("Added", 2), "queued": ("Queued", 1)}
    assert detail["collections"] == {"outputs": 1, "added": 2, "removed": 0, "queued": 1, "missing": 0, "failed": 0}

    store.update(flow_id, dry_run=False)
    live = await engine.run(flow_id)
    assert live["message"] == "Collections: 2 added, 1 queued"
    made = world.plex.find_collection(f"Trending {month()}")
    assert made is not None and made["section"] == "1" and made["sort"] == 2  # custom order
    assert world.plex.collection_titles(made["ratingKey"]) == ["Guarding Stars", "The Grinch"]  # list order
    collection = collections.get(collection["id"])
    assert collection["plex_key"] == made["ratingKey"] and collection["title"] == f"Trending {month()}"
    assert states(collection["id"]) == {"Guarding Stars": "in_plex", "Steps": "queued", "The Grinch": "in_plex"}
    queued = db.get().one("SELECT * FROM collection_items WHERE state='queued'")
    assert queued["service"] == "radarr" and queued["expires_at"] == queued["queued_at"] + 30 * 86400
    assert any(f"Trending {month()}" in message["embeds"][0]["description"] for message in world.discord.messages)

    # Steps reaches Plex: the next sync puts it in its place in the collection.
    world.plex.add_movie("Steps", 2026, "1002")
    calls = len(world.plex.collection_calls)
    summary = await tasks.sync_and_process()
    assert summary["queue"] == {"arrived": 1, "expired": 0}
    assert world.plex.collection_titles(made["ratingKey"]) == ["Guarding Stars", "Steps", "The Grinch"]
    assert [call.split()[0] for call in world.plex.collection_calls[calls:]] == ["add", "move"]  # one move
    assert states(collection["id"])["Steps"] == "in_plex"
    assert world.discord.messages[-1]["embeds"][0]["title"] == f"Added to Trending {month()}"
    assert "Steps (2026)" in world.discord.messages[-1]["embeds"][0]["description"]

    # The chart changes: mirror takes out what dropped out, a title nobody has is listed as missing.
    world.tmdb.charts["/trending/movie/week"] = ["movie:1003", "movie:1002"]
    third = await engine.run(flow_id)
    assert trace(third["run"], "col") == {"Heartland": ("missing", "Not in Plex"), "Steps": ("present", "In the collection"),
                                          "Guarding Stars": ("removed", "No longer in the result"),
                                          "The Grinch": ("removed", "No longer in the result")}
    assert third["message"] == "Collections: 2 removed"
    assert world.plex.collection_titles(made["ratingKey"]) == ["Steps"]
    assert states(collection["id"]) == {"Steps": "in_plex", "Heartland": "missing"}
    titles = store.run_titles(third["run"], "removed")
    assert [row["media"]["title"] for row in titles["rows"]] == ["Guarding Stars", "The Grinch"]
    assert titles["rows"][0]["outcomes"][0]["port_label"] == "Removed"

    # Append keeps what is in the collection; nothing left in the list deletes nothing.
    graph_ = trending({"sync": "append"})
    store.update(flow_id, graph=graph_)
    world.tmdb.charts["/trending/movie/week"] = ["movie:1001"]
    await engine.run(flow_id)
    assert world.plex.collection_titles(made["ratingKey"]) == ["Guarding Stars", "Steps"]

    # Removed from the flow: the collection stays in Plex, listed as no longer in a flow.
    store.update(flow_id, graph={"nodes": [graph_["nodes"][0]], "edges": []})
    assert collections.get(collection["id"])["in_flow"] == 0
    assert world.plex.find_collection(f"Trending {month()}") is not None
    assert await sync.delete(collection["id"]) is True
    assert world.plex.find_collection(f"Trending {month()}") is None and collections.get(collection["id"]) is None


async def test_titles_added_in_the_same_run_are_queued(plex_world):
    world = plex_world
    world.radarr.know(1001, "Guarding Stars", 2026)
    world.radarr.know(1003, "Heartland", 2026)
    world.tmdb.charts["/trending/movie/week"] = ["movie:1003", "movie:360920"]
    flow_id = store.create("Trending", trending({"order": "alpha", "limit": 5}, radarr={}))
    store.update(flow_id, dry_run=False)
    result = await engine.run(flow_id)
    # Radarr runs first: Heartland was just added, so it waits for Plex.
    assert trace(result["run"], "radarr")["Heartland"][0] == "added"
    assert trace(result["run"], "col")["Heartland"] == ("queued", "Added to Radarr in this run")
    assert result["message"] == "1 added, 1 existing, collections: 1 added, 1 queued"
    made = world.plex.find_collection(f"Trending {month()}")
    assert made["sort"] == 1 and not any(call.startswith("move") for call in world.plex.collection_calls)


async def test_queue_expires(plex_world):
    flow_id = store.create("Trending", trending())
    store.update(flow_id, dry_run=False)
    await engine.run(flow_id)
    collection = collections.for_node(flow_id, "col")
    db.get().execute("UPDATE collection_items SET expires_at=? WHERE state='queued'", (db.now() - 10,))
    assert await sync.process_queue() == {"arrived": 0, "expired": 1}
    assert states(collection["id"])["Steps"] == "missing"  # mirror: still listed
    # Taken out of the queue by hand, the title stays listed as missing (mirror).
    settings.set("queue_days", 0)
    await engine.run(flow_id)
    assert states(collection["id"])["Steps"] == "queued"
    assert db.get().scalar("SELECT expires_at FROM collection_items WHERE state='queued'") is None  # no limit
    media_id = db.get().scalar("SELECT media_id FROM collection_items WHERE state='queued'")
    assert sync.unqueue(collection["id"], media_id) is True
    assert states(collection["id"])["Steps"] == "missing"


async def test_genre_collections_from_a_plex_library(plex_world):
    world = plex_world
    world.plex.add_movie("Heartland", 2026, "1003")
    await library.sync()
    flow = {
        "nodes": [
            {"id": "lib", "type": "plex_library", "config": {"library": "1"}},
            {"id": "genre", "type": "genre", "config": {"rules": [
                {"id": "match", "mode": "any", "genres": ["action"]},
                {"id": "r2", "mode": "any", "genres": ["comedy", "romance"]}]}},
            {"id": "action", "type": "collection", "config": {"library": "1", "name": "Action", "order": "release"}},
            {"id": "light", "type": "collection", "config": {"library": "1", "name": "Feel good", "order": "alpha"}},
        ],
        "edges": [
            {"source": "lib", "port": "out", "target": "genre"},
            {"source": "genre", "port": "match", "target": "action"},
            {"source": "genre", "port": "r2", "target": "light"},
        ],
    }
    flow_id = store.create("Genres", flow)
    store.update(flow_id, dry_run=False)
    result = await engine.run(flow_id)
    assert result["outcome"] == "ok", result
    assert trace(result["run"], "lib") == {"Guarding Stars": ("out", ""), "Heartland": ("out", ""),
                                           "The Grinch": ("out", "")}
    action = world.plex.find_collection("Action")
    light = world.plex.find_collection("Feel good")
    assert world.plex.collection_titles(action["ratingKey"]) == ["Guarding Stars"] and action["sort"] == 0
    assert sorted(world.plex.collection_titles(light["ratingKey"])) == ["Heartland", "The Grinch"]
    detail = store.run_detail(result["run"])
    assert detail["nodes"][0]["summary"] == "Filme" and detail["nodes"][0]["count_in"] == 3
    # Shows in a movie library are skipped; over the limit, a title leaves the collection.
    action_id = collections.for_node(flow_id, "action")["id"]
    guarding = db.get().scalar("SELECT id FROM media_cache WHERE tmdb_id=1001")
    titles = [sync.Title(guarding, {"kind": "movie", "title": "Guarding Stars"}),
              sync.Title(99999, {"kind": "show", "title": "Lupin"})]
    collections.update(action_id, {"max_items": 1})
    outcome = await sync.run_output(action_id, titles[::-1], {}, None, True)
    assert outcome.verdicts[99999] == sync.Verdict("skipped", "Not a movie")
    collections.update(action_id, {"max_items": 0})
    outcome = await sync.run_output(action_id, [titles[1]], {}, None, True)
    assert outcome.verdicts[guarding] == sync.Verdict("removed", "No longer in the result")
    assert outcome.counts["skipped"] == 1 and outcome.counts["removed"] == 1
    light_id = collections.for_node(flow_id, "light")["id"]
    collections.update(light_id, {"max_items": 1})
    members = [sync.Title(row["media_id"], {"kind": "movie", "title": row["title"]}, rank) for rank, row in enumerate(
        db.get().query("SELECT ci.media_id, m.title FROM collection_items ci JOIN media_cache m ON m.id=ci.media_id "
                       "WHERE ci.collection_id=? ORDER BY m.title", (light_id,)), 1)]
    outcome = await sync.run_output(light_id, members, {}, None, True)
    assert sorted((verdict.port, verdict.detail) for verdict in outcome.verdicts.values()) == [
        ("present", "In the collection"), ("removed", "Over the limit of 1")]
    assert outcome.counts["skipped"] == 0 and outcome.counts["removed"] == 1


async def test_visibility_poster_and_other_collections(plex_world):
    world = plex_world
    flow_id = store.create("Trending", trending())
    store.update(flow_id, dry_run=False)
    await engine.run(flow_id)
    collection = collections.for_node(flow_id, "col")
    made = world.plex.find_collection(f"Trending {month()}")
    assert made["hub"] is None  # nothing to show: no entry in Manage Recommendations

    collections.update(collection["id"], {"show_home": 1, "show_recommended": 1})
    await sync.apply_visibility(collection["id"])
    assert made["hub"] == {"promotedToRecommended": True, "promotedToOwnHome": True, "promotedToSharedHome": False}
    collections.update(collection["id"], {"show_recommended": 0})
    await sync.apply_visibility(collection["id"])
    assert made["hub"]["promotedToRecommended"] is False

    name = posters.save(collection["id"], poster_for("collection"))
    collections.update(collection["id"], {"poster": name, "poster_source": "upload"})
    await sync.push(collection["id"])
    assert made["poster"] == poster_for("collection")
    assert collections.get(collection["id"])["poster_applied"] == made["ratingKey"]
    calls = len(world.plex.collection_calls)
    await sync.push(collection["id"])  # nothing changed: nothing is sent again
    assert world.plex.collection_calls[calls:] == []

    # Collections from Kometa or made by hand are listed apart and can be deleted; ours cannot.
    grinch = next(key for key, item in world.plex.items.items() if item["title"] == "The Grinch")
    kometa = world.plex.add_collection("1", "Kometa Christmas", [grinch], labels=("Kometa",))
    smart = world.plex.add_collection("1", "Unwatched", [], smart=True)
    others = await sync.others()
    assert [(row["title"], row["count"], row["smart"], row["labels"]) for row in others] == [
        ("Kometa Christmas", 1, False, ["Kometa"]), ("Unwatched", 0, True, [])]
    result = await sync.delete_others([kometa, made["ratingKey"], smart])
    assert result == {"deleted": 2, "failed": 0, "message": None}
    assert list(world.plex.collections) == [made["ratingKey"]]


async def test_collection_deleted_in_plex_is_made_again(plex_world):
    world = plex_world
    flow_id = store.create("Trending", trending())
    store.update(flow_id, dry_run=False)
    await engine.run(flow_id)
    first = world.plex.find_collection(f"Trending {month()}")["ratingKey"]
    del world.plex.collections[first]
    result = await engine.run(flow_id)
    assert trace(result["run"], "col")["Guarding Stars"] == ("added", "")
    again = world.plex.find_collection(f"Trending {month()}")
    assert again["ratingKey"] != first and collections.for_node(flow_id, "col")["plex_key"] == again["ratingKey"]

    # Plex down while writing: the titles count as failed, the next run catches up.
    world.plex.up = False
    failed = await engine.run(flow_id)
    assert failed["outcome"] == "partial"
    assert collections.for_node(flow_id, "col")["error"]
    world.plex.up = True
    await engine.run(flow_id)
    assert collections.for_node(flow_id, "col")["error"] is None


# -- API ---------------------------------------------------------------------------------------------

def test_api(plex_world, client):  # noqa: F811
    world = plex_world
    flow_id = client.post("/api/flows", json={"name": "Trending", "graph": trending()}).json()["id"]
    assert client.get("/api/plex/libraries").json()["libraries"] == [
        {"key": "1", "title": "Filme", "type": "movie"}, {"key": "2", "title": "Serien", "type": "show"}]
    client.put(f"/api/flows/{flow_id}", json={"dry_run": False})
    job = wait_job(client, client.post(f"/api/flows/{flow_id}/run", json={}).json()["job"])
    assert job["status"] == "done", job

    listing = client.get("/api/collections").json()["collections"]
    assert len(listing) == 1
    row = listing[0]
    assert (row["title"], row["library"], row["kind"], row["flow_name"], row["live"], row["in_flow"]) == (
        f"Trending {month()}", "Filme", "movie", "Trending", True, True)
    assert row["counts"] == {"in_plex": 2, "queued": 1, "missing": 0}
    assert client.get(row["image"]).headers["content-type"] == "image/jpeg"  # Plex' composite
    assert client.get("/api/status").json()["counts"]["queue"] == 1

    detail = client.get(f"/api/collections/{row['id']}").json()
    assert [(t["media"]["title"], t["state"]) for t in detail["titles"]] == [
        ("Guarding Stars", "in_plex"), ("The Grinch", "in_plex"), ("Steps", "queued")]
    shown = client.put(f"/api/collections/{row['id']}", json={"show_home": True}).json()
    assert shown["show_home"] is True and shown["show_recommended"] is False
    made = world.plex.find_collection(f"Trending {month()}")
    assert made["hub"]["promotedToOwnHome"] is True

    queue = client.get("/api/queue").json()
    assert queue["days"] == 30 and [(e["media"]["title"], e["service"]) for e in queue["entries"]] == [("Steps", "radarr")]
    entry = queue["entries"][0]
    assert client.delete(f"/api/queue/{entry['collection_id']}/{entry['media']['id']}").json()["ok"]
    assert client.get("/api/queue").json()["entries"] == []
    assert client.delete(f"/api/queue/{entry['collection_id']}/{entry['media']['id']}").status_code == 404

    # A poster from ThePosterDB's collection section
    page = world.tpdb.add_item("Trending Movies", None, "collection")
    poster = world.tpdb.add_poster(page, "Trending Movies", "DIIIVOY", None, kind="collection")
    results = client.get(f"/api/collections/{row['id']}/tpdb", params={"q": "trending"}).json()
    assert [(r["title"], r["year"]) for r in results["results"]] == [("Trending Movies", None)]
    images = client.get(f"/api/collections/{row['id']}/tpdb/posters", params={"url": results["results"][0]["url"]}).json()
    assert [image["designer"] for image in images["images"]] == ["DIIIVOY"]
    assert client.get(images["images"][0]["thumb"]).status_code == 200
    job = wait_job(client, client.post(f"/api/collections/{row['id']}/poster",
                                       json={"token": images["images"][0]["token"]}).json()["job"])
    assert job["status"] == "done" and job["result"]["message"] == "Poster set", job
    assert made["poster"] == poster_for(world.tpdb.image_name(poster), "Trending Movies"[-8:])  # the full image
    after = client.get(f"/api/collections/{row['id']}").json()
    assert after["poster_source"] == "tpdb" and after["image"] != row["image"]
    assert client.get(after["image"]).headers["content-type"] == "image/jpeg"
    # An upload works the same way
    upload = client.post("/api/upload", params={"kind": "collection"},
                         files={"file": ("poster.png", poster_for("mine"), "image/jpeg")}).json()
    job = wait_job(client, client.post(f"/api/collections/{row['id']}/poster", json={"token": upload["token"]}).json()["job"])
    assert job["status"] == "done" and made["poster"] == poster_for("mine")
    assert client.post(f"/api/collections/{row['id']}/poster", json={"token": "forged"}).status_code == 400

    # Other collections, and deleting them
    grinch = next(key for key, item in world.plex.items.items() if item["title"] == "The Grinch")
    kometa = world.plex.add_collection("1", "Christmas", [grinch], labels=("Kometa",))
    other = client.get("/api/collections/other").json()
    assert [(c["title"], c["library"], c["count"]) for c in other["collections"]] == [("Christmas", "Filme", 1)]
    assert other["collections"][0]["image"].startswith("/img/plex?path=")
    assert client.get(other["collections"][0]["image"]).headers["content-type"] == "image/jpeg"
    assert client.get("/img/plex", params={"path": "/library/sections/1/refresh"}).headers["content-type"] == "image/svg+xml"
    job = wait_job(client, client.post("/api/collections/other/delete", json={"keys": [kometa, made["ratingKey"]]}).json()["job"])
    assert job["result"]["deleted"] == 1 and kometa not in world.plex.collections

    # A collection still in its flow cannot be deleted; once its output is gone it can.
    assert client.delete(f"/api/collections/{row['id']}").status_code == 409
    flow = client.get(f"/api/flows/{flow_id}").json()
    graph_ = {"nodes": [node for node in flow["graph"]["nodes"] if node["type"] != "collection"], "edges": []}
    client.put(f"/api/flows/{flow_id}", json={"graph": graph_})
    gone = client.get("/api/collections").json()["collections"][0]
    assert gone["in_flow"] is False and gone["live"] is False
    assert client.delete(f"/api/collections/{row['id']}", params={"keep": True}).json()["message"] == "Removed from Programarr"
    assert world.plex.find_collection(f"Trending {month()}") is not None  # kept in Plex
    assert client.get("/api/collections").json()["collections"] == []


def test_settings_and_events(world, client):  # noqa: F811
    view = client.get("/api/settings").json()
    assert view["values"]["queue_days"] == 30
    assert "collection" in {event["key"] for event in view["discord_events"]}
    client.post("/api/settings", json={"queue_days": "7"})
    assert settings.get("queue_days") == 7 and collections.queue_days() == 7
