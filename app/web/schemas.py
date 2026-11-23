"""Response and request models. They define the JSON the web app gets and feed its generated TypeScript types."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    # In responses every field is present (defaults included), so the generated types mark them required.
    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)


class Body(BaseModel):
    """Request bodies: fields with a default stay optional."""
    model_config = ConfigDict(extra="ignore")


# -- general ---------------------------------------------------------------------
class Ok(Model):
    ok: bool = True
    message: str | None = None


class JobStarted(Model):
    job: int


JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class Outcome(Model):
    slot: str
    ok: bool
    message: str
    path: str | None = None


class JobResult(BaseModel):
    """Result fields of the different job kinds; each kind fills a few of them."""
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    message: str | None = None
    outcomes: list[Outcome] | None = None
    written: int | None = None
    failed: int | None = None
    waiting: list[int] | int | None = None
    items: int | None = None
    new_items: int | None = None
    new_seasons: int | None = None
    skipped: int | None = None
    checked: int | None = None
    found: int | None = None
    high: int | None = None
    errors: int | None = None
    confidence: str | None = None
    applied: int | None = None
    changed: int | None = None
    gave_up: int | None = None
    adopted: int | None = None
    scanned: int | None = None
    outcome: str | None = None


class JobBrief(Model):
    id: int
    kind: str
    title: str
    status: JobStatus
    done: int = 0
    total: int = 0
    message: str | None = None


class Job(JobBrief):
    result: JobResult | None = None
    created_at: int | None = None
    started_at: int | None = None
    finished_at: int | None = None


class Counts(Model):
    review: int
    review_kinds: dict[str, int]
    waiting: int
    movies: int
    shows: int
    linked: int
    jobs: int


class Status(Model):
    version: str
    setup_done: bool
    automation: bool
    last_sync: int
    counts: Counts
    jobs: list[JobBrief]


# -- auth --------------------------------------------------------------------------
class AuthState(Model):
    password_required: bool
    signed_in: bool
    version: str


class LoginBody(Body):
    password: str


# -- dashboard -----------------------------------------------------------------------
class SetupSteps(Model):
    plex: bool
    libraries: bool
    sources: bool
    designers: bool


class Todo(Model):
    review: int
    waiting: int
    problems: int
    unlinked_with_files: int
    detect_open: int


class RecentCard(Model):
    item_key: str
    title: str
    what: str | None = None
    type: str
    source_label: str | None = None
    designer: str | None = None
    origin_label: str
    at: int
    thumb: str


class LibraryFacts(Model):
    movies: int
    shows: int
    linked: int
    complete: int


class Overview(Model):
    setup_done: bool
    automation: bool
    steps: SetupSteps
    last_sync: int
    last_set_check: int
    todo: Todo
    recent: list[RecentCard]
    facts: LibraryFacts
    jobs: list[Job]


# -- library ---------------------------------------------------------------------------
class Card(Model):
    key: str
    title: str
    year: int | None = None
    type: Literal["movie", "show"]
    library_key: str | None = None
    thumb: str
    has_file: bool
    flag: Literal["problem", "review", "waiting", "new"] | None = None
    complete: bool
    designer: str | None = None
    source_label: str | None = None
    files: int


class FilterOption(Model):
    key: str
    label: str


class LibraryRef(Model):
    key: str
    title: str
    type: str


class LibraryPage(Model):
    cards: list[Card]
    total: int
    page: int
    pages: int
    page_size: int
    filters: list[FilterOption]
    libraries: list[LibraryRef]
    empty_library: bool


# -- one title -------------------------------------------------------------------------
class ItemInfo(Model):
    key: str
    type: Literal["movie", "show"]
    title: str
    year: int | None = None
    original_title: str | None = None
    search_title: str | None = None
    collection_name: str | None = None
    show_status: str | None = None
    folder: str | None = None
    local_folder: str | None = None
    tpdb_url: str | None = None
    library_key: str | None = None
    gone: bool = False


class SlotView(Model):
    id: str
    kind: Literal["movie", "show", "season"]
    season: int | None = None
    label: str
    file: str | None = None
    file_name: str | None = None
    folder_name: str | None = None
    thumb: str
    source: str | None = None
    source_label: str | None = None
    designer: str | None = None
    mode: str | None = None
    mode_label: str
    plex_state: str | None = None
    state: Literal["ok", "warn", "bad", "none"]
    state_text: str
    needs_force: bool
    applied_at: int | None = None
    waiting: bool
    waiting_since: int | None = None
    wait_given_up: bool
    gone: bool


class BindingView(Model):
    source: str
    source_label: str
    set_key: str
    set_title: str
    designer: str
    set_url: str | None = None
    bound_at: int | None = None
    checked_at: int | None = None
    state: str
    note: str | None = None


class HistoryRow(Model):
    id: int
    at: int
    item_key: str | None = None
    slot_id: str | None = None
    title: str
    action: str
    action_label: str
    origin: str | None = None
    origin_label: str
    source_label: str | None = None
    designer: str | None = None
    note: str | None = None
    archived: str | None = None


class ReviewBrief(Model):
    id: int
    kind: str
    kind_label: str
    reason: str
    season: int | None = None


class Link(Model):
    label: str
    url: str


class ItemDetail(Model):
    item: ItemInfo
    library: LibraryRef | None = None
    slots: list[SlotView]
    binding: BindingView | None = None
    history: list[HistoryRow]
    reviews: list[ReviewBrief]
    links: list[Link]
    complete: bool
    seasons: int
    specials: bool
    has_files: bool


# -- picker ----------------------------------------------------------------------------
class ImageView(Model):
    token: str
    thumb: str
    kind: str | None = None
    season: int | None = None
    label: str = ""
    language: str | None = None
    designer: str | None = None
    likes: int | None = None
    width: int | None = None
    height: int | None = None
    source: str
    selected: bool = False
    local: bool = False


class SetView(Model):
    token: str
    source: str
    source_label: str
    set_key: str
    title: str
    designer: str
    url: str | None = None
    loaded: bool
    images: list[ImageView]
    rank: int | None = None
    covers: list[int] = []
    missing: list[int] = []
    has_main: bool = True
    complete: bool = False
    hidden: bool = False
    bound: bool = False
    more: int = 0


class SetsResponse(Model):
    type: Literal["movie", "show"]
    sets: list[SetView]
    errors: dict[str, str]
    seasons: list[int]
    tpdb_url: str | None = None
    tpdb_sure: bool = False


class Images(Model):
    images: list[ImageView]


class OnlineSource(Model):
    key: str
    label: str
    enabled: bool
    error: str | None = None
    images: list[ImageView]


class OnlineResponse(Model):
    sources: list[OnlineSource]


class ApplyBody(Body):
    slot: str
    token: str


class BindBody(Body):
    token: str
    replace_all: bool = True
    review: int | None = None


class TokenBody(Body):
    token: str


class SlotBody(Body):
    slot: str


class UrlBody(Body):
    url: str


class ImageUrlBody(Body):
    url: str
    kind: str = "movie"
    season: int | None = None


class TpdbSaved(Model):
    message: str
    url: str


# -- review, waiting, match -------------------------------------------------------------
class ComparePair(Model):
    slot: str
    label: str
    current: str
    other: str


class SetSummary(Model):
    title: str
    designer: str | None = None
    source_label: str
    url: str | None = None


class ReviewView(Model):
    id: int
    kind: str
    kind_label: str
    item_key: str
    title: str
    year: int | None = None
    type: str | None = None
    season: int | None = None
    reason: str
    created_at: int | None = None
    thumb: str
    candidates: list[SetView]
    seasons: list[int]
    errors: dict[str, str]
    pairs: list[ComparePair]
    changes: list[ComparePair]
    set: SetSummary | None = None
    confidence: Literal["high", "medium"] | None = None
    via: str | None = None
    slot: str | None = None


class KindCount(Model):
    key: str
    label: str
    count: int


class ReviewsPage(Model):
    reviews: list[ReviewView]
    kinds: list[KindCount]
    total: int


class ConfirmAll(Model):
    ok: bool = True
    message: str
    count: int


class WaitingRow(Model):
    slot: str
    item_key: str
    title: str
    year: int | None = None
    season: int | None = None
    thumb: str
    waiting: bool
    waiting_since: int | None = None
    checked_at: int | None = None
    set_title: str | None = None
    designer: str | None = None
    source_label: str | None = None


class WaitingPage(Model):
    rows: list[WaitingRow]
    max_days: int


class DetectStats(Model):
    shows: int
    movies: int
    complete: int
    job: Job | None = None


# -- history and log --------------------------------------------------------------------
class HistoryPage(Model):
    rows: list[HistoryRow]
    page: int
    pages: int
    total: int


class LogRow(Model):
    id: int
    at: int
    level: Literal["info", "warning", "error"]
    area: str | None = None
    message: str
    item_key: str | None = None
    title: str | None = None


class LogPage(Model):
    rows: list[LogRow]


# -- settings ---------------------------------------------------------------------------
class PathMap(Model):
    plex: str
    local: str


class Designer(Model):
    source: Literal["any", "mediux", "tpdb"] = "any"
    name: str


class SettingsValues(Model):
    """Editable settings; secrets only say whether they are set."""
    setup_done: bool
    plex_url: str
    plex_token: bool
    path_maps: list[PathMap]
    naming_movie: Literal["auto", "poster", "file"]
    naming_show: Literal["auto", "poster", "show", "folder"]
    naming_season: Literal["auto", "root", "root_plain", "folder"]
    keep_png: bool
    conflicts: Literal["archive", "keep"]
    unlock_fields: bool
    verify_plex: bool
    archive_keep: int
    mediux_token: bool
    mediux_quality: Literal["original", "optimized"]
    mediux_api: str
    mediux_site: str
    tpdb_email: str
    tpdb_password: bool
    tpdb_base: str
    tpdb_delay: float
    tpdb_pages: int
    flaresolverr_url: str
    tmdb_key: bool
    tmdb_api: str
    tmdb_image: str
    tmdb_languages: str
    fanart_key: bool
    fanart_client_key: bool
    fanart_api: str
    tvmaze_enabled: bool
    tvmaze_api: str
    allow_private_urls: bool
    automation: bool
    designers: list[Designer]
    use_mediux_follows: bool
    source_order: list[str]
    auto_new: bool
    auto_require_complete: bool
    auto_count_specials: bool
    auto_collection_consistency: bool
    poll_minutes: int
    set_check_hours: int
    wait_max_days: int
    set_changes: Literal["notify", "apply"]
    detect_movies: bool
    discord_webhook: bool
    discord_events: dict[str, bool]
    base_url: str
    hook_token: str


class LibraryView(Model):
    key: str
    title: str
    type: str
    enabled: bool
    locations: list[str]


class Hooks(Model):
    sonarr: str
    radarr: str
    plex: str


class Follows(Model):
    follow: list[str]
    hide: list[str]


class NamingDetected(Model):
    movie: str | None = None
    show: str | None = None
    season: str | None = None


class EventOption(Model):
    key: str
    label: str


class SettingsView(Model):
    values: SettingsValues
    libraries: list[LibraryView]
    hooks: Hooks
    discord_events: list[EventOption]
    naming_detected: NamingDetected
    follows: Follows
    data_dir: str
    version: str


class Saved(Model):
    ok: bool = True
    saved: list[str]


class Libraries(Model):
    libraries: list[LibraryView]


class EnabledBody(Body):
    enabled: list[str]


class PathExample(Model):
    plex: str
    local: str
    readonly: bool = False


class PathCheck(Model):
    checked: int
    ok: int
    missing: int
    readonly: int
    examples: list[PathExample]


class TestBody(Body):
    value: str | None = None
    extra: str | None = None


class TestResult(Model):
    ok: bool
    message: str


def dump(model: type[Model], data: Any) -> Any:
    """Validate a dict against a model (used where a router returns plain dicts)."""
    return model.model_validate(data)
