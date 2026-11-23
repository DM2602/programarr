"""MediUX, ThePosterDB and the other online sources against their simulations."""
import pytest

from app import db, imaging, settings
from app.posters import catalog, library, sources
from app.posters.sources import fanart, mediux, tmdb, tpdb, tvmaze
from app.posters.sources.base import SourceError


async def test_mediux_show_sets_and_set_by_id(world):
    raw = world.mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1, 2])
    world.mediux.add_show_set("95396", "Severance", 2022, "someone", [1])
    info, sets = await mediux.show_sets("95396")
    assert info["title"] == "Severance" and len(sets) == 2
    first = sets[0]
    assert first.set_key == f"show:{raw['id']}" and first.designer == "willtong93"
    assert first.has_show_poster() and first.seasons() == {1, 2}
    assert first.url == f"https://mediux-site.test/sets/{raw['id']}"
    assert "key=thumb" in first.image_for("season", 1).thumb_url
    fresh = await mediux.get_set(first.set_key)
    assert fresh.seasons() == {1, 2}
    data, _ = await mediux.fetch(first.image_for("show"))
    assert imaging.inspect(data)[1:] == (1000, 1500)
    assert await mediux.test() == "dominik"
    assert await mediux.follows() == {"follow": ["willtong93"], "hide": ["hiddenguy"]}


async def test_mediux_unknown_title_and_bad_token(world):
    info, sets = await mediux.show_sets("999")
    assert sets == []
    settings.set("mediux_token", "falsch")
    with pytest.raises(SourceError, match="token"):
        await mediux.show_sets("1", fresh=True)


async def test_mediux_movie_and_collection_sets(world):
    world.mediux.add_movie_set("120", "Die Gefährten", 2001, "willtong93")
    set_id = world.mediux.add_collection_set("119", "Der Herr der Ringe", "bobaflett",
                                             [("120", "Die Gefährten", 2001), ("121", "Die zwei Türme", 2002)])
    info, sets = await mediux.movie_sets("120")
    keys = sorted(s.set_key for s in sets)
    assert keys == sorted([sets[0].set_key, f"collection:{set_id}"])
    assert info["collection_id"] == "119"
    collection = await mediux.get_set(f"collection:{set_id}", "121")
    assert len(collection.images) == 1 and collection.images[0].extra["tmdb"] == "121"


async def test_tpdb_search_sets_and_login_download(world):
    item = world.tpdb.add_item("Severance", 2022, "show")
    set_id = world.tpdb.add_show_set(item, "DIIIVOY", [1, 2], specials=True)
    world.tpdb.add_show_set(item, "other", [1])
    world.tpdb.add_item("Severance", 2018, "show")  # different show with the same name
    url, sure = await tpdb.find_item_page("Severance", 2022, "show")
    assert url.endswith(f"/posters/{item}") and sure
    show = {"key": "1", "type": "show", "title": "Severance", "year": 2022}
    sets = await tpdb.sets_for_item(show, url)
    assert {s.designer for s in sets} == {"DIIIVOY", "other"}
    full = await tpdb.get_set(set_id, show)
    assert full.title == "Severance (2022)" and full.designer == "DIIIVOY"
    assert full.has_show_poster() and full.seasons() == {0, 1, 2}
    preview, _ = await tpdb.fetch(full.image_for("season", 1), thumb=True)
    assert imaging.inspect(preview)[0] == "WEBP"
    data, _ = await tpdb.fetch(full.image_for("season", 1))
    assert imaging.inspect(data)[1:] == (1000, 1500)
    assert world.tpdb.logins == 1
    # The session cookie is kept for the next download
    await tpdb.fetch(full.image_for("season", 2))
    assert world.tpdb.logins == 1


async def test_tpdb_wrong_password_and_challenge(world):
    item = world.tpdb.add_item("Dune", 2021, "movie")
    world.tpdb.add_movie_set(item, "DIIIVOY")
    settings.set("tpdb_password", "falsch")
    movie = {"key": "1", "type": "movie", "title": "Dune", "year": 2021}
    url, _ = await tpdb.find_item_page("Dune", 2021, "movie")
    sets = await tpdb.sets_for_item(movie, url)
    with pytest.raises(SourceError, match="Sign-in"):
        await tpdb.fetch(sets[0].images[0])
    # Cloudflare challenge: without FlareSolverr a clear message, with it the page loads
    world.tpdb.challenge = True
    from app import cache
    cache.drop("tpdb:")
    with pytest.raises(SourceError, match="FlareSolverr"):
        await tpdb.search("Dune", 2021, "movie")
    settings.set("flaresolverr_url", "https://flaresolverr.test")
    results = await tpdb.search("Dune", 2021, "movie")
    assert results and world.flare.calls == 1
    # The clearance cookie from FlareSolverr lets direct requests through afterwards
    cache.drop("tpdb:")
    await tpdb.search("Dune", 2021, "movie")
    assert world.flare.calls == 1


async def test_tpdb_pagination_and_title_filter(world):
    item = world.tpdb.add_item("Doctor Who", 2005, "show")
    for index in range(30):
        world.tpdb.add_show_set(item, f"user{index}", [], show_poster=True)
    settings.set("tpdb_pages", 2)
    cards = await tpdb.item_cards(f"https://tpdb.test/posters/{item}")
    assert len(cards) == 30


async def test_online_sources(world):
    world.tvmaze.shows["371980"] = "4242"
    show = {"key": "1", "type": "show", "title": "Severance", "year": 2022, "tmdb_id": "95396",
            "tvdb_id": "371980", "imdb_id": None}
    movie = {"key": "2", "type": "movie", "title": "Dune", "year": 2021, "tmdb_id": "438631", "imdb_id": None}
    refs = await tmdb.images(movie, "movie", None)
    assert [r.language for r in refs][:2] == ["de", "en"] and refs[0].url.startswith("https://tmdb-img.test/t/p/original/")
    season_refs = await tmdb.images(show, "season", 1)
    assert season_refs and season_refs[0].season == 1
    fan = await fanart.images(movie, "movie", None)
    assert fan[0].language == "textless" and "/preview/" in fan[0].thumb_url
    fan_season = await fanart.images(show, "season", 2)
    assert len(fan_season) == 1
    maze = await tvmaze.images(show, "show", None)
    assert maze and maze[0].label == "Hauptposter"
    maze_season = await tvmaze.images(show, "season", 2)
    assert maze_season[0].url.endswith("-s2.jpg")
    data, _ = await sources.fetch(maze_season[0])
    assert imaging.inspect(data)[0] == "JPEG"
    combined = await sources.online_images(show, "show", None)
    assert set(combined) == {"tmdb", "fanart", "tvmaze"}
    assert await tmdb.test() and await fanart.test()


async def test_plex_posters_as_source(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    refs = await sources.plex_images(movie, "movie", None)
    assert refs[0].extra["selected"] and refs[0].url.startswith("https://tmdb-images.test/")
    data, _ = await sources.fetch(refs[0])
    assert imaging.inspect(data)[0] == "JPEG"


async def test_catalog_uses_english_title_for_tpdb(world):
    # Plex shows the German title; TPDB knows the English one.
    movie = world.plex.add_movie("Der Herr der Ringe: Die Gefährten", 2001, "120")
    world.mediux.add_movie_set("120", "The Lord of the Rings: The Fellowship of the Ring", 2001, "willtong93")
    item_id = world.tpdb.add_item("The Lord of the Rings: The Fellowship of the Ring", 2001, "movie")
    world.tpdb.add_movie_set(item_id, "DIIIVOY")
    await world.enable_libraries()
    await library.sync()
    item = db.get().one("SELECT * FROM items WHERE key=?", (movie,))
    found = await catalog.find_sets(item)
    assert len(found.mediux) == 1 and len(found.tpdb) == 1 and found.tpdb_sure
    item = db.get().one("SELECT * FROM items WHERE key=?", (movie,))
    assert item["search_title"].startswith("The Lord of the Rings") and item["tpdb_url"].endswith(item_id)


async def test_url_source_blocks_private_addresses():
    from app import http

    http.set_transport(None)
    with pytest.raises(http.FetchError, match="own network"):
        await http.fetch_public("http://127.0.0.1/poster.jpg")
    with pytest.raises(http.FetchError, match="http"):
        await http.fetch_public("file:///etc/passwd")


async def test_tpdb_page_set_by_hand_with_other_title(world):
    show = world.plex.add_show("Marvel's Daredevil", 2015, "61889", seasons=[1, 2])
    item_id = world.tpdb.add_item("Daredevil", 2015, "show")
    set_id = world.tpdb.add_show_set(item_id, "DIIIVOY", [1, 2])
    settings.set("mediux_token", "")
    await world.enable_libraries()
    await library.sync()
    item = db.get().one("SELECT * FROM items WHERE key=?", (show,))
    found = await catalog.find_sets(item)
    assert found.tpdb == []  # the search does not find it under the Plex title
    db.get().update("items", "key", show, {"tpdb_url": f"https://tpdb.test/posters/{item_id}", "tpdb_sure": 1})
    item = db.get().one("SELECT * FROM items WHERE key=?", (show,))
    found = await catalog.find_sets(item)
    assert len(found.tpdb) == 1 and found.tpdb_sure
    full = await catalog.complete_tpdb(item, found.tpdb[0])
    assert full.set_key == set_id and full.seasons() == {1, 2} and full.has_show_poster()


def test_tpdb_card_titles_with_dashes():
    from app.posters.sources.tpdb import Card, cards_to_images

    film = Card("1", "Mission: Impossible - Dead Reckoning Part One (2023)", "movie", "x", "9", "", None)
    assert film.season is None and film.base_title == ("Mission: Impossible - Dead Reckoning Part One", 2023)
    images = cards_to_images([film], "movie", ["Mission: Impossible – Dead Reckoning Part One"], 2023, "9", "Set")
    assert len(images) == 1 and images[0].kind == "movie"
    season = Card("2", "Severance (2022) - Season 02", "show", "x", "9", "", None)
    specials = Card("3", "Severance (2022) - Specials", "show", "x", "9", "", None)
    images = cards_to_images([season, specials], "show", "Severance", 2022, "9", "Set")
    assert [(i.kind, i.season) for i in images] == [("season", 0), ("season", 2)]
