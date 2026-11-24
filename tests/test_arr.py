"""Radarr and Sonarr links on the title pages, and their connection tests."""
from app import settings
from tests.test_web import client, setup_library  # noqa: F401 - fixture and helper


def test_links_to_radarr_and_sonarr(world, client):  # noqa: F811
    other = world.plex.add_movie("Arrival", 2016, "329865")
    show, movie = setup_library(world, client)
    world.radarr.add_movie(438631, "Dune", "dune-438631", has_file=True)
    world.sonarr.add_series(371980, "Severance", "severance", episodes=19, files=10)
    dune = client.get(f"/api/items/{movie}/arr").json()
    assert dune == {"service": "radarr", "label": "Radarr", "present": True, "monitored": True, "downloaded": True,
                    "url": "https://radarr.example.ch/movie/dune-438631", "error": None}
    severance = client.get(f"/api/items/{show}/arr").json()
    assert severance["url"] == "http://sonarr.test:8989/series/severance"  # no external address: the API one
    assert severance["present"] and severance["downloaded"] is False  # 10 of 19 episodes
    # Not in Radarr: the link opens Radarr's search to add it.
    missing = client.get(f"/api/items/{other}/arr").json()
    assert missing["present"] is False and missing["url"] == "https://radarr.example.ch/add/new?term=tmdb%3A329865"
    # Problems show next to the link; without Sonarr set up there is nothing.
    settings.set("radarr_key", "wrong")
    assert client.get(f"/api/items/{movie}/arr").json()["error"] == "Radarr rejects the API key"
    settings.set("sonarr_url", "")
    assert client.get(f"/api/items/{show}/arr").json() == {"service": None, "label": None, "present": False,
                                                          "monitored": None, "downloaded": None, "url": None,
                                                          "error": None}


def test_connection_tests(world, client):  # noqa: F811
    assert client.post("/api/test/radarr", json={}).json() == {"ok": True, "message": "Connected to Radarr 5.14.0"}
    assert client.post("/api/test/sonarr", json={}).json() == {"ok": True, "message": "Connected to Sonarr 4.0.11"}
    wrong = client.post("/api/test/sonarr", json={"value": "nope"}).json()
    assert wrong == {"ok": False, "message": "Sonarr rejects the API key"}
    other = client.post("/api/test/radarr", json={"extra": "http://radarr2.test:7878"}).json()
    assert other == {"ok": False, "message": "Radarr answered 502"}
    values = client.get("/api/settings").json()["values"]
    assert values["radarr_key"] is True and values["radarr_external_url"] == "https://radarr.example.ch"
    saved = client.post("/api/settings", json={"sonarr_external_url": "https://sonarr.example.ch/"}).json()
    assert saved["saved"] == ["sonarr_external_url"] and settings.get("sonarr_external_url") == "https://sonarr.example.ch"
