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
    kept: int | None = None
    unlocked: int | None = None
    restored: int | None = None
    saved: int | None = None
    scanned: int | None = None
    outcome: str | None = None
    run: int | None = None
    dry_run: bool | None = None
    added: int | None = None
    tagged: int | None = None
    present: int | None = None
    deleted: int | None = None


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
    pending: int
    movies: int
    shows: int
    linked: int
    jobs: int
    match_open: int = 0
    queue: int = 0


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
    pending: int
    problems: int
    unlinked_with_files: int
    detect_open: int
    match_open: int = 0
    flows_failed: int = 0


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
    files: int  # poster files in the folders
    backup_files: int | None = None  # None: backup off
    backup_bytes: int | None = None
    exclusions: int
    collections: int = 0  # kept by flows
    queued: int = 0


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
    flows: list[FlowCard]
    flow_adds: list[FlowAdd]
    schedule: list[ScheduleRow]


# -- library ---------------------------------------------------------------------------
class Card(Model):
    key: str
    title: str
    year: int | None = None
    type: Literal["movie", "show"]
    library_key: str | None = None
    thumb: str
    has_file: bool
    flag: Literal["problem", "review", "pending", "new"] | None = None
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
    pending: bool
    pending_since: int | None = None
    dismissed: bool
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
    large: str | None = None  # 720 px wide, for the enlarged view on hover
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


# -- review, pending, match -------------------------------------------------------------
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


class CurrentPoster(Model):
    slot: str
    label: str
    thumb: str
    large: str
    file: bool  # the file in the folder; else what Plex shows


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
    current: list[CurrentPoster] = []  # what the slots show now (new titles and seasons), to keep


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


class PendingRow(Model):
    slot: str
    item_key: str
    title: str
    year: int | None = None
    season: int | None = None
    thumb: str
    pending: bool
    pending_since: int | None = None
    checked_at: int | None = None
    set_title: str | None = None
    designer: str | None = None
    source_label: str | None = None


class PendingPage(Model):
    rows: list[PendingRow]
    max_days: int


class DetectStats(Model):
    shows: int
    movies: int
    complete: int
    job: Job | None = None


class BackupView(Model):
    enabled: bool
    files: int
    bytes: int
    folder: str


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
    backup_posters: bool
    plex_poster_files: Literal["off", "movies", "all"]
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
    mdblist_key: bool
    mdblist_api: str
    trakt_client_id: bool
    trakt_api: str
    flixpatrol_base: str
    flixpatrol_delay: float
    ratings_days: int
    runs_keep_days: int
    radarr_url: str
    radarr_external_url: str
    radarr_key: bool
    sonarr_url: str
    sonarr_external_url: str
    sonarr_key: bool
    queue_days: int
    automation: bool
    designers: list[Designer]
    use_mediux_follows: bool
    source_order: list[str]
    auto_new: bool
    auto_require_complete: bool
    auto_count_specials: bool
    auto_collection_consistency: bool
    poll_minutes: int
    folder_check_hours: int
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


# -- lists -----------------------------------------------------------------------
RunStatus = Literal["running", "ok", "partial", "error"]
MatchStatus = Literal["matched", "uncertain", "failed", "excluded"]
MediaKind = Literal["movie", "show"]


class FieldOption(Model):
    value: str
    label: str


class SourceField(Model):
    key: str
    label: str
    type: Literal["text", "number", "decimal", "select", "multi", "bool", "tokens"]
    options: list[FieldOption]
    default: Any = None
    required: bool
    placeholder: str | None = None
    suggestions: list[str]
    min: float | None = None
    max: float | None = None
    when: dict[str, list[str]] | None = None
    step: float | None = None
    dynamic: str | None = None
    hint: str | None = None


class ListSource(Model):
    key: str
    label: str
    fields: list[SourceField]
    missing: str | None = None


class ListSources(Model):
    sources: list[ListSource]


class PreviewBody(Body):
    source: str
    params: dict[str, Any] = {}


class RunBrief(Model):
    id: int
    source: str
    source_ref: str
    label: str
    started_at: int | None = None
    finished_at: int | None = None
    status: RunStatus
    error: str | None = None
    total: int
    matched: int
    uncertain: int
    failed: int
    excluded: int


class RunsPage(Model):
    runs: list[RunBrief]


class MediaView(Model):
    id: int
    kind: MediaKind
    tmdb_id: int | None = None
    imdb_id: str | None = None
    tvdb_id: int | None = None
    title: str
    original_title: str | None = None
    year: int | None = None
    status: str | None = None
    seasons: int | None = None
    poster: str | None = None
    imdb_rating: float | None = None
    imdb_votes: int | None = None
    rt_critics: int | None = None
    rt_audience: int | None = None
    ratings_at: int | None = None


class Candidate(Model):
    kind: MediaKind
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    popularity: float = 0
    similarity: float | None = None
    poster: str | None = None


class EntryList(Model):
    run_id: int
    label: str
    position: int | None = None


class EntryView(Model):
    id: int
    run_id: int
    position: int | None = None
    title: str
    year: int | None = None
    kind: MediaKind | None = None
    ids: dict[str, Any]
    url: str | None = None
    extra: dict[str, Any]
    fingerprint: str
    status: MatchStatus
    method: str | None = None
    score: float | None = None
    error: str | None = None
    media: MediaView | None = None
    plex_key: str | None = None
    candidates: list[Candidate]
    source: str | None = None
    label: str | None = None
    lists: list[EntryList] | None = None


class RunDetail(RunBrief):
    params: dict[str, Any]
    notes: list[str]
    previous: int | None = None
    entries: list[EntryView]


class ProblemsPage(Model):
    rows: list[EntryView]


class MatchesPage(Model):
    rows: list[EntryView]
    total: int
    page: int
    per_page: int


class FixBody(Body):
    kind: MediaKind
    tmdb_id: int


class Changed(Model):
    ok: bool = True
    changed: int = 0
    message: str | None = None


class OverrideView(Model):
    id: int
    source: str
    fingerprint: str
    title: str
    year: int | None = None
    kind: MediaKind | None = None
    ignore: bool
    media: MediaView | None = None
    created_at: int | None = None


class OverridesPage(Model):
    rows: list[OverrideView]


class ExclusionView(Model):
    id: int | None = None  # Programarr's own exclusion; None: only on Radarr's or Sonarr's list
    scope: str
    reason: str | None = None
    created_at: int | None = None
    media: MediaView | None = None  # None when the title was never read from a list
    plex_key: str | None = None
    title: str
    year: int | None = None
    kind: MediaKind
    service: Literal["radarr", "sonarr"] | None = None  # also on that service's exclusion list
    external_id: int | None = None  # TMDB id (Radarr) or TVDB id (Sonarr)
    pending: bool = False  # waits to be written to Radarr or Sonarr


class ExclusionsPage(Model):
    rows: list[ExclusionView]
    synced_at: int | None = None
    services: list[Literal["radarr", "sonarr"]] = []
    errors: list[str] = []


class ExclusionBody(Body):
    media_id: int | None = None
    kind: MediaKind | None = None
    tmdb_id: int | None = None
    reason: str | None = None


class MediaSearch(Model):
    results: list[Candidate]


class MdblistListView(Model):
    id: int
    name: str
    slug: str
    user: str
    items: int | None = None
    dynamic: bool
    mediatype: str


class MdblistLists(Model):
    lists: list[MdblistListView]


class ArrLink(Model):
    """Where a title stands in Radarr or Sonarr; service is None when that one is not set up."""

    service: Literal["radarr", "sonarr"] | None = None
    label: str | None = None
    present: bool = False
    monitored: bool | None = None
    downloaded: bool | None = None
    url: str | None = None
    error: str | None = None


class MissingSeason(Model):
    number: int
    episodes: int | None = None
    air_date: str | None = None
    poster: str | None = None
    sonarr: Literal["monitored", "unmonitored", "absent"] | None = None
    files: int | None = None


class MissingSeasons(Model):
    """Seasons Plex does not have yet (from TMDB and Sonarr)."""

    seasons: list[MissingSeason]
    sonarr_url: str | None = None


# -- flows -----------------------------------------------------------------------------
FlowRunStatus = Literal["running", "ok", "partial", "error"]
NodeCategory = Literal["source", "filter", "output"]


class NodeFact(Model):
    """One labelled setting of an output, e.g. Profile: HD-1080p."""
    label: str
    value: str


class NodeTypePort(Model):
    key: str
    label: str


class FlowPosition(Model):
    x: float = 0
    y: float = 0


class FlowNode(Model):
    id: str
    type: str
    config: dict[str, Any] = {}
    position: FlowPosition = FlowPosition()


class FlowEdge(Model):
    source: str
    port: str
    target: str


class FlowGraph(Model):
    nodes: list[FlowNode] = []
    edges: list[FlowEdge] = []


class FlowProblem(Model):
    node: str | None = None
    message: str


class CollectionTotals(Model):
    """What the collection outputs of a run changed."""
    outputs: int
    added: int
    removed: int
    queued: int
    missing: int
    failed: int


class FlowRunBrief(Model):
    id: int
    flow_id: int
    flow_name: str
    flow_version: int
    trigger: Literal["manual", "schedule"]
    dry_run: bool
    started_at: int | None = None
    finished_at: int | None = None
    status: FlowRunStatus
    error: str | None = None
    titles: int
    added: int
    tagged: int
    present: int
    skipped: int
    failed: int
    collections: CollectionTotals | None = None


class FlowView(Model):
    id: int
    name: str
    version: int
    schedule: str | None = None
    schedule_text: str
    enabled: bool
    dry_run: bool
    next_run_at: int | None = None
    created_at: int | None = None
    updated_at: int | None = None
    problems: list[FlowProblem]
    summaries: dict[str, str]
    facts: dict[str, list[NodeFact]] = {}  # outputs: profile, folder, tags
    ports: dict[str, list[NodeTypePort]]  # outputs per node; a filter's depend on its rules
    outputs: list[str]
    nodes: int
    last_run: FlowRunBrief | None = None
    graph: FlowGraph | None = None


class FlowsPage(Model):
    flows: list[FlowView]


class FlowCreate(Body):
    name: str
    graph: FlowGraph | None = None
    schedule: str | None = None


class FlowUpdate(Body):
    """Only the fields sent change. schedule null (or empty) means manual."""
    name: str | None = None
    graph: FlowGraph | None = None
    schedule: str | None = None
    enabled: bool | None = None
    dry_run: bool | None = None


class FlowRunBody(Body):
    dry_run: bool | None = None


class FlowCheckBody(Body):
    graph: FlowGraph


class FlowCheck(Model):
    problems: list[FlowProblem]
    summaries: dict[str, str]  # one line per node, for the editor
    facts: dict[str, list[NodeFact]] = {}  # outputs: profile, folder, tags
    ports: dict[str, list[NodeTypePort]]


class ScheduleCheck(Model):
    ok: bool
    schedule: str | None = None
    text: str
    next_run_at: int | None = None
    message: str | None = None


class PortCount(Model):
    key: str
    label: str
    count: int


class CompareList(Model):
    ref: str
    name: str | None = None  # the list's title on MDBList
    run: int | None = None
    error: str | None = None


class CompareView(Model):
    lists: list[CompareList]
    both: int
    flow_only: list[MediaView]
    list_only: list[MediaView]


class RunNode(Model):
    id: str
    type: str
    label: str
    category: str
    summary: str
    facts: list[NodeFact] = []  # outputs: profile, folder, tags
    count_in: int
    ports: list[PortCount]
    entries: dict[str, int] | None = None
    run: int | None = None
    error: str | None = None
    compare: CompareView | None = None
    collection_id: int | None = None  # a collection output: its collection


class FlowRunDetail(FlowRunBrief):
    notes: list[str]
    current: bool
    nodes: list[RunNode]
    sources: list[RunBrief]
    previous: int | None = None
    next: int | None = None


class FlowRunsPage(Model):
    runs: list[FlowRunBrief]
    total: int
    page: int
    pages: int


class TraceStep(Model):
    node: str
    type: str = ""
    label: str
    category: str
    port: str | None = None
    port_label: str
    detail: str


class FlowTitle(Model):
    media: MediaView
    outcomes: list[TraceStep]
    steps: list[TraceStep]


class FlowTitlesPage(Model):
    rows: list[FlowTitle]
    counts: dict[str, int]
    total: int
    page: int
    pages: int


class NodeTypeView(Model):
    type: str
    label: str
    category: NodeCategory
    inputs: bool
    ports: list[NodeTypePort]
    fields: list[SourceField]  # for a filter with rules: the fields of one rule
    missing: str | None = None
    rules: bool = False  # one output per rule, plus No match
    each_default: bool | None = None  # a title fitting several rules goes to each of them


class NodeTypes(Model):
    types: list[NodeTypeView]


class ArrProfile(Model):
    id: int
    name: str


class ArrRootFolder(Model):
    path: str
    free_space: int | None = None


class ArrTag(Model):
    id: int
    label: str


class ArrOptions(Model):
    service: Literal["radarr", "sonarr"]
    configured: bool
    profiles: list[ArrProfile]
    root_folders: list[ArrRootFolder]
    tags: list[ArrTag]
    error: str | None = None


# -- dashboard ----------------------------------------------------------------------------------
class ScheduleRow(Model):
    key: str
    label: str
    interval: int | None = None  # seconds; None when off
    last: int | None = None
    next: int | None = None  # None: off, paused or unknown
    state: Literal["on", "off", "paused"]


class CompareSummary(Model):
    """A run's MDBList comparisons, added up over its outputs."""
    lists: list[CompareList]
    both: int
    flow_only: int
    list_only: int


class FlowCard(FlowView):
    compare: CompareSummary | None = None


class FlowAdd(Model):
    """A title a live run added to Radarr or Sonarr."""
    media: MediaView
    service: Literal["radarr", "sonarr"]
    flow_id: int
    flow_name: str
    run_id: int
    at: int | None = None
    plex_key: str | None = None


Overview.model_rebuild()


# -- collections -------------------------------------------------------------------
CollectionState = Literal["in_plex", "queued", "missing"]


class CollectionCounts(Model):
    in_plex: int
    queued: int
    missing: int


class CollectionBrief(Model):
    id: int
    title: str  # as in Plex, or the name before the first run
    name_template: str
    library_key: str
    library: str
    kind: MediaKind
    flow_id: int | None = None
    flow_name: str | None = None
    in_flow: bool  # False: its output was removed, the collection stays in Plex
    live: bool  # its flow is live: Plex follows the runs
    sync_mode: Literal["mirror", "append"]
    item_order: Literal["list", "release", "alpha"]
    max_items: int
    plex_key: str | None = None
    show_recommended: bool
    show_home: bool
    show_shared: bool
    image: str
    poster_source: str | None = None
    synced_at: int | None = None
    error: str | None = None
    counts: CollectionCounts


class CollectionsPage(Model):
    collections: list[CollectionBrief]


class CollectionTitle(Model):
    media: MediaView
    state: CollectionState
    position: int | None = None
    service: Literal["radarr", "sonarr"] | None = None
    item_key: str | None = None
    queued_at: int | None = None
    expires_at: int | None = None


class CollectionDetail(CollectionBrief):
    titles: list[CollectionTitle]


class CollectionUpdate(Body):
    show_recommended: bool | None = None
    show_home: bool | None = None
    show_shared: bool | None = None


class OtherCollection(Model):
    key: str
    title: str
    library_key: str
    library: str
    count: int
    smart: bool
    automatic: bool  # made by Plex from TMDB's movie collections (franchises)
    image: str
    updated_at: int | None = None
    labels: list[str]


class OtherCollections(Model):
    collections: list[OtherCollection]
    error: str | None = None


class KeysBody(Body):
    keys: list[str]


class TokenBody(Body):
    token: str


class TpdbResult(Model):
    id: str
    title: str
    year: int | None = None
    url: str


class TpdbResults(Model):
    results: list[TpdbResult]
    error: str | None = None


class CollectionImages(Model):
    images: list[ImageView]
    error: str | None = None


class QueueEntry(Model):
    collection_id: int
    collection: str
    media: MediaView
    service: Literal["radarr", "sonarr"] | None = None
    queued_at: int | None = None
    expires_at: int | None = None


class QueuePage(Model):
    entries: list[QueueEntry]
    days: int


class PlexLibrary(Model):
    key: str
    title: str
    type: str


class PlexLibraries(Model):
    libraries: list[PlexLibrary]
