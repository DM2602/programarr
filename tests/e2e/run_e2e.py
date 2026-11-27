"""Browser run against simulated services: python tests/e2e/run_e2e.py [--keep] [--shots DIR]

Starts Programarr with the demo library (tests/demo.py) and clicks through the main
flows with Playwright (Chromium or Chrome; path via PROGRAMARR_CHROME). The web app
must be built first (cd frontend && npm run build). With --keep the instance keeps
running afterwards (http://127.0.0.1:8795).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
MAIN_PAGES = ("/", "/library", "/review", "/pending", "/match", "/history", "/lists", "/lists/matches",
              "/lists/exclusions", "/flows", "/flows/history", "/logs", "/settings/plex", "/settings/lists",
              "/settings/services")


def png(color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (600, 900), color).save(out, "PNG")
    return out.getvalue()


def wait_for(condition, timeout: float = 20.0, what: str = "condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {what}")


def start_server(data_root: Path):
    os.environ["PROGRAMARR_DATA_DIR"] = str(data_root / "data")
    os.environ["PROGRAMARR_SCHEDULER"] = "0"
    (data_root / "data").mkdir(parents=True, exist_ok=True)
    from app import config, db, http, settings
    from tests.demo import build
    from tests.fakes.world import World

    if not (ROOT / "app" / "web" / "dist" / "index.html").exists():
        raise SystemExit("The web app is not built: cd frontend && npm run build")
    config.set_data_dir(data_root / "data")
    config.SCHEDULER = False
    config.ensure_dirs()
    db.init(data_root / "data" / "programarr.db")
    settings.reset_cache()
    world = World(data_root)
    http.set_transport(world.router)
    world.configure()
    asyncio.run(build(world))

    import uvicorn

    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    return world, server


class Steps:
    def __init__(self, shots: Path | None):
        self.done = 0
        self.shots = shots

    def ok(self, text: str, page=None) -> None:
        self.done += 1
        print(f"  ✓ {self.done:2d} {text}")
        if self.shots and page is not None:
            self.shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(self.shots / f"{self.done:02d}.png"), full_page=True)


def goto(page, path: str) -> None:
    page.goto(BASE + path)
    page.wait_for_load_state("networkidle")


def settled(locator) -> dict:
    """The element's box once it stops moving (the canvas may still pan)."""
    box = locator.bounding_box()
    for _ in range(30):
        time.sleep(0.1)
        again = locator.bounding_box()
        if again == box:
            return box
        box = again
    return box


def drag(page, source, target) -> None:
    """Drag with the mouse from one element to another (React Flow connections)."""
    start, end = settled(source), settled(target)
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=15)
    page.mouse.up()


def run_browser(world, keep: bool, shots: Path | None) -> int:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from app import db

    def key_of(title: str) -> str:
        return db.get().scalar("SELECT key FROM items WHERE title=?", (title,))

    def binding_of(key: str):
        return db.get().one("SELECT * FROM bindings WHERE item_key=?", (key,))

    steps = Steps(shots)
    chrome = os.environ.get("PROGRAMARR_CHROME") or ("/usr/bin/google-chrome" if Path("/usr/bin/google-chrome").exists() else None)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]) if chrome else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        # 1
        goto(page, "/")
        page.wait_for_selector("[data-testid=todo] a")
        assert page.locator("[data-testid=todo] a").count() >= 2
        assert page.locator(".recent-card").count() >= 3
        steps.ok("Dashboard with open items and recently set posters", page)

        # 2
        goto(page, "/library?status=unlinked")
        page.wait_for_selector(".title-card")
        titles = page.locator(".title-card").evaluate_all("els => els.map(e => e.dataset.title)")
        assert "Slow Horses" in titles and "Severance" not in titles, titles
        page.fill("#global-search", "slow")
        page.press("#global-search", "Enter")
        wait_for(lambda: page.locator(".title-card").count() == 1, what="search result")
        assert page.locator(".title-card").first.get_attribute("data-title") == "Slow Horses"
        steps.ok("Library: filter Not linked and search", page)

        # 3
        bear = key_of("The Bear")
        goto(page, f"/item/{bear}")
        page.click(f"button.pick[data-slot='S:{bear}:2']")
        page.wait_for_selector(".picker[data-open] .set-block")
        first_set = page.locator(".picker .set-block").first
        assert first_set.locator(".image-choice[data-match]").count() == 1
        assert first_set.locator(".image-choice[data-match]").inner_text().strip() == "S2"
        assert page.locator(".picker .image-choice[disabled]").count() == 0
        page.keyboard.press("Escape")
        page.wait_for_selector(".picker", state="detached")
        page.click(f"button.pick[data-slot='S:{bear}:3']")
        page.wait_for_selector(".picker[data-open] .set-block")
        coverage = page.locator(".picker .set-block .coverage").first
        assert "Season 3" in coverage.get_attribute("aria-label")
        assert coverage.locator("[data-season='3']").get_attribute("data-has") == "false"
        steps.ok("Picker highlights the matching image, the set shows the missing season", page)

        # 4
        page.click(".picker [data-tab=file]")
        page.set_input_files("#upload-input", files=[{"name": "s3.png", "mimeType": "image/png", "buffer": png((200, 40, 40))}])
        page.wait_for_selector(".picker .chosen img")
        page.click(".picker [data-action=apply-image]")
        season_root = world.media / "tv" / "The Bear (2022)" / "Season 03"

        def season3_file():
            return next((p for p in season_root.iterdir() if p.name.lower().startswith("season03")), None)

        wait_for(season3_file, what="season 3 poster file")
        wait_for(lambda: world.plex.selected_is_local(world.plex.season_key(bear, 3)), what="Plex showing the file")
        page.wait_for_selector(f".slot-card[data-slot='S:{bear}:3'] >> text=Season03")
        steps.ok("Uploaded file: season 3 is in the folder and Plex shows it", page)

        # 5
        page.click(f"button.pick[data-slot='S:{bear}:3']")
        page.wait_for_selector(".picker[data-open]")
        page.click(".picker [data-tab=file]")
        page.set_input_files("#upload-input", files=[{"name": "s3b.png", "mimeType": "image/png", "buffer": png((40, 40, 200))}])
        page.wait_for_selector(".picker .chosen img")
        page.click(".picker [data-action=apply-image]")
        wait_for(lambda: Image.open(season3_file()).convert("RGB").getpixel((10, 10)) == (40, 40, 200), what="second upload")
        page.wait_for_selector("[data-action=restore]")
        page.locator("[data-action=restore]").first.click()
        page.click("[role=dialog] button[type=submit]")
        wait_for(lambda: Image.open(season3_file()).convert("RGB").getpixel((10, 10)) == (200, 40, 40), what="restored poster")
        steps.ok("Replaced poster restored from the history", page)

        # 6
        slow = key_of("Slow Horses")
        goto(page, f"/item/{slow}")
        page.click("[data-action=pick-set]")
        page.wait_for_selector(".picker[data-open] .set-block")
        page.locator(".picker [data-action=apply-set]").first.click()
        wait_for(lambda: binding_of(slow), what="Slow Horses linked")
        page.wait_for_selector("[data-testid=linked-set] >> text=Slow Horses")
        goto(page, "/pending")
        page.wait_for_selector(".pending-list")
        listing = page.inner_text(".pending-list")
        assert "Slow Horses" in listing and "Season 4" in listing, listing
        steps.ok("Set applied; season 4 pending", page)

        # 7
        andor = key_of("Andor")
        goto(page, "/review?kind=detect")
        page.click("[data-action=review-confirm]")
        wait_for(lambda: binding_of(andor), what="Andor linked")
        page.wait_for_selector("text=Nothing to review")
        steps.ok("Matched set for Andor linked", page)

        # 8
        shogun = key_of("Shōgun")
        goto(page, "/review?kind=new_item")
        page.locator("[data-action=review-bind]").first.click()
        wait_for(lambda: (world.media / "tv" / "Shōgun (2024)" / "show.jpg").exists(), what="Shōgun poster")
        wait_for(lambda: binding_of(shogun), what="Shōgun linked")
        steps.ok("New title applied from Review", page)

        # 11b
        oppenheimer = key_of("Oppenheimer")
        goto(page, "/review?kind=new_item")
        tiles = page.locator(".review-card .candidate")
        tiles.first.wait_for()
        assert tiles.count() >= 4, "movie posters side by side"
        assert tiles.first.locator("[data-action=review-keep-plex]").count() == 1, "Plex poster first"
        page.click(".review-card [data-action=review-pick]")
        page.wait_for_selector(".picker[data-open] .set-tile")
        page.keyboard.press("Escape")
        page.wait_for_selector(".picker", state="detached")
        assert page.url.endswith("/review?kind=new_item"), "the picker opens over Review"
        tiles.nth(1).locator("img").hover()
        page.wait_for_selector(".zoom-preview")
        page.mouse.move(0, 0)
        page.wait_for_selector(".zoom-preview", state="detached")
        designer = tiles.nth(1).locator(".font-semibold").inner_text()
        tiles.nth(1).locator("[data-action=review-bind]").click()
        wait_for(lambda: (binding_of(oppenheimer) or {}).get("designer") == designer, what="Oppenheimer linked")
        steps.ok("Movie in Review: Plex poster, sets side by side, more in the picker, enlarged on hover, one applied", page)

        # 9
        goto(page, "/settings/automation")
        page.click("[data-action=add-designer]")
        page.locator(".designer input[data-k=name]").last.fill("olivier_pons")
        page.click("[data-action=save]")
        page.wait_for_selector("[data-result=save][data-ok=true]")
        page.reload()
        page.wait_for_selector(".designer input[data-k=name]")
        names = page.locator(".designer input[data-k=name]").evaluate_all("els => els.map(e => e.value)")
        assert names[-1] == "olivier_pons", names
        steps.ok("Designer list saved", page)

        # 10
        goto(page, "/settings/plex")
        page.click("[data-test=plex]")
        page.wait_for_selector("[data-result=plex][data-ok=true]")
        page.click("[data-action=paths-check]")
        page.wait_for_selector("[data-result=paths][data-ok=true]")
        steps.ok("Plex connection and paths checked", page)

        # 11
        from app import settings

        goto(page, "/settings/automation")
        page.uncheck("#f-automation")
        page.click("[data-action=save]")
        page.wait_for_selector("[data-result=save][data-ok=true]")
        assert settings.get("automation") is False
        goto(page, "/")
        page.wait_for_selector("text=Automation is off. Turn on")
        page.click("a:has-text('Automation is off')")
        page.wait_for_selector("#f-automation")
        page.check("#f-automation")
        page.click("[data-action=save]")
        page.wait_for_selector("[data-result=save][data-ok=true]")
        assert settings.get("automation") is True
        steps.ok("Automation switched off and on again", page)

        # 12
        mobile = browser.new_page(viewport={"width": 390, "height": 820}, is_mobile=True)
        mobile.on("pageerror", lambda exc: errors.append(str(exc)))
        mobile.goto(f"{BASE}/item/{bear}")
        mobile.click(f"button.pick[data-slot='s:{bear}']")
        mobile.wait_for_selector(".picker[data-open]")
        mobile.wait_for_timeout(400)  # slide-in transition
        box = mobile.locator(".picker").bounding_box()
        assert box and box["width"] <= 391 and abs(box["x"]) < 1, box
        steps.ok("Picker on a narrow screen", mobile)
        mobile.close()

        # 13
        goto(page, "/match")
        page.click("[data-action=adopt]")
        lotr_folder = world.media / "movies" / "Der Herr der Ringe: Die Gefährten (2001)"
        wait_for(lambda: (lotr_folder / "poster.jpg").exists(), what="adopted poster")
        lotr = db.get().scalar("SELECT key FROM items WHERE title LIKE 'Der Herr der Ringe%'")
        wait_for(lambda: world.plex.selected_is_local(lotr), what="Plex showing the adopted file")
        steps.ok("Poster uploaded to Plex saved as a file", page)

        # 13b
        from app.posters import backup

        goto(page, "/settings/files")
        page.wait_for_selector("text=Back up all posters")
        page.click("[data-action=backup]")
        wait_for(lambda: backup.stats()["files"] > 0, what="poster copies in the backup")
        lotr_item = db.get().one("SELECT * FROM items WHERE key=?", (lotr,))
        wait_for(lambda: backup.find(lotr_item, "movie", None) is not None, what="backup copy of the adopted poster")
        (lotr_folder / "poster.jpg").unlink()
        page.click("[data-action=restore-backup]")
        wait_for(lambda: (lotr_folder / "poster.jpg").exists(), what="poster restored from the backup")
        steps.ok("Posters backed up, a deleted one restored", page)

        # 14
        goto(page, "/lists")
        page.click("[data-preview-form] button:has-text('FlixPatrol')")
        page.fill("[data-preview-form] input[name=platform]", "netflix")
        page.fill("[data-preview-form] input[name=region]", "switzerland")
        page.click("[data-action=preview]")
        page.wait_for_url(re.compile(r"/lists/runs/\d+$"), timeout=30000)
        page.wait_for_selector(".entry")
        run_id = int(page.url.rsplit("/", 1)[1])
        assert page.locator(".entry").count() == 20
        assert page.locator(".entry[data-status=uncertain]").count() == 2
        assert page.locator(".entry[data-status=failed]").count() == 1
        assert page.locator(".entry[data-status=excluded]").count() == 2  # Power here, Hill 12 in Radarr
        grinch = page.locator(".entry", has_text="The Grinch")
        assert grinch.locator("a:has-text('In Plex')").count() == 1 and "IMDb 6.3" in grinch.inner_text()
        steps.ok("FlixPatrol preview from the form: 20 entries, 3 unresolved", page)

        # 15
        assert [page.locator("[data-section]").nth(i).get_attribute("data-section") for i in range(2)] == ["movies", "shows"]
        switch = page.locator(".entry", has_text="The Christmas Switch")
        assert switch.locator("button.score").inner_text() == "90%" and "11 days in TOP 10" in switch.inner_text()
        switch.locator("button.score").click()
        page.wait_for_selector(".match-details")
        assert "Same title, year off by one" in page.inner_text(".match-details")
        steps.ok("Match details open from the score", page)
        page.keyboard.press("Escape")
        page.wait_for_selector(".match-details", state="detached")

        # 16
        page.locator(".entry", has_text="A Dog's Perfect Christmas").locator("[data-action=fix]").click()
        page.wait_for_selector("[role=dialog] .candidate")
        page.click("[role=dialog] .candidate button[data-tmdb='1011']")
        page.wait_for_selector("[role=dialog]", state="detached")
        wait_for(lambda: page.locator(".entry", has_text="A Dog's Perfect Christmas").get_attribute("data-status") == "matched",
                 what="fixed entry")
        assert db.get().scalar("SELECT COUNT(*) FROM parser_overrides WHERE media_id IS NOT NULL") == 1
        steps.ok("Match fixed in the search dialog", page)

        # 17
        goto(page, "/lists/matches")
        page.wait_for_selector(".problem")
        assert page.locator(".problem").count() == 2
        page.locator(".problem", has_text="How to Make a Killing").locator(".candidate[data-tmdb='1010'] button").click()
        wait_for(lambda: page.locator(".problem").count() == 1, what="problem solved")
        page.locator(".problem", has_text="The Altruists").locator("[data-action=skip]").click()
        page.wait_for_selector("text=Nothing unresolved")
        page.click("[data-tab=corrections]")
        page.wait_for_selector(".correction")
        assert page.locator(".correction").count() == 3
        steps.ok("Match log: candidate used, unknown title skipped", page)

        # 18
        goto(page, f"/lists/runs/{run_id}")
        page.locator(".entry", has_text="Lupin").locator("[data-action=exclude]").click()
        page.fill("[role=dialog] input", "Seen it")
        page.click("[role=dialog] button[type=submit]")
        wait_for(lambda: page.locator(".entry", has_text="Lupin").get_attribute("data-status") == "excluded", what="excluded")
        assert 383365 in world.sonarr.excluded  # on Sonarr's list too
        goto(page, "/lists/exclusions")
        page.wait_for_selector(".exclusion")
        # Lupin and Power from here, Cats, Hill 12 and The Idol from Radarr and Sonarr
        assert page.locator(".exclusion").count() == 5
        lupin = page.locator(".exclusion", has_text="Lupin")
        assert lupin.get_attribute("data-service") == "sonarr" and "Seen it" in lupin.inner_text()
        lupin.locator("button:has-text('Remove')").click()
        wait_for(lambda: page.locator(".exclusion").count() == 4, what="exclusion removed")
        assert 383365 not in world.sonarr.excluded
        steps.ok("Title excluded from a preview and exclusion removed, in Sonarr too", page)

        # 19
        world.radarr.exclude(1004, "The Christmas Switch", 2025)  # excluded in Radarr meanwhile
        cats = page.locator(".exclusion", has_text="Cats")
        assert cats.get_attribute("data-service") == "radarr" and "From Radarr" in cats.inner_text()
        page.click("[data-action=sync-exclusions]")
        page.wait_for_selector(".exclusion[data-service=radarr]:has-text('The Christmas Switch')")
        assert "Synced with Radarr and Sonarr" in page.inner_text("h1 + p")
        cats.locator("button:has-text('Remove')").click()
        wait_for(lambda: cats.count() == 0, what="Cats included")
        assert 536869 not in world.radarr.excluded and 1004 in world.radarr.excluded
        page.click("button[aria-pressed]:has-text('Shows')")
        assert page.locator(".exclusion").count() == 2  # Power and The Idol
        page.fill("input[aria-label='Filter by title']", "idol")
        assert page.locator(".exclusion").count() == 1
        assert page.inner_text("button[aria-pressed=true]") == "Shows"
        steps.ok("Exclusions synced with Radarr both ways, filtered", page)

        # 20
        dune = db.get().one("SELECT key FROM items WHERE title='Dune'")["key"]
        sequel = db.get().one("SELECT key, tmdb_id FROM items WHERE title='Dune: Part Two'")
        goto(page, f"/item/{dune}")
        page.wait_for_selector("[data-arr=radarr]")
        assert page.get_attribute("[data-arr=radarr]", "href") == "https://radarr.example.ch/movie/dune-438631"
        goto(page, f"/item/{sequel['key']}")
        page.wait_for_selector("[data-arr=radarr][data-present=false]")
        assert page.get_attribute("[data-arr=radarr]", "href").endswith(f"/add/new?term=tmdb%3A{sequel['tmdb_id']}")
        assert page.inner_text("[data-arr=radarr]").startswith("Add to Radarr")
        bear = db.get().one("SELECT key FROM items WHERE title='The Bear'")["key"]
        goto(page, f"/item/{bear}")
        page.wait_for_selector("[data-missing='4']")
        assert "Monitored in Sonarr" in page.inner_text("[data-missing='4']")
        assert page.locator("[data-missing]").count() == 2
        steps.ok("Title pages link to Radarr: open there or add", page)

        # 21
        goto(page, "/settings/lists")
        page.click("[data-test=mdblist]")
        page.wait_for_selector("[data-result=mdblist][data-ok=true]")
        page.click("[data-test=trakt]")
        page.wait_for_selector("[data-result=trakt][data-ok=true]")
        steps.ok("MDBList key and Trakt client ID checked", page)

        # 22
        top10 = db.get().one("SELECT id, version FROM flows WHERE name='Netflix Switzerland'")
        goto(page, "/flows")
        page.wait_for_selector(".flow-row")
        assert page.locator(".flow-row").count() == 2
        row = page.locator(f".flow-row[data-flow='{top10['id']}']")
        assert " ".join(row.locator("[data-total=added]").inner_text().split()) == "10 to add"
        row.locator(f"a[href='/flows/{top10['id']}']").click()
        page.wait_for_selector("[data-node=radarr1] [data-outcome=added]")
        assert page.get_attribute("[data-node=flixpatrol1] [data-count-in]", "data-count-in") == "15"
        assert page.inner_text("[data-node=radarr1] [data-outcome=added]").split() == ["Would", "add", "5"]
        page.wait_for_selector("[data-checks=ok]")
        steps.ok("Flow editor shows the last dry run on its nodes", page)

        # 23
        page.click("[data-node=radarr1]")
        page.wait_for_selector("[data-panel=radarr1]")
        assert page.input_value("[data-panel=radarr1] select[data-field=quality_profile]") == "1"
        page.fill("[data-panel=radarr1] input[data-field=limit]", "3")
        page.wait_for_selector("text=Unsaved")
        page.click("[data-action=save]")
        wait_for(lambda: db.get().scalar("SELECT version FROM flows WHERE id=?", (top10["id"],)) == top10["version"] + 1,
                 what="flow saved")
        page.wait_for_selector("text=Unsaved", state="detached")
        writes = (world.radarr.writes, world.sonarr.writes)
        page.click("[data-action=dry-run]")
        page.wait_for_url(re.compile(r".*/flows/runs/\d+$"))
        page.wait_for_selector(".output-card [data-output=radarr1]")
        radarr_card = page.locator(".output-card").filter(has=page.locator("[data-output=radarr1]"))
        assert radarr_card.locator("[data-outcome=added]").inner_text().strip() == "3 to add"
        assert radarr_card.locator("[data-both]").get_attribute("data-both") == "2"  # the MDBList import list
        facts = {fact.get_attribute("data-fact"): fact.locator("dd").inner_text()
                 for fact in radarr_card.locator("[data-fact]").all()}
        assert facts == {"Profile": "HD-1080p", "Folder": "/data/movies", "Tags": "top-10"}, facts
        assert (world.radarr.writes, world.sonarr.writes) == writes  # a dry run writes nothing there
        assert radarr_card.locator("[data-outcome=tagged]").count() == 0  # no tags afterwards
        radarr_card.locator("[data-outcome=present]").click()
        grinch_row = page.locator(".flow-title[data-title='The Grinch']")
        grinch_row.wait_for()
        wait_for(lambda: page.locator(".flow-title").count() == 2, what="titles there already")
        assert "In Plex" in grinch_row.inner_text()
        assert page.locator(".flow-title[data-title='Woodwalkers 2']").count() == 1  # in Radarr, not in Plex yet
        # The type icon leads every title line, before the title.
        line = page.locator(".flow-title").first.locator("div.flex-wrap").first
        assert line.locator(":scope > *").first.get_attribute("title") == "Movie"
        steps.ok("Limit saved, the dry run shows what Radarr would get and compares with MDBList", page)

        # 24
        goto(page, "/flows")
        page.click("[data-action=new-flow]")
        page.fill("[role=dialog] input", "TMDB to Radarr")
        page.click("[role=dialog] button[type=submit]")
        page.wait_for_url(re.compile(r".*/flows/\d+$"))
        page.click("[data-add-menu=source]")
        page.click(".add-menu [data-add=tmdb]")
        page.wait_for_selector("[data-panel=tmdb1]")
        page.click("[data-action=close-panel]")
        page.click("[data-add-menu=output]")
        page.click(".add-menu [data-add=radarr]")
        page.wait_for_selector("[data-panel=radarr1] select[data-field=root_folder] option[value='/data/movies']",
                               state="attached")
        page.click("[data-action=close-panel]")
        page.wait_for_selector("[data-checks='2']")  # both nodes want a connection
        drag(page, page.locator("[data-node=tmdb1] .react-flow__handle.source"),
             page.locator("[data-node=radarr1] .react-flow__handle.target"))
        page.wait_for_selector("[data-checks=ok]")
        page.click("[data-action=save]")
        page.wait_for_selector("text=Unsaved", state="detached")
        built = db.get().one("SELECT graph, dry_run FROM flows WHERE name='TMDB to Radarr'")
        graph = db.loads(built["graph"])
        assert graph["edges"] == [{"source": "tmdb1", "port": "out", "target": "radarr1"}] and built["dry_run"] == 1
        radarr_node = next(node for node in graph["nodes"] if node["id"] == "radarr1")
        assert (radarr_node["config"]["quality_profile"], radarr_node["config"]["root_folder"]) == (1, "/data/movies")
        steps.ok("New flow built in the editor: TMDB connected to Radarr", page)

        # 25
        goto(page, f"/flows/{top10['id']}")
        page.click("[data-action=go-live]")
        page.click("[role=dialog] button[type=submit]")
        page.wait_for_selector("[data-action=run]")
        assert db.get().scalar("SELECT dry_run FROM flows WHERE id=?", (top10["id"],)) == 0
        page.click("[data-action=run]")
        page.wait_for_url(re.compile(r".*/flows/runs/\d+$"))
        page.wait_for_selector(".output-card [data-output=radarr1]")
        assert [body["tmdbId"] for body in world.radarr.added] == [1001, 1002, 1003]
        assert sorted(body["tvdbId"] for body in world.sonarr.added) == [383365, 420005, 450003, 480001, 480002]
        grinch = next(row for row in world.radarr.titles if row["tmdbId"] == 360920)
        woodwalkers = next(row for row in world.radarr.titles if row["tmdbId"] == 1006)
        assert world.radarr.tag_labels(grinch) == world.radarr.tag_labels(woodwalkers) == []
        assert world.radarr.edits == []  # tags only go on with a new title
        radarr_card = page.locator(".output-card").filter(has=page.locator("[data-output=radarr1]"))
        assert radarr_card.locator("[data-outcome=added]").inner_text().strip() == "3 added"
        steps.ok("Flow live: Radarr and Sonarr get the new titles, existing ones keep their tags", page)

        # 26
        goto(page, "/flows/history")
        page.wait_for_selector(".history-run")
        assert page.locator(".history-run").count() == 3
        page.select_option("select", label="TMDB to Radarr")
        page.wait_for_selector("text=No runs yet")
        steps.ok("Flow history lists the runs, filtered by flow", page)

        # 26b
        goto(page, "/")
        page.wait_for_selector(".flow-card")
        assert page.locator("[data-testid=todo] a[href='/flows']").count() == 0
        assert page.locator(".flow-card").count() == db.get().scalar("SELECT COUNT(*) FROM flows")
        netflix = page.locator(".flow-card", has_text="Netflix Switzerland")
        assert "Live" in netflix.inner_text() and "8 added" in " ".join(netflix.inner_text().split())
        assert "in both" in netflix.locator(f"[data-compare='{top10['id']}']").inner_text()
        assert page.locator(".flow-add").count() == 8  # 3 movies, 5 shows from the live run
        assert page.locator(".flow-add", has_text="Guarding Stars").locator("svg").count() == 1  # Radarr mark
        assert page.locator(".schedule [data-schedule]").count() == 4
        steps.ok("Dashboard: flows with their last run, titles added by flows, schedule", page)

        # 27
        trakt = db.get().one("SELECT id FROM flows WHERE name='Trakt trending'")
        goto(page, f"/flows/{trakt['id']}")
        page.wait_for_selector("[data-node=genre1]")
        handles = page.locator("[data-node=genre1] .react-flow__handle.source")
        assert handles.count() == 3  # Action, Family, No match
        # The two Radarr outputs tell apart by profile and tags on the canvas.
        assert page.inner_text("[data-node=radarr1] [data-fact=Profile] dd") == "Ultra-HD"
        assert page.inner_text("[data-node=radarr2] [data-fact=Profile] dd") == "HD-1080p"
        page.click("[data-node=genre1]")
        page.wait_for_selector("[data-panel=genre1] [data-rule]")
        assert page.locator("[data-panel=genre1] [data-rule]").count() == 2
        page.click("[data-panel=genre1] [data-action=add-rule]")
        wait_for(lambda: handles.count() == 4, what="a third output")
        page.locator("[data-panel=genre1] [data-rule]").last.locator("[data-action=remove-rule]").click()
        wait_for(lambda: handles.count() == 3, what="two outputs again")
        page.click("[data-action=close-panel]")
        page.click("[data-action=dry-run]")
        page.wait_for_url(re.compile(r".*/flows/runs/\d+$"))
        page.wait_for_selector(".run-node[data-node=genre1]")
        wait_for(lambda: "No match 1" in " ".join(page.inner_text(".run-node[data-node=genre1]").split()), what="genre counts")
        genre_step = " ".join(page.inner_text(".run-node[data-node=genre1]").split())
        assert "Action, Thriller 1" in genre_step and "Animation, Family 1" in genre_step, genre_step
        page.fill("input[aria-label='Filter by title']", "Guarding")
        page.press("input[aria-label='Filter by title']", "Enter")
        wait_for(lambda: page.locator(".flow-title").count() == 1, what="one title")
        path = " ".join(page.inner_text(".flow-title").split())
        assert "Action, Thriller" in path and "Already exists" in path, path  # added by the other flow
        steps.ok("Genre filter with two outputs: each goes to its own Radarr output", page)

        # 28
        visited: set[str] = set()
        for path in MAIN_PAGES:
            goto(page, path)
            links = page.locator("a[href^='/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
            for link in [path, *links][:60]:
                target = link.split("#")[0]
                if target in visited or target.startswith(("/api/", "/img/")):
                    continue
                visited.add(target)
                goto(page, target)
                text = page.inner_text("body")
                assert "Page not found" not in text and "Cannot load" not in text, target
                broken = page.locator("img").evaluate_all("els => els.filter(e => e.complete && e.naturalWidth === 0).map(e => e.src)")
                assert not broken, (target, broken)
        steps.ok(f"All links of the main pages work ({len(visited)} pages)", page)

        assert not errors, errors
        browser.close()
    print(f"\n{steps.done} steps passed.")
    if keep:
        print(f"Programarr keeps running: {BASE}  (Ctrl+C stops)")
        while True:
            time.sleep(3600)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--shots", type=Path, default=None, help="save a screenshot after each step")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="programarr-e2e-") as tmp:
        world, server = start_server(Path(tmp))
        try:
            return run_browser(world, args.keep, args.shots)
        finally:
            server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
