import { Fragment, type ReactNode } from "react";
import { Link } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { Button } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { Badge } from "../../components/ui";
import { hostOf } from "../../lib/format";

export type Entry = Schemas["EntryView"];
export type Media = Schemas["MediaView"];
export type Candidate = Schemas["Candidate"];
export type RunBrief = Schemas["RunBrief"];

export const SOURCE_LABELS: Record<string, string> = {
  flixpatrol: "FlixPatrol",
  mdblist: "MDBList",
  tmdb: "TMDB",
  trakt: "Trakt",
};

export const METHOD_LABELS: Record<string, string> = {
  exact: "Exact",
  "year±1": "Year ±1",
  title: "Title only",
  "alt-title": "Other title",
  id: "By ID",
  fixed: "Fixed",
  similar: "Similar",
  ignored: "Ignored",
  error: "Error",
};

export const KIND_LABELS: Record<string, string> = { movie: "Movie", show: "Show" };

export const POSTER_FALLBACK = "/img/tmdb/none";

export function isOpen(entry: Pick<Entry, "status">): boolean {
  return entry.status === "uncertain" || entry.status === "failed";
}

export function StatusBadge({ entry }: { entry: Entry }) {
  if (entry.status === "uncertain") return <Badge tone="yellow">To check</Badge>;
  if (entry.status === "failed") return <Badge tone="red">{entry.method === "error" ? "Error" : "Not found"}</Badge>;
  if (entry.status === "excluded") return <Badge tone="gray">{entry.method === "ignored" ? "Ignored" : "Excluded"}</Badge>;
  return null;
}

export function Ratings({ media }: { media: Media }) {
  const parts: { label: string; value: string; title: string }[] = [];
  if (media.imdb_rating) {
    const votes = media.imdb_votes ? ` (${media.imdb_votes.toLocaleString("en-US")} votes)` : "";
    parts.push({ label: "IMDb", value: media.imdb_rating.toFixed(1), title: `IMDb ${media.imdb_rating.toFixed(1)}${votes}` });
  }
  if (media.rt_critics !== null && media.rt_critics !== undefined) {
    parts.push({ label: "RT", value: `${media.rt_critics}%`, title: "Rotten Tomatoes critics" });
  }
  if (media.rt_audience !== null && media.rt_audience !== undefined) {
    parts.push({ label: "Audience", value: `${media.rt_audience}%`, title: "Rotten Tomatoes audience" });
  }
  if (!parts.length) return null;
  return (
    <span className="ratings inline-flex flex-wrap gap-x-3 text-xs text-gray-400">
      {parts.map((part) => (
        <span key={part.label} title={part.title}>
          {part.label} <span className="font-semibold text-gray-200">{part.value}</span>
        </span>
      ))}
    </span>
  );
}

export function tmdbUrl(media: { kind: string; tmdb_id?: number | null }): string | null {
  if (!media.tmdb_id) return null;
  return `https://www.themoviedb.org/${media.kind === "show" ? "tv" : "movie"}/${media.tmdb_id}`;
}

const LINK_LABELS: [string, string][] = [
  ["flixpatrol", "FlixPatrol"],
  ["trakt.tv", "Trakt"],
  ["themoviedb.org", "TMDB"],
  ["mdblist", "MDBList"],
];

export function linkLabel(url: string): string {
  const host = hostOf(url);
  return LINK_LABELS.find(([part]) => host.includes(part))?.[1] ?? host;
}

function statText(stat: string): string {
  const days = /^(\d+) d$/.exec(stat);
  if (days) return `${days[1]} ${days[1] === "1" ? "day" : "days"} in TOP 10`;
  return stat;
}

/** Where the raw entry came from: section and FlixPatrol's days in the TOP 10. */
export function origin(entry: Entry): string {
  const extra = entry.extra as Record<string, unknown>;
  const parts: string[] = [];
  if (typeof extra.section === "string") parts.push(SECTION_LABELS[extra.section] ?? extra.section);
  if (typeof extra.stat === "string" && extra.stat) parts.push(statText(extra.stat));
  return parts.join(", ");
}

/** Small line under an entry: its lists (or section) and the page at the source. */
export function EntryMeta({ entry, showLists = false }: { entry: Entry; showLists?: boolean }) {
  const parts: ReactNode[] = [];
  if (showLists && entry.lists?.length) {
    for (const list of entry.lists) {
      parts.push(
        <Link to={`/lists/runs/${list.run_id}`} className="hover:text-gray-300 hover:underline">
          {list.label}
          {list.position ? ` #${list.position}` : ""}
        </Link>,
      );
    }
  } else if (origin(entry)) {
    parts.push(<span>{origin(entry)}</span>);
  }
  if (entry.url) {
    parts.push(
      <a href={entry.url} target="_blank" rel="noopener noreferrer" className="hover:text-gray-300 hover:underline">
        {linkLabel(entry.url)}
      </a>,
    );
  }
  if (!parts.length) return null;
  return (
    <p className="mt-0.5 truncate text-xs text-gray-500">
      {parts.map((part, index) => (
        <Fragment key={index}>
          {index > 0 && ", "}
          {part}
        </Fragment>
      ))}
    </p>
  );
}

const SECTION_LABELS: Record<string, string> = {
  movies: "Movies",
  shows: "TV Shows",
  overall: "Overall",
  "kids-movies": "Kids Movies",
  "kids-shows": "Kids TV Shows",
};

/** Change, exclude, fix, ignore or undo, depending on the entry's state. */
export function EntryActions({ entry, onFix }: { entry: Entry; onFix: (entry: Entry) => void }) {
  const run = useRunJob();
  const dialogs = useDialogs();
  const path = { params: { path: { entry_id: entry.id } } };
  const exclude = async () => {
    if (!entry.media) return;
    const reason = await dialogs.prompt({
      title: `Exclude ${entry.media.title}`,
      label: "Reason",
      placeholder: "Optional",
      confirm: "Exclude",
    });
    if (reason === null) return;
    const mediaId = entry.media.id;
    await run(() => unwrap(api.POST("/api/exclusions", { body: { media_id: mediaId, reason: reason || null } })), "Excluding…");
  };
  if (entry.status === "excluded") {
    if (entry.method === "ignored") {
      return (
        <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/lists/entries/{entry_id}/reset", path)), "Matching again…")}>
          Undo
        </Button>
      );
    }
    const mediaId = entry.media?.id;
    return mediaId ? (
      <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.DELETE("/api/exclusions/media/{media_id}", { params: { path: { media_id: mediaId } } })), "Including…")}>
        Include
      </Button>
    ) : null;
  }
  if (isOpen(entry)) {
    return (
      <>
        <Button size="sm" variant="primary" onClick={() => onFix(entry)} data-action="fix">
          Fix
        </Button>
        <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/lists/entries/{entry_id}/ignore", path)), "Ignoring…")} data-action="ignore">
          Ignore
        </Button>
      </>
    );
  }
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => onFix(entry)} data-action="change">
        Change
      </Button>
      {entry.method === "fixed" && (
        <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/lists/entries/{entry_id}/reset", path)), "Matching again…")}>
          Reset
        </Button>
      )}
      <Button size="sm" variant="ghost" onClick={exclude} data-action="exclude">
        Exclude
      </Button>
    </>
  );
}

/** One raw entry and what it was matched to. */
export function EntryRow({ entry, onFix, showList = false }: { entry: Entry; onFix: (entry: Entry) => void; showList?: boolean }) {
  const media = entry.media;
  const dim = entry.status === "excluded";
  return (
    <div className="entry flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center" data-entry={entry.id} data-status={entry.status}>
      <div className={`flex min-w-0 flex-1 items-center gap-4 ${dim ? "opacity-60" : ""}`}>
        {entry.position !== null && entry.position !== undefined && !showList && (
          <span className="w-6 shrink-0 text-right text-sm font-semibold text-gray-500 tabular-nums">{entry.position}</span>
        )}
        <img
          src={media?.poster ?? entry.candidates[0]?.poster ?? POSTER_FALLBACK}
          alt=""
          loading="lazy"
          className={`aspect-[2/3] w-11 shrink-0 rounded object-cover ring-1 ring-gray-700 ${media ? "" : "opacity-40 grayscale"}`}
        />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate font-semibold text-gray-100">{entry.title}</span>
            {entry.year && <span className="text-sm text-gray-400">{entry.year}</span>}
            {entry.kind && <span className="text-xs text-gray-500">{KIND_LABELS[entry.kind]}</span>}
            <StatusBadge entry={entry} />
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-400">
            {media ? (
              <>
                <span className="min-w-0 truncate">
                  {tmdbUrl(media) ? (
                    <a href={tmdbUrl(media)!} target="_blank" rel="noopener noreferrer" className="text-gray-200 hover:underline">
                      {media.title}
                    </a>
                  ) : (
                    <span className="text-gray-200">{media.title}</span>
                  )}{" "}
                  ({media.year ?? "?"})
                </span>
                {entry.method && entry.method !== "ignored" && <span className="text-xs text-gray-500">{METHOD_LABELS[entry.method] ?? entry.method}</span>}
                <Ratings media={media} />
                {entry.plex_key && (
                  <Link to={`/item/${entry.plex_key}`} className="text-xs font-semibold text-green-300 hover:underline">
                    In Plex
                  </Link>
                )}
              </>
            ) : (
              <span className={entry.status === "failed" ? "text-red-300" : entry.status === "uncertain" ? "text-yellow-200" : ""}>
                {entry.error ?? (entry.method === "ignored" ? "Left out of lists" : "")}
              </span>
            )}
          </p>
          <EntryMeta entry={entry} showLists={showList} />
        </div>
      </div>
      <div className="flex flex-wrap gap-2 sm:justify-end">
        <EntryActions entry={entry} onFix={onFix} />
      </div>
    </div>
  );
}
