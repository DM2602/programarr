"""Posters picked in Plex, files changed in the folder, the backup and Plex posters kept as files."""
import time

from app import db, imaging, jobs, settings
from app.posters import apply, backup, binding, catalog, library, paths, reviews, tasks
from app.posters.apply import Assignment
from app.posters.sources.base import ImageRef
from tests.fakes.images import poster_for


def upload_ref(kind, season=None, name="upload-1"):
    from app.posters import sources

    image_id = sources.store_upload(poster_for(name, "UP"))
    return ImageRef("upload", image_id, kind, season)


def agent_poster(key):
    return poster_for(f"agent-{key}.jpg", "AGENT")


def item(key):
    return db.get().one("SELECT * FROM items WHERE key=?", (key,))


def slot(slot_id):
    return db.get().one("SELECT * FROM slots WHERE id=?", (slot_id,))


async def bound_show(world):
    show = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=[1, 2])
    raw = world.mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1, 2])
    await world.enable_libraries()
    await library.sync()
    ranked, _ = await catalog.gather_ranked(item(show))
    result = await binding.bind(show, ranked[0].set)
    assert result["written"] == 3
    return show, raw


# -- picked in Plex ---------------------------------------------------------------------
async def test_poster_chosen_in_plex_becomes_the_file(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    world.plex.choose(movie, "agent")  # picked in Plex: selected and locked
    summary = await tasks.sync_and_process()
    assert summary["plex_choices"] == 1
    assert imaging.strip_stamp(poster.read_bytes()) == agent_poster(movie)
    assert world.plex.selected_is_local(movie) and "thumb" not in world.plex.items[movie]["locked"]
    last = db.get().one("SELECT * FROM history WHERE slot_id=? ORDER BY id DESC LIMIT 1", (f"m:{movie}",))
    assert last["action"] == "unlock"
    write = db.get().one("SELECT * FROM history WHERE slot_id=? AND action='write' ORDER BY id DESC LIMIT 1",
                         (f"m:{movie}",))
    assert write["origin"] == "plex" and write["archived"]  # the file before is in the archive
    assert slot(f"m:{movie}")["mode"] == "manual"
    # Plex shows the file now: nothing more to take
    summary = await tasks.sync_and_process()
    assert "plex_choices" not in summary


async def test_upload_into_plex_unlinks_the_season_from_its_set(world):
    show, raw = await bound_show(world)
    season = world.plex.season_key(show, 1)
    world.plex.upload(season, poster_for("posteria-s1", "S1"))
    summary = await tasks.sync_and_process()
    assert summary["plex_choices"] == 1
    path = world.media / "tv" / "Severance (2022)" / "Season 01" / "Season01.jpg"
    assert imaging.strip_stamp(path.read_bytes()) == poster_for("posteria-s1", "S1")
    assert slot(f"S:{show}:1")["mode"] == "manual"
    assert world.plex.selected_is_local(season)
    # The set does not take the season back
    world.mediux.change_image(raw["season_posters"][0], "willtong93-95396-s1-neu")
    result = await binding.check_binding(show)
    assert result["applied"] == 0
    assert imaging.strip_stamp(path.read_bytes()) == poster_for("posteria-s1", "S1")


async def test_lock_on_the_file_itself_is_released(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    world.plex.items[movie]["locked"].add("thumb")
    assert await apply.take_plex_choice(movie, f"m:{movie}") == "unlocked"
    assert "thumb" not in world.plex.items[movie]["locked"]


async def test_plex_falling_back_to_its_own_poster_reads_the_file_again(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    world.plex.choose(movie, "agent")
    world.plex.items[movie]["locked"].discard("thumb")  # shows the agent poster, nothing locked
    summary = await tasks.sync_and_process()
    assert summary["refreshed"] == 1 and "plex_choices" not in summary
    assert world.plex.selected_is_local(movie)
    assert imaging.strip_stamp((world.media / "movies" / "Dune (2021)" / "poster.jpg").read_bytes()) == \
        poster_for("upload-1", "UP")


async def test_nothing_is_taken_with_automation_off(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    settings.set("automation", False)
    world.plex.choose(movie, "agent")
    summary = await tasks.sync_and_process()
    assert "plex_choices" not in summary
    assert not (world.media / "movies" / "Dune (2021)" / "poster.jpg").exists()


# -- files changed in the folder ---------------------------------------------------------
async def test_file_added_in_the_folder_is_shown_in_plex(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    (world.media / "movies" / "Dune (2021)" / "poster.jpg").write_bytes(poster_for("by-hand"))
    assert not world.plex.selected_is_local(movie)
    result = await tasks.check_folders()
    assert result["refreshed"] == 1
    assert world.plex.selected_is_local(movie)
    assert slot(f"m:{movie}")["source"] == "extern"


async def test_folders_are_read_only_when_due(world):
    now = time.time()
    assert "folders" in jobs.due(now)
    await tasks.check_folders()
    assert "folders" not in jobs.due(time.time() + 3600)
    assert "folders" in jobs.due(time.time() + 25 * 3600)
    settings.set("folder_check_hours", 0)
    assert "folders" not in jobs.due(time.time() + 100 * 3600)


async def test_title_named_by_radarr_gets_its_poster_back(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    await tasks.sync_and_process()  # Plex reports the new poster once
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    poster.unlink()  # Radarr upgraded the release and cleared the folder
    assert "restored" not in await tasks.sync_and_process()  # Plex changed nothing: the folder is not read
    assert not poster.exists()
    assert tasks.name_title("movie", {"tmdb_id": 438631, "imdb_id": None})
    assert not tasks.name_title("movie", {"tmdb_id": None})
    summary = await tasks.sync_and_process()
    assert summary["restored"] == 1
    assert imaging.strip_stamp(poster.read_bytes()) == poster_for("upload-1", "UP")
    assert world.plex.selected_is_local(movie)


async def test_new_release_in_plex_reads_the_folder(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    await tasks.sync_and_process()
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    poster.unlink()  # Radarr upgraded the release; Plex has the new file
    world.plex.items[movie]["file"] = world.plex.items[movie]["file"].replace(".mkv", " 2160p.mkv")
    summary = await tasks.sync_and_process()
    assert summary["restored"] == 1 and poster.exists()


# -- backup -------------------------------------------------------------------------------
async def test_written_posters_are_backed_up_by_id(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    outcomes = await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    copy = backup.find(item(movie), "movie", None)
    assert copy is not None and copy.parent.name == "movie-tmdb-438631"
    assert copy.read_bytes() == (world.media / "movies" / "Dune (2021)" / "poster.jpg").read_bytes()
    # Another poster replaces the copy, the same one is not copied again
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie", name="upload-2"))], "manual", "manual")
    copies = list(copy.parent.iterdir())
    assert len(copies) == 1 and copies[0] != copy
    assert not backup.save_bytes(item(movie), "movie", None, copies[0].read_bytes(), ".jpg")
    assert outcomes[0].ok and backup.stats()["files"] == 1


async def test_files_from_before_are_backed_up_in_maintenance(world):
    show = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=[1])
    root = world.media / "tv" / "Severance (2022)"
    (root / "poster.jpg").write_bytes(poster_for("a"))
    (root / "Season 01" / "Season01.jpg").write_bytes(poster_for("b"))
    await world.enable_libraries()
    await library.sync()
    result = await tasks.maintenance()
    assert result["backup"] == {"saved": 2, "checked": 2, "failed": 0}
    folder = backup.title_dir(item(show))
    assert folder.name == "show-tvdb-371980"
    assert sorted(p.name.split("-")[0] for p in folder.iterdir()) == ["poster", "season"]
    assert (await tasks.maintenance())["backup"]["saved"] == 0


async def test_deleted_poster_comes_back_from_the_backup(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    poster.unlink()
    result = await tasks.check_folders()
    assert result["restored"] == 1
    assert imaging.strip_stamp(poster.read_bytes()) == poster_for("upload-1", "UP")
    stamp = imaging.read_stamp_file(poster)
    assert stamp["src"] == "upload" and stamp["mode"] == "manual"
    last = db.get().one("SELECT * FROM history WHERE slot_id=? AND action='write' ORDER BY id DESC LIMIT 1",
                        (f"m:{movie}",))
    assert last["origin"] == "backup"
    assert world.plex.selected_is_local(movie)


async def test_title_added_again_gets_posters_and_set_back(world):
    show, raw = await bound_show(world)
    old_set = binding.get(show)["set_key"]
    # Deleted in Sonarr with its folder, then downloaded again: Plex gives it a new key
    world.plex.remove_item(show)
    root = world.media / "tv" / "Severance (2022)"
    for path in sorted(root.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    root.rmdir()
    await tasks.sync_and_process()
    assert item(show)["gone"] == 1
    again = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=[1, 2])
    summary = await tasks.sync_and_process()
    assert summary["auto"] == {"restored": 1}
    assert (root / "show.jpg").exists() and (root / "Season 01" / "Season01.jpg").exists()
    assert (root / "Season 02" / "Season02.jpg").exists()
    linked = binding.get(again)
    assert linked and linked["set_key"] == old_set
    assert {slot(f"S:{again}:{n}")["mode"] for n in (1, 2)} == {"set"}
    assert reviews.open_items("new_item") == []
    # Still follows the set
    world.mediux.add_season_to_set(raw, 3)
    world.plex.add_season(again, 3)
    summary = await tasks.sync_and_process()
    assert summary["seasons"] == {"applied": 1}


async def test_removed_poster_stays_removed(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    world.plex.items[movie]["addedAt"] = int(time.time()) - 7200
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    assert backup.find(item(movie), "movie", None) is not None
    await apply.remove_file(movie, f"m:{movie}")
    assert backup.find(item(movie), "movie", None) is None
    assert not (await tasks.check_folders()).get("restored")
    result = await tasks.maintenance()
    assert result["plex_files"]["kept"] == 0
    assert not (world.media / "movies" / "Dune (2021)" / "poster.jpg").exists()


async def test_backup_off_restores_nothing(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    settings.set("backup_posters", False)
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    assert not backup.root().exists() or backup.stats()["files"] == 0
    (world.media / "movies" / "Dune (2021)" / "poster.jpg").unlink()
    await tasks.check_folders()
    result = await tasks.maintenance()
    assert not (world.media / "movies" / "Dune (2021)" / "poster.jpg").exists()
    assert result["backup"] == {"saved": 0, "checked": 0}


# -- Plex posters kept as files --------------------------------------------------------------
async def test_titles_without_a_file_keep_the_plex_poster(world):
    old = int(time.time()) - 7200
    dune = world.plex.add_movie("Dune", 2021, "438631")
    arrival = world.plex.add_movie("Arrival", 2016, "329865")
    heat = world.plex.add_movie("Heat", 1995, "949")  # just added: Plex may not have its poster yet
    show = world.plex.add_show("Andor", 2022, "83867", seasons=[1])
    for key in (dune, arrival, show):
        world.plex.items[key]["addedAt"] = old
    await world.enable_libraries()
    await library.sync()
    db.get().execute("UPDATE items SET added_at=? WHERE key=?", (int(time.time()), heat))
    reviews.add(arrival, "new_item", "No sets found", data={"candidates": []})
    result = await tasks.keep_plex_posters()
    assert result == {"kept": 1, "restored": 0, "checked": 1}
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    assert imaging.strip_stamp(poster.read_bytes()) == agent_poster(dune)
    assert world.plex.selected_is_local(dune)
    write = db.get().one("SELECT * FROM history WHERE slot_id=? AND action='write'", (f"m:{dune}",))
    assert write["origin"] == "plex_file"
    assert not (world.media / "movies" / "Arrival (2016)" / "poster.jpg").exists()  # waits under Review
    assert not (world.media / "movies" / "Heat (1995)" / "poster.jpg").exists()
    assert not list((world.media / "tv" / "Andor (2022)").glob("*.jpg"))  # movies only
    # All: shows and seasons too
    settings.set("plex_poster_files", "all")
    result = await tasks.keep_plex_posters()
    assert result["kept"] == 2
    assert (world.media / "tv" / "Andor (2022)" / "show.jpg").exists()
    assert (world.media / "tv" / "Andor (2022)" / "Season 01" / "Season01.jpg").exists()
    settings.set("plex_poster_files", "off")
    assert await tasks.keep_plex_posters() == {"kept": 0, "restored": 0, "checked": 0}


async def test_copy_in_the_backup_comes_before_the_plex_poster(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    world.plex.items[movie]["addedAt"] = int(time.time()) - 7200
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    poster = world.media / "movies" / "Dune (2021)" / "poster.jpg"
    # Deleted while automation was off
    settings.set("automation", False)
    poster.unlink()
    await tasks.check_folders()
    assert not poster.exists()
    settings.set("automation", True)
    result = await tasks.keep_plex_posters()
    assert result == {"kept": 0, "restored": 1, "checked": 1}
    assert imaging.strip_stamp(poster.read_bytes()) == poster_for("upload-1", "UP")


async def test_take_posters_from_plex_job(world):
    old = int(time.time()) - 7200
    movie = world.plex.add_movie("Dune", 2021, "438631")
    show = world.plex.add_show("Andor", 2022, "83867", seasons=[1])
    arrival = world.plex.add_movie("Arrival", 2016, "329865")
    world.plex.items[arrival]["addedAt"] = old
    world.plex.add_movie("Heat", 1995, "949")  # just added: stays as it is
    await world.enable_libraries()
    await library.sync()
    world.plex.upload(movie, poster_for("posteria-dune", "OLD"))
    world.plex.upload(world.plex.season_key(show, 1), poster_for("posteria-andor-s1", "S1"), lock=False)
    result = await tasks.take_from_plex()
    assert result["adopted"] == 2 and result["kept"] == 1 and result["failed"] == 0
    assert imaging.strip_stamp((world.media / "movies" / "Dune (2021)" / "poster.jpg").read_bytes()) == \
        poster_for("posteria-dune", "OLD")
    assert (world.media / "tv" / "Andor (2022)" / "Season 01" / "Season01.jpg").exists()
    assert (world.media / "movies" / "Arrival (2016)" / "poster.jpg").exists()
    assert not (world.media / "movies" / "Heat (1995)" / "poster.jpg").exists()
    assert world.plex.selected_is_local(movie) and "thumb" not in world.plex.items[movie]["locked"]
    upload = db.get().one("SELECT * FROM history WHERE slot_id=? AND action='write'", (f"m:{movie}",))
    assert upload["origin"] == "adopt"
    assert paths.archive_dir(f"m:{movie}").exists() is False
