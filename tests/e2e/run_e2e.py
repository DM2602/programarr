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
MAIN_PAGES = ("/", "/library", "/review", "/waiting", "/match", "/history", "/lists", "/lists/matches",
              "/lists/exclusions", "/logs", "/settings/plex", "/settings/lists", "/settings/services")


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
        goto(page, "/waiting")
        page.wait_for_selector(".waiting-list")
        listing = page.inner_text(".waiting-list")
        assert "Slow Horses" in listing and "Season 4" in listing, listing
        steps.ok("Set applied; season 4 waits for the set", page)

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
        page.wait_for_selector("text=Automation is off. Programarr writes posters only when you click.")
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
        assert page.locator(".entry[data-status=excluded]").count() == 1  # Power, excluded in the demo
        grinch = page.locator(".entry", has_text="The Grinch")
        assert grinch.locator("a:has-text('In Plex')").count() == 1 and "IMDb 6.3" in grinch.inner_text()
        steps.ok("FlixPatrol preview from the form: 20 entries, 3 to check", page)

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
        page.locator(".problem", has_text="The Altruists").locator("[data-action=ignore]").click()
        page.wait_for_selector("text=Nothing to check")
        page.click("[data-tab=corrections]")
        page.wait_for_selector(".correction")
        assert page.locator(".correction").count() == 3
        steps.ok("Match log: candidate used, unknown title ignored", page)

        # 18
        goto(page, f"/lists/runs/{run_id}")
        page.locator(".entry", has_text="Lupin").locator("[data-action=exclude]").click()
        page.fill("[role=dialog] input", "Seen it")
        page.click("[role=dialog] button[type=submit]")
        wait_for(lambda: page.locator(".entry", has_text="Lupin").get_attribute("data-status") == "excluded", what="excluded")
        goto(page, "/lists/exclusions")
        page.wait_for_selector(".exclusion")
        assert page.locator(".exclusion").count() == 2  # Power and Lupin
        page.locator(".exclusion", has_text="Lupin").locator("button:has-text('Remove')").click()
        wait_for(lambda: page.locator(".exclusion").count() == 1, what="exclusion removed")
        steps.ok("Title excluded from a preview and included again", page)

        # 19
        dune = db.get().one("SELECT key FROM items WHERE title='Dune'")["key"]
        sequel = db.get().one("SELECT key, tmdb_id FROM items WHERE title='Dune: Part Two'")
        goto(page, f"/item/{dune}")
        page.wait_for_selector("[data-arr=radarr]")
        assert page.get_attribute("[data-arr=radarr]", "href") == "https://radarr.example.ch/movie/dune-438631"
        goto(page, f"/item/{sequel['key']}")
        page.wait_for_selector("[data-arr=radarr][data-present=false]")
        assert page.get_attribute("[data-arr=radarr]", "href").endswith(f"/add/new?term=tmdb%3A{sequel['tmdb_id']}")
        assert page.inner_text("[data-arr=radarr]").startswith("Add to Radarr")
        steps.ok("Title pages link to Radarr: open there or add", page)

        # 20
        goto(page, "/settings/lists")
        page.click("[data-test=mdblist]")
        page.wait_for_selector("[data-result=mdblist][data-ok=true]")
        page.click("[data-test=trakt]")
        page.wait_for_selector("[data-result=trakt][data-ok=true]")
        steps.ok("MDBList key and Trakt client ID checked", page)

        # 21
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
