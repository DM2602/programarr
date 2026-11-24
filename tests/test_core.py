"""Sync from Plex, file names, writing, archive, locks and Plex verification."""
import sqlite3
from pathlib import Path

import pytest

from app import db, imaging, settings
from app.posters import apply, library, paths
from app.posters.apply import Assignment
from app.posters.sources.base import ImageRef
from tests.fakes.images import poster_for


def upload_ref(kind, season=None, name="upload-1"):
    from app.posters import sources

    image_id = sources.store_upload(poster_for(name, "UP"))
    return ImageRef("upload", image_id, kind, season)


# -- path mapping and names ---------------------------------------------------
def test_path_mapper_longest_prefix():
    mapper = paths.PathMapper([{"plex": "/data", "local": "/a"}, {"plex": "/data/media/tv", "local": "/tv"}])
    assert mapper.to_local("/data/media/tv/Show (2020)") == Path("/tv/Show (2020)")
    assert mapper.to_local("/data/media/movies/X") == Path("/a/media/movies/X")
    assert mapper.to_local("/other/path") == Path("/other/path")
    assert mapper.to_plex("/tv/Show (2020)") == "/data/media/tv/Show (2020)"
    assert mapper.to_local("D:\\Media\\x") == Path("D:/Media/x")


def test_existing_and_target_names(tmp_path):
    show = tmp_path / "Show (2020)"
    season_dir = show / "Season 01"
    season_dir.mkdir(parents=True)
    naming = {"movie": "poster", "show": "poster", "season": "root"}
    place = paths.Place("season", show, season=1, season_folder=season_dir)
    assert paths.target(place, ".jpg", naming) == show / "season01-poster.jpg"
    assert paths.target(place, ".jpg", {**naming, "season": "folder"}) == season_dir / "Season01.jpg"
    assert paths.target(place, ".jpg", {**naming, "season": "root_plain"}) == show / "Season01.jpg"
    specials = paths.Place("season", show, season=0)
    assert paths.target(specials, ".png", naming) == show / "season-specials-poster.png"
    # Specials otherwise follow the seasons: Season00 in "Season 00" (preferred over a "Specials" folder).
    assert paths.target(specials, ".png", {**naming, "season": "root_plain"}) == show / "Season00.png"
    assert paths.target(specials, ".png", {**naming, "season": "folder"}) == show / "Season00.png"  # no folder yet
    (show / "Specials").mkdir()
    (show / "Season 00").mkdir()
    assert paths.target(specials, ".jpg", {**naming, "season": "folder"}) == show / "Season 00" / "Season00.jpg"
    from_plex = paths.Place("season", show, season=0, season_folder=show / "Specials")
    assert paths.target(from_plex, ".jpg", {**naming, "season": "folder"}) == show / "Specials" / "Season00.jpg"
    # An existing file keeps its name and place
    (season_dir / "Season01.jpg").write_bytes(b"x")
    assert paths.existing(place) == [season_dir / "Season01.jpg"]
    assert paths.target(place, ".jpg", naming) == season_dir / "Season01.jpg"
    assert paths.target(place, ".png", naming) == season_dir / "Season01.png"
    # Show posters and their variants
    (show / "folder.jpg").write_bytes(b"x")
    (show / "poster-2.png").write_bytes(b"x")
    show_place = paths.Place("show", show)
    assert [p.name for p in paths.existing(show_place)] == ["poster-2.png", "folder.jpg"]
    assert [p.name for p in paths.conflicts(show_place, show / "poster-2.jpg")] == ["poster-2.png", "folder.jpg"]


def test_movie_names_shared_folder(tmp_path):
    folder = tmp_path / "movies"
    folder.mkdir()
    naming = {"movie": "poster", "show": "poster", "season": "root"}
    own = paths.Place("movie", folder, video_stem="Film (2020)", shared=False)
    shared = paths.Place("movie", folder, video_stem="Film (2020)", shared=True)
    assert paths.target(own, ".jpg", naming) == folder / "poster.jpg"
    assert paths.target(shared, ".jpg", naming) == folder / "Film (2020).jpg"
    (folder / "poster.jpg").write_bytes(b"x")
    assert paths.existing(shared) == []
    assert paths.video_stem("Film (2020) - cd1.mkv") == "Film (2020)"


# -- sync ---------------------------------------------------------------------
async def test_first_sync_is_baseline_then_new_items(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    show = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=[1, 2])
    await world.enable_libraries()
    result = await library.sync()
    assert result.first_sync and not result.new_items and not result.new_seasons
    item = db.get().one("SELECT * FROM items WHERE key=?", (show,))
    assert item["folder"] == "/data/media/tv/Severance (2022)" and item["tmdb_id"] == "95396" and item["tvdb_id"] == "371980"
    assert db.get().scalar("SELECT COUNT(*) FROM slots WHERE item_key=?", (show,)) == 3
    seasons = db.get().query("SELECT number, folder FROM seasons WHERE show_key=? ORDER BY number", (show,))
    assert seasons[0]["folder"].endswith("Severance (2022)/Season 01")
    # Second sync: a new movie and a new season count as new
    new_movie = world.plex.add_movie("Arrival", 2016, "329865")
    world.plex.add_season(show, 3)
    result = await library.sync()
    assert result.new_items == [new_movie]
    assert result.new_seasons == [(show, 3)]
    # A removed item is marked gone
    world.plex.remove_item(movie)
    result = await library.sync()
    assert result.gone == 1
    assert db.get().scalar("SELECT gone FROM items WHERE key=?", (movie,)) == 1


async def test_scan_files_and_naming_detection(world):
    show = world.plex.add_show("Severance", 2022, "95396", seasons=[1])
    root = world.media / "tv" / "Severance (2022)"
    (root / "poster.jpg").write_bytes(poster_for("a"))
    (root / "Season 01" / "Season01.jpg").write_bytes(poster_for("b"))
    await world.enable_libraries()
    await library.sync()
    slot = db.get().one("SELECT * FROM slots WHERE id=?", (f"S:{show}:1",))
    assert slot["file"].endswith("Season 01/Season01.jpg") and slot["source"] == "extern"
    detected = settings.get("naming_detected")
    assert detected["season"] == "folder" and detected["show"] == "poster"
    assert library.naming()["season"] == "folder"


# -- writing --------------------------------------------------------------------
async def test_apply_writes_file_plex_reads_it_and_archive(world):
    show = world.plex.add_show("Severance", 2022, "95396", seasons=[1, 2])
    root = world.media / "tv" / "Severance (2022)"
    (root / "folder.jpg").write_bytes(poster_for("old-folder"))
    (root / "show.png").write_bytes(poster_for("old-show", fmt="PNG"))
    await world.enable_libraries()
    await library.sync()
    world.plex.items[show]["locked"].add("thumb")  # chosen by hand in Plex long ago
    outcomes = await apply.apply(show, [Assignment(f"s:{show}", upload_ref("show"))], "manual", "manual")
    assert outcomes[0].ok, outcomes[0].message
    written = Path(outcomes[0].path)
    assert written == root / "show.jpg"  # keeps the first existing name, extension follows the content
    assert not (root / "folder.jpg").exists() and not (root / "show.png").exists()
    stamp = imaging.read_stamp_file(written)
    assert stamp["src"] == "upload" and stamp["mode"] == "manual"
    # Plex: lock released, refreshed, file selected
    assert show in world.plex.unlocks and show in world.plex.refreshes
    assert world.plex.selected_is_local(show)
    states = await apply.verify(show, [f"s:{show}"])
    assert states[f"s:{show}"] == "ok"
    # Archive and history
    history = db.get().query("SELECT * FROM history WHERE item_key=? AND action='write'", (show,))
    assert len(history) == 1 and history[0]["archived"]
    archived = list(paths.archive_dir(f"s:{show}").iterdir())
    assert {paths.original_name(p) for p in archived} == {"folder.jpg", "show.png"}


async def test_season_posters_use_plex_names(world):
    show = world.plex.add_show("Andor", 2022, "83867", seasons=[0, 1, 2])
    await world.enable_libraries()
    await library.sync()
    assignments = [Assignment(f"S:{show}:{n}", upload_ref("season", n, f"s{n}")) for n in (0, 1, 2)]
    outcomes = await apply.apply(show, assignments, "manual", "manual")
    assert all(o.ok for o in outcomes)
    names = sorted(Path(o.path).name for o in outcomes)
    assert names == ["Season00.jpg", "Season01.jpg", "Season02.jpg"]
    root = world.media / "tv" / "Andor (2022)"
    assert (root / "Season 01" / "Season01.jpg").exists() and (root / "Season 00" / "Season00.jpg").exists()
    for n in (0, 1, 2):
        assert world.plex.selected_is_local(world.plex.season_key(show, n))


async def test_movie_in_shared_folder_gets_file_name(world):
    world.plex.add_movie("Alien", 1979, "348", folder="Sammlung", file_name="Alien (1979).mkv")
    aliens = world.plex.add_movie("Aliens", 1986, "679", folder="Sammlung", file_name="Aliens (1986).mkv")
    await world.enable_libraries()
    await library.sync()
    outcomes = await apply.apply(aliens, [Assignment(f"m:{aliens}", upload_ref("movie"))], "manual", "manual")
    assert outcomes[0].ok
    assert Path(outcomes[0].path).name == "Aliens (1986).jpg"
    assert world.plex.selected_is_local(aliens)


async def test_restore_and_remove(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    folder = world.media / "movies" / "Dune (2021)"
    original = poster_for("original-dune")
    (folder / "poster.jpg").write_bytes(original)
    await world.enable_libraries()
    await library.sync()
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    entry = db.get().one("SELECT id FROM history WHERE item_key=? AND action='write'", (movie,))
    outcomes = await apply.restore(entry["id"])
    assert outcomes[0].ok
    restored = (folder / "poster.jpg").read_bytes()
    assert imaging.strip_stamp(restored) == original
    message = await apply.remove_file(movie, f"m:{movie}")
    assert "archive" in message and not (folder / "poster.jpg").exists()
    world.plex.refresh(movie)
    assert not world.plex.selected_is_local(movie)


async def test_external_change_is_respected(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    outcomes = await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "set", "set")
    Path(outcomes[0].path).write_bytes(poster_for("by-hand"))
    library.scan_item(movie)
    slot = db.get().one("SELECT * FROM slots WHERE id=?", (f"m:{movie}",))
    assert slot["mode"] == "extern" and slot["source"] == "extern"


async def test_plex_not_picking_file_is_reported(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    world.plex.read_local_on_refresh = False
    await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    states = await apply.verify(movie, [f"m:{movie}"], final=True)
    assert states[f"m:{movie}"] == "pending"
    # Plex may still be busy: no alarm within the first hour
    assert db.get().one("SELECT * FROM reviews WHERE item_key=? AND kind='plex'", (movie,)) is None
    db.get().execute("UPDATE slots SET applied_at=applied_at-7200 WHERE id=?", (f"m:{movie}",))
    assert await apply.recheck_pending() == 1
    review = db.get().one("SELECT * FROM reviews WHERE item_key=? AND kind='plex'", (movie,))
    assert review is not None
    world.plex.read_local_on_refresh = True
    message = await apply.force_local(movie, f"m:{movie}")
    assert "file from the folder" in message
    assert world.plex.selected_is_local(movie) and "thumb" not in world.plex.items[movie]["locked"]


async def test_missing_folder_gives_clear_error(world):
    movie = world.plex.add_movie("Dune", 2021, "438631")
    await world.enable_libraries()
    await library.sync()
    settings.set("path_maps", [{"plex": "/data/media", "local": "/nowhere"}])
    outcomes = await apply.apply(movie, [Assignment(f"m:{movie}", upload_ref("movie"))], "manual", "manual")
    assert not outcomes[0].ok and "Folder not found" in outcomes[0].message
    status = library.folder_status()
    assert status["missing"] == 1


def test_scheduler_due(world):
    import time

    from app import jobs

    now = time.time()
    settings.update({"last_sync": now - 60, "last_set_check": now - 3600, "poll_minutes": 15, "set_check_hours": 24})
    assert jobs.due(now) == []
    settings.set("last_sync", now - 16 * 60)
    assert jobs.due(now) == ["sync"]
    settings.set("last_set_check", now - 25 * 3600)
    assert jobs.due(now) == ["sync", "setcheck"]
    settings.set("poll_minutes", 1)  # at least every 5 minutes
    settings.set("last_sync", now - 120)
    assert "sync" not in jobs.due(now)
    settings.set("setup_done", False)
    assert jobs.due(now) == []


async def test_posters_uploaded_into_plex_become_files(world):
    from app.posters import tasks

    movie = world.plex.add_movie("Dune", 2021, "438631")
    show = world.plex.add_show("Andor", 2022, "83867", seasons=[1])
    world.plex.add_movie("Arrival", 2016, "329865")  # Plex default poster: stays as it is
    await world.enable_libraries()
    await library.sync()
    world.plex.upload(movie, poster_for("posteria-dune", "OLD"))
    world.plex.upload(world.plex.season_key(show, 1), poster_for("posteria-andor-s1", "S1"))
    result = await tasks.adopt_uploads()
    assert result["adopted"] == 2 and result["failed"] == 0
    assert imaging.strip_stamp((world.media / "movies" / "Dune (2021)" / "poster.jpg").read_bytes()) == \
        poster_for("posteria-dune", "OLD")
    assert (world.media / "tv" / "Andor (2022)" / "Season 01" / "Season01.jpg").exists()
    assert not (world.media / "movies" / "Arrival (2016)" / "poster.jpg").exists()
    assert world.plex.selected_is_local(movie) and "thumb" not in world.plex.items[movie]["locked"]
    assert world.plex.selected_is_local(world.plex.season_key(show, 1))


def test_fresh_database_and_backup_before_upgrade(tmp_path, monkeypatch):
    database = db.Database(tmp_path / "programarr.db")
    assert database.scalar("PRAGMA user_version") == db.SCHEMA_VERSION == 3
    database.close()
    # A file from version 1: copied before the migration, data kept, the list and flow tables added.
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA_V1 + "\nPRAGMA user_version=1;")
    conn.execute("INSERT INTO items (key, type, title, show_status) VALUES ('1', 'show', 'Old', 'ended')")
    conn.commit()
    conn.close()
    database = db.Database(path)
    assert database.scalar("PRAGMA user_version") == 3
    assert database.one("SELECT title, show_status FROM items WHERE key='1'") == {"title": "Old", "show_status": "ended"}
    assert (tmp_path / "programarr-pre-v1.db").exists()
    tables = {row["name"] for row in database.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"media_cache", "parser_runs", "parser_entries", "parser_overrides", "exclusions",
            "flows", "flow_runs", "flow_trace"} <= tables
    database.close()
    # A file from version 2 (match log in use): only the flow tables come in, the runs stay.
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA_V1 + db.SCHEMA_V2 + "\nPRAGMA user_version=2;")
    conn.execute("INSERT INTO parser_runs (source, source_ref, status) VALUES ('mdblist', 'mdblist:x', 'ok')")
    conn.commit()
    conn.close()
    database = db.Database(path)
    assert database.scalar("PRAGMA user_version") == 3
    assert database.scalar("SELECT COUNT(*) FROM parser_runs") == 1
    assert (tmp_path / "programarr-pre-v2.db").exists()
    database.close()
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
    with pytest.raises(RuntimeError, match="newer Programarr"):
        db.Database(path)


def test_write_works_when_share_refuses_chmod(tmp_path, monkeypatch):
    """NFS/SMB shares with squashed users refuse chmod although writing works (Unraid)."""
    import os as _os

    def refuse(*_args, **_kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(_os, "chmod", refuse)
    target = tmp_path / "show.jpg"
    target.write_bytes(b"old")
    archived = paths.archive(target, "s:1", move=False)
    paths.write_atomic(target, b"new")
    assert target.read_bytes() == b"new" and archived.read_bytes() == b"old"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_write_error_names_step_and_user(tmp_path):
    with pytest.raises(paths.PathError) as info:
        paths.write_atomic(tmp_path / "missing" / "show.jpg", b"x")
    assert "create file" in str(info.value) and "UID" in str(info.value)
