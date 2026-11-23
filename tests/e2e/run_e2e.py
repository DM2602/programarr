"""Durchlauf im Browser gegen simulierte Dienste: python tests/e2e/run_e2e.py [--keep]

Startet Litfass mit der Demo-Bibliothek (tests/demo.py) und klickt mit Playwright
(Chromium bzw. Chrome, Pfad über LITFASS_CHROME) durch die wichtigsten Abläufe.
Mit --keep läuft die Instanz danach weiter (http://127.0.0.1:8795).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"


def png(color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (600, 900), color).save(out, "PNG")
    return out.getvalue()


def start_server(data_root: Path):
    os.environ["LITFASS_DATA_DIR"] = str(data_root / "data")
    os.environ["LITFASS_SCHEDULER"] = "0"
    (data_root / "data").mkdir(parents=True, exist_ok=True)
    from app import config, db, http, settings
    from tests.demo import build
    from tests.fakes.world import World

    config.set_data_dir(data_root / "data")
    config.SCHEDULER = False
    config.ensure_dirs()
    db.init(data_root / "data" / "litfass.db")
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
    def __init__(self):
        self.done = 0

    def ok(self, text: str) -> None:
        self.done += 1
        print(f"  ✓ {self.done:2d} {text}")


def run_browser(world, keep: bool) -> int:
    from playwright.sync_api import sync_playwright

    from app import db

    steps = Steps()
    chrome = os.environ.get("LITFASS_CHROME") or ("/usr/bin/google-chrome" if Path("/usr/bin/google-chrome").exists() else None)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]) if chrome else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("dialog", lambda dialog: dialog.accept())

        page.goto(BASE + "/")
        assert "Zu tun" in page.content() and page.locator(".strip .card").count() >= 3
        steps.ok("Übersicht mit offenen Punkten und zuletzt gesetzten Postern")

        page.goto(BASE + "/bibliothek?status=ohne")
        titles = page.locator(".card .title").all_inner_texts()
        assert "Slow Horses" in titles and "Severance" not in titles
        page.fill("#q", "slow")
        page.press("#q", "Enter")
        page.wait_for_load_state("networkidle")
        assert page.locator(".card").count() == 1
        steps.ok("Bibliothek: Filter «Ohne Set» und Suche")

        bear = db.get().scalar("SELECT key FROM items WHERE title='The Bear'")
        page.goto(f"{BASE}/titel/{bear}")
        page.click(f"button.pick[data-slot='S:{bear}:2']")
        page.wait_for_selector(".drawer.open .set")
        first_set = page.locator(".drawer .set").first
        assert first_set.locator(".pick.match").count() == 1
        assert first_set.locator(".pick.match .cap").inner_text().strip() == "St. 2"
        assert page.locator(".drawer .pick[disabled]").count() == 0
        page.keyboard.press("Escape")
        page.wait_for_timeout(350)
        page.click(f"button.pick[data-slot='S:{bear}:3']")
        page.wait_for_selector(".drawer.open .set")
        cover = page.locator(".drawer .set .cover").first
        assert "Staffel 3" in cover.get_attribute("aria-label")
        assert cover.locator(".s.miss").inner_text().strip() == "3"
        steps.ok("Auswahl hebt das passende Bild hervor, Set zeigt die fehlende Staffel")

        page.click(".tabs button[data-tab=file]")
        page.set_input_files("#upload-input", files=[{"name": "s3.png", "mimeType": "image/png", "buffer": png((200, 40, 40))}])
        page.wait_for_selector(".drawer-foot .chosen .poster")
        with page.expect_navigation(timeout=30000):
            page.click(".drawer-foot .button.primary")
        show_root = world.media / "tv" / "The Bear (2022)"
        season_root = show_root / "Season 03"
        files = sorted(p.name for p in season_root.iterdir() if p.suffix != ".mkv")
        assert any(name.lower().startswith("season03") for name in files), files
        assert world.plex.selected_is_local(world.plex.season_key(bear, 3))
        steps.ok("Datei hochgeladen: Staffel 3 liegt im Ordner, Plex zeigt sie")

        page.click(f"button.pick[data-slot='S:{bear}:3']")
        page.click(".tabs button[data-tab=file]")
        page.set_input_files("#upload-input", files=[{"name": "s3b.png", "mimeType": "image/png", "buffer": png((40, 40, 200))}])
        page.wait_for_selector(".drawer-foot .chosen .poster")
        with page.expect_navigation(timeout=30000):
            page.click(".drawer-foot .button.primary")
        page.wait_for_selector("[data-action=restore]")
        with page.expect_navigation(timeout=30000):
            page.locator("[data-action=restore]").first.click()
        from PIL import Image

        current = next(p for p in season_root.iterdir() if p.name.lower().startswith("season03"))
        assert Image.open(current).convert("RGB").getpixel((10, 10)) == (200, 40, 40)
        steps.ok("Ersetztes Poster aus dem Verlauf wiederhergestellt")

        slow = db.get().scalar("SELECT key FROM items WHERE title='Slow Horses'")
        page.goto(f"{BASE}/titel/{slow}")
        page.click(".bindbox button[data-action=pick]")
        page.wait_for_selector(".drawer.open .set")
        with page.expect_navigation(timeout=30000):
            page.click(".drawer .set .button.primary")
        assert "Gebunden an «Slow Horses»" in page.content()
        page.goto(BASE + "/warten")
        assert "Slow Horses" in page.inner_text(".list") and "Staffel 4" in page.inner_text(".list")
        steps.ok("Set übernommen; Staffel 4 wartet auf das Set")

        page.goto(BASE + "/pruefen?art=detect")
        page.click("[data-action=review-confirm]")
        page.wait_for_timeout(600)
        andor = db.get().scalar("SELECT key FROM items WHERE title='Andor'")
        assert db.get().one("SELECT * FROM bindings WHERE item_key=?", (andor,))
        steps.ok("Erkanntes Set für Andor gebunden")

        page.goto(BASE + "/pruefen?art=new_item")
        with page.expect_navigation(timeout=30000):
            page.locator("[data-action=review-bind]").first.click()
        shogun = db.get().scalar("SELECT key FROM items WHERE title='Shōgun'")
        assert (world.media / "tv" / "Shōgun (2024)" / "show.jpg").exists()
        assert db.get().one("SELECT * FROM bindings WHERE item_key=?", (shogun,))
        steps.ok("Neuer Titel aus «Prüfen» übernommen")

        page.goto(BASE + "/einstellungen?teil=automatik")
        page.click("[data-action=repeat-add][data-repeat=designers]")
        page.locator(".entry.designer input[data-k=name]").last.fill("olivier_pons")
        page.click(".savebar .button.primary")
        page.wait_for_selector("[data-result=save].ok")
        page.reload()
        names = page.locator(".entry.designer input[data-k=name]").evaluate_all("els => els.map(e => e.value)")
        assert names[-1] == "olivier_pons", names
        steps.ok("Designerliste gespeichert")

        page.goto(BASE + "/einstellungen?teil=plex")
        page.click("[data-test=plex]")
        page.wait_for_selector("[data-result=plex].ok")
        page.click("[data-action=paths-check]")
        page.wait_for_selector("[data-result=paths].ok")
        steps.ok("Plex-Verbindung und Pfade geprüft")

        page.click("#theme-toggle")
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        page.click("#theme-toggle")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        steps.ok("Darstellung umschaltbar")

        mobile = browser.new_page(viewport={"width": 390, "height": 820}, is_mobile=True)
        mobile.goto(f"{BASE}/titel/{bear}")
        mobile.click(f"button.pick[data-slot='s:{bear}']")
        mobile.wait_for_selector(".drawer.open")
        mobile.wait_for_timeout(400)  # slide-in transition
        box = mobile.locator(".drawer").bounding_box()
        assert box and box["width"] <= 391 and abs(box["x"]) < 1, box
        steps.ok("Auswahl auf schmalem Bildschirm")

        page.goto(BASE + "/erkennen")
        with page.expect_navigation(timeout=30000):
            page.click("[data-action=adopt]")
        lotr_folder = world.media / "movies" / "Der Herr der Ringe: Die Gefährten (2001)"
        assert (lotr_folder / "poster.jpg").exists()
        lotr = db.get().scalar("SELECT key FROM items WHERE title LIKE 'Der Herr der Ringe%'")
        assert world.plex.selected_is_local(lotr)
        steps.ok("In Plex hochgeladenes Poster als Datei übernommen")

        for path in ("/", "/bibliothek", "/pruefen", "/warten", "/erkennen", "/verlauf", "/protokoll"):
            page.goto(BASE + path)
            for link in page.locator("a[href^='/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")[:60]:
                response = page.request.get(BASE + link)
                assert response.status < 400, (path, link, response.status)
        steps.ok("Alle Links der Hauptseiten erreichbar")

        assert not errors, errors
        browser.close()
    print(f"\n{steps.done} Schritte bestanden.")
    if keep:
        print(f"Litfass läuft weiter: {BASE}  (Strg+C beendet)")
        while True:
            time.sleep(3600)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="litfass-e2e-") as tmp:
        world, server = start_server(Path(tmp))
        try:
            return run_browser(world, args.keep)
        finally:
            server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
