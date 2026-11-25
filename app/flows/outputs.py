"""The Radarr and Sonarr outputs: add new titles, give existing ones the missing tags, compare with MDBList.

Titles that are already there keep their profile, folder and monitoring. A dry run reads Radarr and
Sonarr but writes nothing, not even new tags.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import arr, db, jobs
from ..arr import library
from ..media import mdblist
from ..media import store as media_store
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
    in_plex = media_store.in_plex([title.media_id for title in titles])
    added = 0
    ordered = _order(titles)
    for index, title in enumerate(ordered):
        jobs.progress(index, len(ordered), f"{name}: {title.media.get('title')}")
        media = title.media
        if media["kind"] != kind:
            sent.put(title, "skipped", "Not a movie" if kind == "movie" else "Not a show")
            continue
        if title.media_id in in_plex:
            # In the library already: neither added nor tagged, whatever Radarr or Sonarr hold.
            sent.put(title, "present", "In Plex")
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
                sent.put(title, "present", "")
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


async def _read_list(ref: str, flow_run_id: int) -> dict:
    """One MDBList list as matched titles: {"run", "error", "titles": [(media id, kind)]}."""
    try:
        params = runs.prepare("mdblist", {"list": ref, "limit": 1000})
    except SourceError as exc:
        return {"run": None, "error": str(exc), "titles": []}
    outcome = await runs.preview("mdblist", params, flow_run_id=flow_run_id)
    if outcome["outcome"] == "error":
        return {"run": outcome["run"], "error": outcome["message"], "titles": []}
    rows = db.get().query(
        "SELECT e.media_id, m.kind FROM parser_entries e JOIN media_cache m ON m.id=e.media_id "
        "WHERE e.run_id=? AND e.status='matched'", (outcome["run"],))
    return {"run": outcome["run"], "error": None, "titles": [(row["media_id"], row["kind"]) for row in rows]}


async def compare(node: ArrOutput, config: dict, titles: list[Title], flow_run_id: int,
                  read: dict[str, dict] | None = None) -> dict | None:
    """The titles that reached the output against MDBList lists (the import lists Radarr and Sonarr use).

    read keeps the lists already read in this run, so two outputs comparing with one list read it once.
    """
    if not config.get("compare"):
        return None
    read = read if read is not None else {}
    kind = "movie" if node.service == "radarr" else "show"
    lists = []
    listed: set[int] = set()
    for ref in config["compare"]:
        if ref not in read:
            read[ref] = await _read_list(ref, flow_run_id)
        found = read[ref]
        lists.append({"ref": ref, "name": mdblist.cached_list_name(ref), "run": found["run"], "error": found["error"]})
        listed.update(media_id for media_id, media_kind in found["titles"] if media_kind == kind)
    flow = {title.media_id for title in titles if title.media.get("kind") == kind}
    return {
        "lists": lists,
        "both": sorted(flow & listed),
        "flow_only": sorted(flow - listed),
        "list_only": sorted(listed - flow),
    }
