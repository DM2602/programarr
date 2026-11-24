"""The Radarr and Sonarr outputs: add new titles, give existing ones the missing tags, compare with MDBList.

Titles that are already there keep their profile, folder and monitoring. A dry run reads Radarr and
Sonarr but writes nothing, not even new tags.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import arr, db, jobs
from ..arr import library
from ..lists import runs
from ..lists.base import SourceError
from .nodes import ArrOutput, Title, Verdict


@dataclass
class Sent:
    verdicts: dict[int, Verdict] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {key: 0 for key in
                                                             ("added", "tagged", "present", "skipped", "failed")})
    notes: list[str] = field(default_factory=list)
    limited: bool = False

    def put(self, title: Title, port: str, detail: str) -> None:
        self.verdicts[title.media_id] = Verdict(port, detail)
        self.counts[port] += 1


def _order(titles: list[Title]) -> list[Title]:
    """Best list position first, so the limit keeps the top titles."""
    return sorted(titles, key=lambda title: (title.rank if title.rank is not None else 10**6,
                                             title.media.get("title") or ""))


async def send(node: ArrOutput, config: dict, titles: list[Title], dry_run: bool) -> Sent:
    service = node.service
    name = arr.label(service)
    kind = "movie" if service == "radarr" else "show"
    sent = Sent()
    if not titles:
        return sent
    try:
        excluded = await library.exclusions(service)
        tag_ids, to_create = await library.tags(service, config["tags"], create=not dry_run)
        options = await library.options(service)
    except arr.ArrError as exc:
        for title in titles:
            sent.put(title, "failed", str(exc))
        sent.notes.append(str(exc))
        return sent
    profile = next((row["name"] for row in options["profiles"] if row["id"] == config["quality_profile"]),
                   f"profile {config['quality_profile']}")
    if not any(row["path"].rstrip("/") == config["root_folder"].rstrip("/") for row in options["root_folders"]):
        message = f"{name} has no root folder {config['root_folder']}"
        for title in titles:
            sent.put(title, "failed", message)
        sent.notes.append(message)
        return sent
    labels_by_id = {tag_id: label for label, tag_id in tag_ids.items()}
    added = 0
    ordered = _order(titles)
    for index, title in enumerate(ordered):
        jobs.progress(index, len(ordered), f"{name}: {title.media.get('title')}")
        media = title.media
        if media["kind"] != kind:
            sent.put(title, "skipped", "Not a movie" if kind == "movie" else "Not a show")
            continue
        external = media.get("tmdb_id") if service == "radarr" else media.get("tvdb_id")
        if not external:
            sent.put(title, "skipped", "No TVDB ID" if service == "sonarr" else "No TMDB ID")
            continue
        if int(external) in excluded:
            sent.put(title, "skipped", f"On the {name} exclusion list")
            continue
        try:
            found = await library.find(service, media)
        except arr.ArrError as exc:
            sent.put(title, "failed", str(exc))
            continue
        if found:
            have = set(found.get("tags") or [])
            missing_ids = [tag_id for tag_id in tag_ids.values() if tag_id not in have]
            missing = [labels_by_id[tag_id] for tag_id in missing_ids] + to_create
            if not missing:
                sent.put(title, "present", f"Already in {name}")
                continue
            if dry_run:
                sent.put(title, "tagged", "Tags: " + ", ".join(missing))
                continue
            try:
                await library.add_tags(service, int(found["id"]), missing_ids)
            except arr.ArrError as exc:
                sent.put(title, "failed", str(exc))
                continue
            sent.put(title, "tagged", "Tags: " + ", ".join(missing))
            continue
        if added >= config["limit"]:
            sent.put(title, "skipped", f"Over the limit of {config['limit']} new titles per run")
            sent.limited = True
            continue
        detail = f"{profile}, {config['root_folder']}"
        if dry_run:
            added += 1
            sent.put(title, "added", detail)
            continue
        try:
            if service == "radarr":
                await library.add_movie(media, config, list(tag_ids.values()))
            else:
                await library.add_series(media, config, list(tag_ids.values()))
        except arr.ArrError as exc:
            sent.put(title, "failed", str(exc))
            continue
        added += 1
        sent.put(title, "added", detail)
    if sent.limited:
        sent.notes.append(f"{name}: limit of {config['limit']} new titles reached")
    return sent


async def compare(node: ArrOutput, config: dict, titles: list[Title], flow_run_id: int) -> dict | None:
    """The titles that reached the output against MDBList lists (the import lists Radarr and Sonarr use)."""
    if not config.get("compare"):
        return None
    kind = "movie" if node.service == "radarr" else "show"
    lists = []
    listed: set[int] = set()
    for ref in config["compare"]:
        try:
            params = runs.prepare("mdblist", {"list": ref, "limit": 1000})
        except SourceError as exc:
            lists.append({"ref": ref, "run": None, "error": str(exc)})
            continue
        outcome = await runs.preview("mdblist", params, flow_run_id=flow_run_id)
        entry = {"ref": ref, "run": outcome["run"], "error": None}
        if outcome["outcome"] == "error":
            entry["error"] = outcome["message"]
        else:
            rows = db.get().query(
                "SELECT e.media_id FROM parser_entries e JOIN media_cache m ON m.id=e.media_id "
                "WHERE e.run_id=? AND e.status='matched' AND m.kind=?",
                (outcome["run"], kind),
            )
            listed.update(row["media_id"] for row in rows)
        lists.append(entry)
    flow = {title.media_id for title in titles if title.media.get("kind") == kind}
    return {
        "lists": lists,
        "both": sorted(flow & listed),
        "flow_only": sorted(flow - listed),
        "list_only": sorted(listed - flow),
    }
