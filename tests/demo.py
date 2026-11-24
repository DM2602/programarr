"""Run Programarr against simulated services: python -m tests.demo [--port 8796]

Plex, MediUX, ThePosterDB, TMDB, fanart.tv, TVmaze, Discord, FlixPatrol, MDBList and
Trakt are simulated in the same process. The media folders live under ./demo-data/media.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import shutil
from pathlib import Path

from PIL import Image


def degrade(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB").resize((500, 750))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=72)
    return out.getvalue()


async def build(world) -> None:
    from app import db
    from app.posters import binding, catalog, detect, library, tasks
    from tests.fakes.images import poster_for

    plex, mediux, tpdb = world.plex, world.mediux, world.tpdb
    severance = plex.add_show("Severance", 2022, "95396", "371980", seasons=[1, 2])
    mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1, 2], popularity=40)
    mediux.add_show_set("95396", "Severance", 2022, "olivier_pons", [1], popularity=12)
    item = tpdb.add_item("Severance", 2022, "show")
    tpdb.add_show_set(item, "DIIIVOY", [1, 2])
    tpdb.add_show_set(item, "fwlolx", [1, 2])

    andor = plex.add_show("Andor", 2022, "83867", "393189", seasons=[1, 2])
    raw = mediux.add_show_set("83867", "Andor", 2022, "bobaflett", [1, 2], popularity=22)
    root = world.media / "tv" / "Andor (2022)"
    (root / "poster.jpg").write_bytes(degrade(poster_for(mediux.images[raw["show_poster"][0]["id"]], "SHOW")))
    for entry in raw["season_posters"]:
        n = entry["season"]["season_number"]
        (root / f"Season{n:02d}.jpg").write_bytes(degrade(poster_for(mediux.images[entry["id"]], f"S{n}")))

    bear = plex.add_show("The Bear", 2022, "136315", "412429", seasons=[1, 2, 3])
    bear_set = mediux.add_show_set("136315", "The Bear", 2022, "willtong93", [1, 2], popularity=30)
    plex.add_show("Slow Horses", 2022, "95480", "386268", seasons=[1, 2, 3, 4])
    mediux.add_show_set("95480", "Slow Horses", 2022, "mikenobbs", [1, 2, 3], popularity=8)
    world.tvmaze.shows["386268"] = "40011"
    world.tvmaze.shows["371980"] = "40012"
    world.tvmaze.status["371980"] = "Ended"  # Severance: ended, every poster a file -> Complete
    plex.add_show("Star Trek: The Next Generation", 1987, "655", "71470", seasons=[1, 2, 3, 4, 5, 6, 7])
    mediux.add_show_set("655", "Star Trek: The Next Generation", 1987, "bobaflett", [1, 2, 3, 4], popularity=9)
    mediux.add_show_set("655", "Star Trek: The Next Generation", 1987, "mikenobbs", [1, 2, 3, 4, 5, 6, 7], popularity=4)

    plex.add_movie("The Grinch", 2018, "360920")  # also in the FlixPatrol TOP 10
    dune = plex.add_movie("Dune", 2021, "438631")
    mediux.add_movie_set("438631", "Dune", 2021, "willtong93", popularity=50)
    arrival = plex.add_movie("Arrival", 2016, "329865")
    (world.media / "movies" / "Arrival (2016)" / "poster.jpg").write_bytes(poster_for("arrival-own", "OWN"))
    lotr = plex.add_movie("Der Herr der Ringe: Die Gefährten", 2001, "120")
    mediux.add_collection_set("119", "Der Herr der Ringe", "bobaflett",
                              [("120", "The Fellowship of the Ring", 2001), ("121", "The Two Towers", 2002)])
    lotr_item = tpdb.add_item("The Lord of the Rings: The Fellowship of the Ring", 2001, "movie")
    tpdb.add_movie_set(lotr_item, "DIIIVOY")

    await world.enable_libraries()
    await library.sync()  # existing titles
    plex.upload(lotr, poster_for("posteria-lotr", "OLD"))  # uploaded with Posteria earlier

    for key in (severance, bear, dune):
        item = db.get().one("SELECT * FROM items WHERE key=?", (key,))
        ranked, _ = await catalog.gather_ranked(item)
        preferred = next(r for r in ranked if r.set.designer == "willtong93")
        await binding.bind(key, preferred.set)
    _ = bear_set, arrival

    # New titles after the first sync
    plex.add_movie("Dune: Part Two", 2024, "693134")
    mediux.add_movie_set("693134", "Dune: Part Two", 2024, "willtong93", popularity=60)
    plex.add_show("Shōgun", 2024, "126308", "419220", seasons=[1])
    mediux.add_show_set("126308", "Shōgun", 2024, "olivier_pons", [1], popularity=15)
    await tasks.sync_and_process()
    await detect.detect_item(andor)
    # Plex has read everything by now; Programarr checks.
    from app.posters import apply

    for key in list(plex.items):
        plex.refresh(key)
    for row in db.get().query("SELECT item_key, id FROM slots WHERE file IS NOT NULL"):
        await apply.verify(row["item_key"], [row["id"]])
    await build_lists(world)
    world.radarr.add_movie(438631, "Dune", "dune-438631")  # the other movies link to Radarr's search
    world.sonarr.add_series(371980, "Severance", "severance", episodes=19, files=10)


async def build_lists(world) -> None:
    """Previews of a FlixPatrol TOP 10, an MDBList list and a Trakt chart, one excluded title."""
    from app.lists import runs, scrape
    from app.media import store

    world.fill_lists()
    scrape.reset()
    await runs.preview("flixpatrol", runs.prepare("flixpatrol", {"platform": "netflix", "region": "switzerland",
                                                                 "sections": ["movies", "shows"], "limit": 10}))
    await runs.preview("mdblist", runs.prepare("mdblist", {"list": "dominik/netflix-ch", "limit": 100}))
    await runs.preview("trakt", runs.prepare("trakt", {"chart": "trending", "type": "movies", "limit": 10}))
    power = store.find("show", 2010)
    await runs.exclude(None, None, power["id"], "Seen it")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--data", default="demo-data")
    args = parser.parse_args()

    root = Path(args.data).resolve()
    if root.exists():
        shutil.rmtree(root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    os.environ["PROGRAMARR_DATA_DIR"] = str(root / "data")
    os.environ.setdefault("PROGRAMARR_SCHEDULER", "0")

    from app import config, db, http, settings
    from tests.fakes.world import World

    config.set_data_dir(root / "data")
    config.ensure_dirs()
    db.init(root / "data" / "programarr.db")
    settings.reset_cache()
    world = World(root)
    http.set_transport(world.router)
    world.configure()
    asyncio.run(build(world))

    import uvicorn

    from app.main import app

    print(f"Programarr demo: http://127.0.0.1:{args.port}  (media folders: {world.media})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
