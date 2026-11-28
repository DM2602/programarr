"""JSON API, images, webhooks, sign-in and the web app shell through the real FastAPI app."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, settings
from app.posters import reviews, tasks
from tests.fakes.images import poster_for

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(world):
    config.SCHEDULER = False
    from app.main import create_app

    with TestClient(create_app(), headers={"X-Programarr": "1"}) as test_client:
        yield test_client
    config.APP_PASSWORD = ""


def wait_job(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("Job runs too long")


def run(client, method, url, **kwargs):
    response = client.request(method, url, **kwargs)
    assert response.status_code == 200, response.text
    return wait_job(client, response.json()["job"])


def setup_library(world, client):
    show = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=[1, 2])
    world.mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1, 2])
    movie = world.plex.add_movie("Dune", 2021, "438631")
    libs = client.post("/api/libraries/refresh").json()["libraries"]
    client.post("/api/libraries", json={"enabled": [lib["key"] for lib in libs]})
    job = run(client, "POST", "/api/setup/finish")
    assert job["status"] == "done", job
    return show, movie


def test_setup_then_read_endpoints(world, client):
    settings.set("setup_done", False)
    overview = client.get("/api/overview").json()
    assert overview["setup_done"] is False and overview["steps"]["plex"] and not overview["steps"]["libraries"]
    show, movie = setup_library(world, client)
    assert settings.get("setup_done")
    overview = client.get("/api/overview").json()
    facts = overview["facts"]
    assert {key: facts[key] for key in ("movies", "shows", "linked", "complete", "exclusions")} == \
        {"movies": 1, "shows": 1, "linked": 0, "complete": 0, "exclusions": 0}
    assert facts["backup_files"] == 0 and overview["flows"] == [] and overview["flow_adds"] == []
    assert [row["key"] for row in overview["schedule"]][:3] == ["sync", "setcheck", "folders"]
    assert overview["jobs"][0]["title"] == "First sync with Plex" and overview["jobs"][0]["status"] == "done"
    status = client.get("/api/status").json()
    assert status["counts"]["shows"] == 1 and status["automation"] is True and status["jobs"] == []
    library = client.get("/api/library").json()
    assert [card["title"] for card in library["cards"]] == ["Dune", "Severance"]
    assert library["filters"][0] == {"key": "all", "label": "All"} and library["libraries"]
    assert client.get("/api/library?type=show&status=unlinked&q=sev").json()["total"] == 1
    assert client.get("/api/library?q=nothing").json()["cards"] == []
    detail = client.get(f"/api/items/{show}").json()
    assert detail["item"]["title"] == "Severance" and detail["seasons"] == 2 and detail["binding"] is None
    assert [slot["label"] for slot in detail["slots"]] == ["Show", "Season 1", "Season 2"]
    assert {link["label"] for link in detail["links"]} >= {"TMDB", "MediUX", "TheTVDB"}
    assert client.get("/api/items/nope").status_code == 404
    for path in ("/api/reviews", "/api/pending", "/api/detect/stats", "/api/history", "/api/log",
                 "/api/log?level=warning", "/api/settings", f"/api/items/{movie}"):
        assert client.get(path).status_code == 200, path
    assert client.get("/health").json()["ok"]


def test_pick_set_and_upload_through_api(world, client):
    show, movie = setup_library(world, client)
    sets = client.get(f"/api/items/{show}/sets").json()
    assert sets["sets"] and sets["sets"][0]["designer"] == "willtong93"
    assert sets["sets"][0]["complete"] is True and sets["seasons"] == [1, 2]
    job = run(client, "POST", f"/api/items/{show}/bind", json={"token": sets["sets"][0]["token"]})
    assert job["result"]["written"] == 3
    root = world.media / "tv" / "Severance (2022)"
    assert (root / "Season 02" / "Season02.jpg").exists() and (root / "show.jpg").exists()
    # Previews of the set images come through the app
    thumb = client.get(sets["sets"][0]["images"][0]["thumb"])
    assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/")
    upload = client.post("/api/upload?kind=movie", files={"file": ("p.jpg", poster_for("mine", "MINE"), "image/jpeg")})
    assert upload.status_code == 200, upload.text
    job = run(client, "POST", f"/api/items/{movie}/apply", json={"slot": f"m:{movie}", "token": upload.json()["token"]})
    assert job["result"]["outcomes"][0]["ok"]
    assert (world.media / "movies" / "Dune (2021)" / "poster.jpg").exists()
    assert client.get(f"/img/slot/m:{movie}").headers["content-type"] == "image/jpeg"
    detail = client.get(f"/api/items/{show}").json()
    assert detail["binding"]["set_title"] == "Severance" and detail["binding"]["source_label"] == "MediUX"
    season = next(slot for slot in detail["slots"] if slot["id"] == f"S:{show}:1")
    assert season["file_name"] == "Season01.jpg" and season["folder_name"] == "Season 01"
    assert season["mode_label"] == "linked set" and season["state_text"], season
    assert detail["history"][0]["action_label"] == "Poster set"
    recent = client.get("/api/overview").json()["recent"]
    assert recent[0]["title"] == "Dune" and recent[1]["what"] == "Show and 2 seasons"
    linked = client.get("/api/library?status=linked").json()["cards"]
    assert [card["title"] for card in linked] == ["Severance"] and linked[0]["designer"] == "willtong93"


def test_tampered_tokens_and_bad_uploads(world, client):
    show, _ = setup_library(world, client)
    assert client.post(f"/api/items/{show}/apply", json={"slot": f"s:{show}", "token": "abc"}).status_code == 400
    assert client.post(f"/api/items/{show}/bind", json={"token": "abc.def"}).status_code == 400
    response = client.post(f"/api/items/{show}/apply", json={"slot": "s:999", "token": "x"})
    assert response.status_code == 400 and response.json()["detail"] == "Slot belongs to another title"
    assert client.post(f"/api/items/{show}/apply", json={"slot": "nonsense", "token": "x"}).status_code == 400
    response = client.post("/api/upload?kind=show", files={"file": ("x.txt", b"no image", "text/plain")})
    assert response.status_code == 400 and "image" in response.json()["detail"].lower()
    assert client.get("/img/ref/garbage").headers["content-type"] == "image/svg+xml"


def test_plex_and_online_tabs(world, client):
    show, movie = setup_library(world, client)
    plex = client.get(f"/api/items/{movie}/plex?slot=m:{movie}").json()
    assert plex["images"] and plex["images"][0]["selected"]
    online = client.get(f"/api/items/{show}/online?slot=S:{show}:1").json()["sources"]
    assert [source["key"] for source in online] == ["tmdb", "fanart", "tvmaze"]
    tmdb = online[0]
    assert tmdb["enabled"] and tmdb["label"] == "TMDB" and tmdb["images"] and tmdb["error"] is None


def test_settings_keep_secrets_and_clean_lists(world, client):
    response = client.post("/api/settings", json={
        "plex_token": "",
        "poll_minutes": "30",
        "designers": [{"source": "mediux", "name": " @willtong93 "}, {"source": "x", "name": "DIIIVOY"},
                      {"source": "mediux", "name": "willtong93"}, {"name": ""}],
        "path_maps": [{"plex": "/data/media/", "local": "/media"}, {"plex": "", "local": "/x"}],
        "discord_events": {"auto": False, "season": True, "unknown": True},
        "source_order": ["tpdb"],
        "hook_token": "not changeable",
        "automation": False,
    })
    assert response.status_code == 200, response.text
    assert "hook_token" not in response.json()["saved"]
    assert settings.get("plex_token") == world.plex.token
    assert settings.get("poll_minutes") == 30 and settings.get("automation") is False
    assert settings.get("designers") == [{"source": "mediux", "name": "willtong93"}, {"source": "any", "name": "DIIIVOY"}]
    assert settings.get("path_maps") == [{"plex": "/data/media/", "local": "/media"}]
    assert settings.get("discord_events")["auto"] is False and "unknown" not in settings.get("discord_events")
    assert settings.get("source_order") == ["tpdb", "mediux"]
    assert settings.get("hook_token") != "not changeable"
    client.post("/api/settings", json={"tmdb_key": False})
    assert settings.get("tmdb_key") == ""
    assert client.post("/api/settings", json={"naming_show": "elsewhere"}).status_code == 400
    assert client.post("/api/settings", json={"archive_keep": "many"}).status_code == 400
    view = client.get("/api/settings")
    assert world.mediux.token not in view.text and world.plex.token not in view.text
    values = view.json()["values"]
    assert values["plex_token"] is True and values["tmdb_key"] is False and values["mediux_token"] is True
    hooks = view.json()["hooks"]
    assert hooks["sonarr"].endswith(f"/api/hooks/sonarr?token={settings.get('hook_token')}")


def test_connection_tests(world, client):
    assert client.post("/api/test/plex", json={}).json()["ok"]
    assert "dominik" in client.post("/api/test/mediux", json={}).json()["message"]
    assert client.post("/api/test/tpdb", json={}).json()["ok"]
    assert client.post("/api/test/tmdb", json={}).json()["ok"]
    assert client.post("/api/test/discord", json={}).json()["ok"]
    assert world.discord.titles()[-1] == "Programarr is connected"
    assert len(world.discord.avatars) == 1 and world.discord.avatars[0].startswith("data:image/png;base64,")
    assert world.discord.messages[-1]["username"] == "Programarr"
    assert client.post("/api/test/discord", json={}).json()["ok"]
    assert len(world.discord.avatars) == 1  # once per webhook
    bad = client.post("/api/test/plex", json={"value": "wrong"}).json()
    assert not bad["ok"] and "token" in bad["message"].lower()
    assert client.post("/api/test/nothing", json={}).status_code == 404


def test_webhooks_need_token(world, client):
    token = settings.get("hook_token")
    assert client.post("/api/hooks/sonarr", json={"eventType": "Test"}).status_code == 403
    assert client.post("/api/hooks/sonarr?token=wrong", json={"eventType": "Test"}).status_code == 403
    response = client.post(f"/api/hooks/sonarr?token={token}", json={"eventType": "Test"})
    assert response.json()["message"] == "Programarr received the test"
    response = client.post(f"/api/hooks/radarr?token={token}",
                           json={"eventType": "Download", "movie": {"title": "Dune", "tmdbId": 438631}})
    assert response.status_code == 200
    events = db.get().query("SELECT message FROM events WHERE area='webhook'")
    assert any("Radarr Download (Dune)" in e["message"] for e in events)
    assert tasks._named[-1] == ("movie", {"tmdb_id": "438631"})
    client.post(f"/api/hooks/sonarr?token={token}",
                json={"eventType": "Download", "series": {"title": "Andor", "tvdbId": 393189, "imdbId": "tt9253284"}})
    assert tasks._named[-1] == ("show", {"tvdb_id": "393189", "imdb_id": "tt9253284"})
    response = client.post(f"/api/hooks/plex?token={token}",
                           data={"payload": '{"event": "library.new", "Metadata": {"title": "X"}}'})
    assert response.status_code == 200


def test_password_protects_data_but_not_the_shell_or_hooks(world, client):
    config.APP_PASSWORD = "secret"
    state = client.get("/api/auth/state").json()
    assert state == {"password_required": True, "signed_in": False, "version": config.VERSION}
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status").json()["detail"] == "Sign in required"
    assert client.get("/img/slot/m:1").status_code == 401
    assert client.get("/library").status_code == 200  # the web app shows its sign-in page
    token = settings.get("hook_token")
    assert client.post(f"/api/hooks/sonarr?token={token}", json={"eventType": "Test"}).status_code == 200
    bad = client.post("/api/auth/login", json={"password": "wrong"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Incorrect password"
    good = client.post("/api/auth/login", json={"password": "secret"})
    assert good.status_code == 200 and good.json()["signed_in"] is True
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/auth/logout").json()["signed_in"] is False
    assert client.get("/api/status").status_code == 401


def test_without_password_everyone_is_signed_in(world, client):
    assert client.get("/api/auth/state").json()["signed_in"] is True
    assert client.post("/api/auth/login", json={"password": "anything"}).json()["signed_in"] is True


def test_review_list_and_actions(world, client):
    show, _ = setup_library(world, client)
    review_id = reviews.add(show, "new_item", "No set from a preferred designer")
    page = client.get("/api/reviews").json()
    assert page["total"] == 1 and page["reviews"][0]["kind_label"] == "New title"
    assert page["reviews"][0]["title"] == "Severance" and page["reviews"][0]["candidates"] == []
    kinds = {kind["key"]: kind["count"] for kind in page["kinds"]}
    assert kinds["new_item"] == 1 and kinds["detect"] == 0
    assert client.get("/api/reviews?kind=detect").json()["reviews"] == []
    assert client.get(f"/api/items/{show}").json()["reviews"][0]["kind_label"] == "New title"
    assert client.post(f"/api/reviews/{review_id}/dismiss").json()["ok"]
    assert not reviews.open_items()
    assert client.post("/api/reviews/999/confirm").status_code == 404
    assert client.post("/api/reviews/confirm-all").json() == {"ok": True, "message": "Linked 0 titles", "count": 0}


def test_review_keeps_the_plex_posters(world, client):
    from app import imaging
    from tests.fakes.images import poster_for

    show, movie = setup_library(world, client)
    movie_review = reviews.add(movie, "new_item", "No set from a preferred designer")
    show_review = reviews.add(show, "new_item", "No set from a preferred designer")
    page = {r["id"]: r for r in client.get("/api/reviews").json()["reviews"]}
    plex = page[movie_review]["current"]
    assert [p["slot"] for p in plex] == [f"m:{movie}"] and plex[0]["large"].endswith("&size=large")
    assert plex[0]["file"] is False
    assert client.get(plex[0]["large"]).status_code == 200
    assert [p["label"] for p in page[show_review]["current"]] == ["Show", "S1", "S2"]
    job = run(client, "POST", f"/api/reviews/{movie_review}/keep-plex")
    assert job["status"] == "done" and job["result"]["kept"] == 1
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    assert imaging.strip_stamp(poster.read_bytes()) == poster_for(f"agent-{movie}.jpg", "AGENT")
    job = run(client, "POST", f"/api/reviews/{show_review}/keep-plex")
    assert job["result"]["kept"] == 3
    assert not reviews.open_items()
    assert client.get(f"/img/slot/m:{movie}?size=large").headers["content-type"] == "image/jpeg"
    assert run(client, "POST", f"/api/reviews/{movie_review}/keep-plex")["status"] == "failed"


def test_keep_the_current_poster(world, client):
    from tests.fakes.images import poster_for

    show, movie = setup_library(world, client)
    review = reviews.add(movie, "new_item", "No set from a preferred designer")
    job = run(client, "POST", f"/api/items/{movie}/keep", json={"slot": f"m:{movie}"})
    assert job["result"]["kept"] == 1 and job["result"]["message"] == "Plex poster kept as file"
    assert (world.media / "movies" / "Dune (2021)" / "poster.jpg").exists()
    assert not reviews.open_items()
    # A file someone put in the folder: kept as it is, the review closes
    season_file = world.media / "tv" / "Severance (2022)" / "Season 01" / "Season01.jpg"
    season_file.write_bytes(poster_for("by-hand"))
    run(client, "POST", f"/api/items/{show}/rescan")
    review = reviews.add(show, "new_season", "No set", season=1)
    current = {p["slot"]: p for p in client.get("/api/reviews").json()["reviews"][0]["current"]}
    assert current[f"S:{show}:1"]["file"] is True
    job = run(client, "POST", f"/api/reviews/{review}/keep-plex")
    assert job["result"]["kept"] == 1 and not reviews.open_items()
    assert season_file.read_bytes() == poster_for("by-hand")
    job = run(client, "POST", f"/api/items/{show}/keep", json={"slot": f"S:{show}:1"})
    assert job["result"]["message"] == "File kept"


def test_new_title_review_offers_signed_sets(world, client):
    show, _ = setup_library(world, client)
    run(client, "POST", f"/api/items/{show}/retry")
    review = client.get("/api/reviews?kind=new_item").json()["reviews"]
    if review:  # the preferred designer's complete set is taken without asking
        candidate = review[0]["candidates"][0]
        assert candidate["token"] and candidate["images"][0]["thumb"].startswith("/img/ref/")
    else:
        assert client.get(f"/api/items/{show}").json()["binding"]["designer"] == "willtong93"


def test_large_preview_of_a_set_image(world, client):
    import io

    from PIL import Image

    show, _ = setup_library(world, client)
    image = client.get(f"/api/items/{show}/sets").json()["sets"][0]["images"][0]
    assert image["large"] == image["thumb"] + "?size=large"
    response = client.get(image["large"])
    assert response.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).width <= 720
    assert client.get(image["large"]).content == response.content
    assert client.get("/img/ref/garbage?size=large").headers["content-type"] == "image/svg+xml"


def test_pending_list_and_dismiss(world, client):
    show, _ = setup_library(world, client)
    world.plex.add_season(show, 3)
    sets = client.get(f"/api/items/{show}/sets").json()
    run(client, "POST", f"/api/items/{show}/bind", json={"token": sets["sets"][0]["token"]})
    run(client, "POST", "/api/sync")
    rows = client.get("/api/pending").json()["rows"]
    assert rows and rows[0]["season"] == 3 and rows[0]["pending"] and rows[0]["set_title"] == "Severance"
    assert client.get("/api/status").json()["counts"]["pending"] == 1
    response = client.post(f"/api/items/{show}/slots/dismiss", json={"slot": f"S:{show}:3"})
    assert response.status_code == 200
    rows = client.get("/api/pending").json()["rows"]
    assert rows[0]["pending"] is False


def test_restore_from_history(world, client):
    show, movie = setup_library(world, client)
    folder = world.media / "movies" / "Dune (2021)"
    (folder / "poster.jpg").write_bytes(poster_for("first", "ONE"))
    upload = client.post("/api/upload?kind=movie", files={"file": ("p.jpg", poster_for("second", "TWO"), "image/jpeg")})
    run(client, "POST", f"/api/items/{movie}/apply", json={"slot": f"m:{movie}", "token": upload.json()["token"]})
    history = client.get("/api/history").json()
    archived = next(row for row in history["rows"] if row["archived"])
    assert history["pages"] == 1 and archived["title"] == "Dune"
    assert client.get(archived["archived"]).headers["content-type"] == "image/jpeg"
    job = run(client, "POST", f"/api/history/{archived['id']}/restore")
    assert job["result"]["outcomes"][0]["ok"]
    from app import imaging

    assert imaging.strip_stamp((folder / "poster.jpg").read_bytes()) == poster_for("first", "ONE")
    assert Path(db.get().scalar("SELECT file FROM slots WHERE id=?", (f"m:{movie}",))).name == "poster.jpg"
    assert client.post("/api/history/9999/restore").status_code == 404


def test_api_changes_need_the_header(world, client):
    response = client.post("/api/sync", headers={"X-Programarr": ""})
    assert response.status_code == 403 and "X-Programarr" in response.json()["detail"]
    token = settings.get("hook_token")
    plain = TestClient(client.app)
    assert plain.post(f"/api/hooks/sonarr?token={token}", json={"eventType": "Test"}).status_code == 200
    assert plain.get("/api/status").status_code == 200
    assert plain.post("/api/auth/login", json={"password": "x"}).status_code == 403


def test_complete_show_in_library_and_details(world, client):
    world.tvmaze.shows["371980"] = "4242"
    world.tvmaze.status["371980"] = "Ended"
    show, movie = setup_library(world, client)
    sets = client.get(f"/api/items/{show}/sets").json()
    assert sets["sets"][0]["covers"] == [1, 2] and sets["sets"][0]["has_main"]
    run(client, "POST", f"/api/items/{show}/bind", json={"token": sets["sets"][0]["token"]})
    run(client, "POST", "/api/sync")
    complete = client.get("/api/library?status=complete").json()["cards"]
    assert [card["title"] for card in complete] == ["Severance"] and complete[0]["complete"]
    assert client.get("/api/library?status=unlinked&type=show").json()["cards"] == []
    detail = client.get(f"/api/items/{show}").json()
    assert detail["complete"] and detail["item"]["show_status"] == "ended"
    assert client.get("/api/overview").json()["facts"]["complete"] == 1
    assert client.get("/api/detect/stats").json()["complete"] == 1


def test_match_existing_stats_and_job(world, client):
    show, _ = setup_library(world, client)
    (world.media / "tv" / "Severance (2022)" / "poster.jpg").write_bytes(poster_for("own", "OWN"))
    run(client, "POST", "/api/rescan")
    stats = client.get("/api/detect/stats").json()
    assert stats["shows"] == 1 and stats["job"] is None
    job = run(client, "POST", "/api/detect?scope=shows")
    assert job["result"]["checked"] == 1
    stats = client.get("/api/detect/stats").json()
    assert stats["job"]["id"] == job["id"] and stats["job"]["status"] == "done"
    assert client.post("/api/detect?scope=everything").status_code == 400


def test_web_app_shell_and_assets(world, client, tmp_path, monkeypatch):
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Programarr</title><div id=root></div>")
    (dist / "assets" / "app-1234.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    monkeypatch.setattr(main, "DIST_DIR", dist)
    for path in ("/", "/library", "/item/123", "/settings/plex"):
        response = client.get(path)
        assert response.status_code == 200 and "<div id=root>" in response.text, path
        assert response.headers["cache-control"] == "no-cache"
    asset = client.get("/assets/app-1234.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/nothing").status_code == 404 and client.get("/api/nothing").json()["detail"]
    assert client.get("/img/nothing").status_code == 404
    assert client.get("/../secret.key").status_code in (200, 404)
    assert "secret" not in client.get("/%2e%2e/secret.key").text


def test_openapi_file_is_current(client):
    """The web app's TypeScript types come from frontend/openapi.json; regenerate it after API changes."""
    committed = json.loads((ROOT / "frontend" / "openapi.json").read_text())
    assert committed == client.app.openapi(), "Run: python -m app.openapi > frontend/openapi.json"
