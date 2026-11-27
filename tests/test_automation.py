"""Set links, new seasons, waiting, set changes, new titles, Match existing and Complete."""
import io

from PIL import Image

from app import config, db, settings
from app.posters import binding, catalog, detect, library, reviews, status, tasks
from tests.fakes.images import poster_for


async def setup_show(world, seasons=(1, 2), designer="willtong93", set_seasons=(1, 2)):
    show = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=list(seasons))
    raw = world.mediux.add_show_set("95396", "Severance", 2022, designer, list(set_seasons))
    await world.enable_libraries()
    await library.sync()
    return show, raw


async def bind_first_set(show):
    item = db.get().one("SELECT * FROM items WHERE key=?", (show,))
    ranked, _ = await catalog.gather_ranked(item)
    return await binding.bind(show, ranked[0].set)


async def test_bind_writes_all_and_marks_missing_seasons_waiting(world):
    show, raw = await setup_show(world, seasons=(1, 2, 3), set_seasons=(1, 2))
    result = await bind_first_set(show)
    assert result["written"] == 3 and result["waiting"] == [3]
    root = world.media / "tv" / "Severance (2022)"
    assert sorted(p.name for p in root.glob("*.jpg")) == ["show.jpg"]
    assert (root / "Season 01" / "Season01.jpg").exists() and (root / "Season 02" / "Season02.jpg").exists()
    bound = binding.get(show)
    assert bound["source"] == "mediux" and bound["designer"] == "willtong93"
    assert set(bound["snapshot"]) == {f"s:{show}", f"S:{show}:1", f"S:{show}:2"}
    waiting = db.get().one("SELECT waiting FROM slots WHERE id=?", (f"S:{show}:3",))
    assert waiting["waiting"] == 1


async def test_new_season_comes_from_bound_set(world):
    show, raw = await setup_show(world)
    await bind_first_set(show)
    world.mediux.add_season_to_set(raw, 3)
    world.plex.add_season(show, 3)
    summary = await tasks.sync_and_process()
    assert summary["seasons"] == {"applied": 1}
    path = world.media / "tv" / "Severance (2022)" / "Season 03" / "Season03.jpg"
    assert path.exists()
    assert world.plex.selected_is_local(world.plex.season_key(show, 3))
    assert any(title.startswith("New season: Severance") for title in world.discord.titles())
    assert world.discord.messages[-1]["_files"] == ["files[0]"]


async def test_new_season_waits_then_arrives_with_set_check(world):
    show, raw = await setup_show(world)
    await bind_first_set(show)
    world.plex.add_season(show, 3)
    summary = await tasks.sync_and_process()
    assert summary["seasons"] == {"waiting": 1}
    assert any(title.startswith("Waiting for set") for title in world.discord.titles())
    # Nothing new yet
    result = await binding.check_binding(show)
    assert result["applied"] == 0 and result["waiting"] == 1
    # The designer adds the season to the set
    world.mediux.add_season_to_set(raw, 3)
    result = await binding.check_binding(show)
    assert result["applied"] == 1
    assert (world.media / "tv" / "Severance (2022)" / "Season 03" / "Season03.jpg").exists()
    slot = db.get().one("SELECT waiting FROM slots WHERE id=?", (f"S:{show}:3",))
    assert slot["waiting"] == 0


async def test_changed_image_in_set_is_reported_then_taken(world):
    show, raw = await setup_show(world)
    await bind_first_set(show)
    before = (world.media / "tv" / "Severance (2022)" / "Season 01" / "Season01.jpg").read_bytes()
    world.mediux.change_image(raw["season_posters"][0], "willtong93-95396-s1-neu")
    result = await binding.check_binding(show)
    assert result["changed"] == 1 and result["applied"] == 0
    open_reviews = reviews.open_items("set_changed")
    assert len(open_reviews) == 1
    # Not reported twice
    result = await binding.check_binding(show)
    assert result["changed"] == 0
    await binding.accept_changes(open_reviews[0]["id"], take=True)
    after = (world.media / "tv" / "Severance (2022)" / "Season 01" / "Season01.jpg").read_bytes()
    assert after != before
    assert not reviews.open_items("set_changed")


async def test_manual_choice_is_not_overwritten_by_set(world):
    show, raw = await setup_show(world)
    await bind_first_set(show)
    from app.posters import sources
    from app.posters.sources.base import ImageRef

    upload = sources.store_upload(poster_for("eigenes", "EIGEN"))
    await tasks.apply_image(show, f"S:{show}:1", ImageRef("upload", upload, "season", 1).to_dict())
    world.mediux.change_image(raw["season_posters"][0], "willtong93-95396-s1-neu")
    result = await binding.check_binding(show)
    assert result["changed"] == 0 and result["applied"] == 0


async def test_new_show_with_preferred_designer_is_applied(world):
    await world.enable_libraries()
    await library.sync()  # baseline (empty library)
    world.plex.add_show("Andor", 2022, "83867", seasons=[1, 2])
    world.mediux.add_show_set("83867", "Andor", 2022, "someone", [1, 2], popularity=99)
    world.mediux.add_show_set("83867", "Andor", 2022, "willtong93", [1, 2])
    summary = await tasks.sync_and_process()
    assert summary["auto"] == {"applied": 1}
    show = db.get().scalar("SELECT key FROM items WHERE title='Andor'")
    assert binding.get(show)["designer"] == "willtong93"
    assert any(title.startswith("New: Andor") for title in world.discord.titles())


async def test_new_show_incomplete_or_unknown_designer_goes_to_review(world):
    await world.enable_libraries()
    await library.sync()
    world.plex.add_show("Andor", 2022, "83867", seasons=[1, 2])
    world.mediux.add_show_set("83867", "Andor", 2022, "willtong93", [1])
    world.plex.add_show("Shogun", 2024, "126308", seasons=[1])
    world.mediux.add_show_set("126308", "Shogun", 2024, "someone", [1])
    summary = await tasks.sync_and_process()
    assert summary["auto"] == {"review": 2}
    reasons = {row["title"]: row["reason"] for row in reviews.open_items("new_item")}
    assert "incomplete" in reasons["Andor"] and "Season 2" in reasons["Andor"]
    assert reasons["Shogun"] == "No set from a preferred designer"
    candidates = reviews.open_items("new_item")[0]["data"]["candidates"]
    assert candidates and candidates[0]["set"]["source"] == "mediux"


async def test_tpdb_set_of_preferred_designer_is_used_when_unambiguous(world):
    settings.set("mediux_token", "")
    await world.enable_libraries()
    await library.sync()
    world.plex.add_show("Andor", 2022, "83867", seasons=[1])
    world.tmdb.add("tv", "83867", "Andor", "2022-09-21")
    item_id = world.tpdb.add_item("Andor", 2022, "show")
    world.tpdb.add_show_set(item_id, "DIIIVOY", [1])
    summary = await tasks.sync_and_process()
    assert summary["auto"] == {"applied": 1}
    show = db.get().scalar("SELECT key FROM items WHERE title='Andor'")
    assert binding.get(show)["source"] == "tpdb"
    assert (world.media / "tv" / "Andor (2022)" / "Season 01" / "Season01.jpg").exists()


async def test_movie_follows_the_set_of_its_collection(world):
    set_id = world.mediux.add_collection_set("119", "Der Herr der Ringe", "bobaflett",
                                             [("120", "Die Gefährten", 2001), ("121", "Die zwei Türme", 2002)])
    first = world.plex.add_movie("Die Gefährten", 2001, "120")
    await world.enable_libraries()
    await library.sync()
    item = db.get().one("SELECT * FROM items WHERE key=?", (first,))
    ranked, _ = await catalog.gather_ranked(item)
    collection = next(r.set for r in ranked if r.set.set_key == f"collection:{set_id}")
    await binding.bind(first, collection)
    # bobaflett is not a preferred designer, but the second film follows its collection
    world.plex.add_movie("Die zwei Türme", 2002, "121")
    summary = await tasks.sync_and_process()
    assert summary["auto"] == {"applied": 1}
    second = db.get().scalar("SELECT key FROM items WHERE title='Die zwei Türme'")
    assert binding.get(second)["set_key"] == f"collection:{set_id}"


async def test_new_season_of_unbound_show_goes_to_review(world):
    show, raw = await setup_show(world)
    world.mediux.add_season_to_set(raw, 3)
    world.plex.add_season(show, 3)
    summary = await tasks.sync_and_process()
    assert summary["seasons"] == {"review": 1}
    review = reviews.open_items("new_season")[0]
    assert review["season"] == 3 and review["data"]["candidates"]


def _degrade(data: bytes, scale: float = 0.5, quality: int = 70) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = img.resize((int(img.width * scale), int(img.height * scale)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


async def test_detection_binds_existing_files_to_their_set(world):
    show = world.plex.add_show("Severance", 2022, "95396", seasons=[1, 2, 3])
    world.mediux.add_show_set("95396", "Severance", 2022, "other", [1, 2])
    raw = world.mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1, 2])
    settings.update({"naming_show": "auto", "naming_season": "auto"})
    root = world.media / "tv" / "Severance (2022)"
    images = world.mediux.images
    show_label = raw["show_poster"][0]["id"]
    (root / "poster.jpg").write_bytes(_degrade(poster_for(images[show_label], "SHOW")))
    for entry in raw["season_posters"]:
        n = entry["season"]["season_number"]
        (root / f"Season{n:02d}.jpg").write_bytes(_degrade(poster_for(images[entry["id"]], f"S{n}")))
    await world.enable_libraries()
    await library.sync()
    proposal = await detect.detect_item(show)
    assert proposal["confidence"] == "high"
    assert proposal["set"]["designer"] == "willtong93" and len(proposal["matched"]) == 3
    review = reviews.open_items("detect")[0]
    assert detect.confirm(review["id"])
    bound = binding.get(show)
    assert bound["bound_by"] == "detect"
    # Files stay as they are; season 3 now waits for the set
    assert (root / "Season01.jpg").exists() and not (root / "season01-poster.jpg").exists()
    assert db.get().scalar("SELECT waiting FROM slots WHERE id=?", (f"S:{show}:3",)) == 1
    world.mediux.add_season_to_set(raw, 3)
    result = await binding.check_binding(show)
    assert result["applied"] == 1
    # Naming "as existing": new files follow the existing names (Season03.jpg in the show folder)
    assert (root / "Season03.jpg").exists()


async def test_detection_ignores_unrelated_files(world):
    show = world.plex.add_show("Severance", 2022, "95396", seasons=[1])
    world.mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1])
    root = world.media / "tv" / "Severance (2022)"
    (root / "poster.jpg").write_bytes(poster_for("something-else", "X"))
    await world.enable_libraries()
    await library.sync()
    assert await detect.detect_item(show) is None


async def test_detection_job_over_library_and_confirm_all(world):
    shows = []
    for index, (title, tmdb) in enumerate((("Andor", "83867"), ("Shogun", "126308"))):
        show = world.plex.add_show(title, 2022 + index, tmdb, seasons=[1])
        raw = world.mediux.add_show_set(tmdb, title, 2022 + index, "willtong93", [1])
        root = world.media / "tv" / f"{title} ({2022 + index})"
        images = world.mediux.images
        (root / "poster.jpg").write_bytes(_degrade(poster_for(images[raw["show_poster"][0]["id"]], "SHOW")))
        (root / "season01-poster.jpg").write_bytes(
            _degrade(poster_for(images[raw["season_posters"][0]["id"]], "S1")))
        shows.append(show)
    await world.enable_libraries()
    await library.sync()
    summary = await detect.run("shows")
    assert summary == {"checked": 2, "found": 2, "high": 2, "errors": 0}
    assert detect.confirm_all() == 2
    assert all(binding.get(show) for show in shows)
    assert any(title == "Match existing finished" for title in world.discord.titles())


async def test_stamped_files_are_recognized_without_comparison(world):
    show, raw = await setup_show(world)
    await bind_first_set(show)
    # Lose the database knowledge, keep the files
    db.get().execute("DELETE FROM bindings")
    db.get().execute("UPDATE slots SET source=NULL, set_key=NULL, written_sha1=NULL, file=NULL, file_size=NULL")
    library.scan_files(None)
    slot = db.get().one("SELECT * FROM slots WHERE id=?", (f"S:{show}:1",))
    assert slot["source"] == "mediux" and slot["set_key"] == f"show:{raw['id']}"
    proposal = await detect.detect_item(show, store=False)
    assert proposal["via"] == "origin stamp in the file" and proposal["confidence"] == "high"


async def test_single_tpdb_poster_is_written_but_not_bound(world):
    settings.set("mediux_token", "")
    movie = world.plex.add_movie("Arrival", 2016, "329865")
    item_id = world.tpdb.add_item("Arrival", 2016, "movie")
    world.tpdb.add_poster(item_id, "Arrival (2016)", "DIIIVOY", None, kind="movie")
    await world.enable_libraries()
    await library.sync()
    item = db.get().one("SELECT * FROM items WHERE key=?", (movie,))
    ranked, _ = await catalog.gather_ranked(item)
    single = ranked[0].set
    assert single.set_key.startswith("poster:")
    result = await binding.bind(movie, single)
    assert result["written"] == 1
    assert binding.get(movie) is None
    slot = db.get().one("SELECT * FROM slots WHERE id=?", (f"m:{movie}",))
    assert slot["mode"] == "manual" and slot["source"] == "tpdb"


async def test_folder_layout_with_tvdb_id_and_season_folders(world):
    """Show name (Year) {tvdb-…}/show.jpg and Season 03/Season03.jpg; older names get archived."""
    folder = "Severance (2022) {tvdb-371980}"
    show = world.plex.add_show("Severance", 2022, "95396", "371980", seasons=[1, 2], folder=folder)
    raw = world.mediux.add_show_set("95396", "Severance", 2022, "willtong93", [1, 2])
    root = world.media / "tv" / folder
    (root / "poster.jpg").write_bytes(poster_for("old-show", "OLD"))
    (root / "season01-poster.jpg").write_bytes(poster_for("old-s1", "OLD1"))
    await world.enable_libraries()
    await library.sync()
    result = await bind_first_set(show)
    assert result["written"] == 3
    assert (root / "show.jpg").exists() and not (root / "poster.jpg").exists()
    assert (root / "Season 01" / "Season01.jpg").exists() and not (root / "season01-poster.jpg").exists()
    assert (root / "Season 02" / "Season02.jpg").exists()
    for key in (show, world.plex.season_key(show, 1), world.plex.season_key(show, 2)):
        assert world.plex.selected_is_local(key)
    archived = {p.name.split("__", 1)[-1] for p in (config.DATA_DIR / "archive").rglob("*") if p.is_file()}
    assert {"poster.jpg", "season01-poster.jpg"} <= archived
    # A new season arrives in Plex and in the set: its poster lands in the new season folder
    world.mediux.add_season_to_set(raw, 3)
    world.plex.add_season(show, 3)
    summary = await tasks.sync_and_process()
    assert summary["seasons"] == {"applied": 1}
    assert (root / "Season 03" / "Season03.jpg").exists()
    assert world.plex.selected_is_local(world.plex.season_key(show, 3))


async def test_ended_show_with_all_posters_is_done(world):
    show, raw = await setup_show(world)
    world.tvmaze.shows["371980"] = "4242"
    world.tvmaze.status["371980"] = "Ended"
    await bind_first_set(show)
    summary = await tasks.sync_and_process()
    assert summary["status"] >= 1
    assert db.get().scalar("SELECT show_status FROM items WHERE key=?", (show,)) == "ended"
    assert status.is_done(show)
    # Finished shows are left alone by the set check and by Match existing
    assert (await tasks.check_sets())["checked"] == 0
    binding.unbind(show)
    assert (await detect.run("shows"))["checked"] == 0
    # Statuses are not looked up again on every sync
    before = world.tvmaze.lookups
    await tasks.sync_and_process()
    assert world.tvmaze.lookups == before
    # A new season without poster: no longer done, status is looked up again
    world.plex.add_season(show, 3)
    await tasks.sync_and_process()
    assert not status.is_done(show)
    assert world.tvmaze.lookups == before + 1


async def test_running_or_incomplete_show_is_not_done(world):
    show, raw = await setup_show(world, seasons=(1, 2, 3), set_seasons=(1, 2))
    world.tvmaze.shows["371980"] = "4242"
    world.tvmaze.status["371980"] = "Running"
    await bind_first_set(show)
    await tasks.sync_and_process()
    assert db.get().scalar("SELECT show_status FROM items WHERE key=?", (show,)) == "running"
    assert not status.is_done(show)
    world.tvmaze.status["371980"] = "Ended"
    await status.refresh(show)
    assert not status.is_done(show)  # season 3 still has no file
    # Specials without a poster do not count
    world.mediux.add_season_to_set(raw, 3)
    await binding.check_binding(show)
    world.plex.add_season(show, 0)
    await library.sync()
    assert status.is_done(show)


async def test_automation_off_leaves_new_titles_and_seasons_alone(world):
    """While Litfass still runs, Programarr must not write posters on its own."""
    import time

    from app import jobs

    show, raw = await setup_show(world)
    await bind_first_set(show)
    settings.set("automation", False)
    world.mediux.add_season_to_set(raw, 3)
    world.plex.add_season(show, 3)
    world.plex.add_show("Andor", 2022, "83867", seasons=[1])
    world.mediux.add_show_set("83867", "Andor", 2022, "willtong93", [1])
    summary = await tasks.sync_and_process()
    assert summary["new_items"] == 1 and summary["new_seasons"] == 1 and summary["skipped"] == 2
    assert summary["auto"] == {} and summary["seasons"] == {}
    assert not (world.media / "tv" / "Severance (2022)" / "Season 03" / "Season03.jpg").exists()
    andor = db.get().scalar("SELECT key FROM items WHERE title='Andor'")
    assert andor and binding.get(andor) is None and not reviews.open_items()
    assert not any(title.startswith(("New", "Review")) for title in world.discord.titles())
    # The scheduler leaves set checks out; a click still runs them.
    now = time.time()
    settings.update({"last_sync": now, "last_set_check": 0, "last_folder_check": now})
    assert jobs.due(now) == []
    settings.set("automation", True)
    assert jobs.due(now) == ["setcheck"]
