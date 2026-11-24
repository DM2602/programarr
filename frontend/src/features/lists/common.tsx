import { ArrowDownIcon, ArrowUpIcon, MinusIcon } from "@heroicons/react/16/solid";
import { CalendarDaysIcon, ChartBarIcon, FilmIcon, SparklesIcon, Squares2X2Icon, TvIcon, UserGroupIcon } from "@heroicons/react/20/solid";
import { ArrowTopRightOnSquareIcon } from "@heroicons/react/24/outline";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { siImdb, siRottentomatoes } from "simple-icons";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { BrandIcon } from "../../components/BrandIcon";
import { Button } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { Popover } from "../../components/Popover";
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

export const KIND_LABELS: Record<string, string> = { movie: "Movie", show: "Show" };

export const SECTION_LABELS: Record<string, string> = {
  movies: "Movies",
  shows: "TV Shows",
  overall: "Overall",
  "kids-movies": "Kids Movies",
  "kids-shows": "Kids TV Shows",
};

const RULES: Record<string, string> = {
  exact: "Same title and year",
  "year±1": "Same title, year off by one",
  title: "Same title, no year to compare",
  "alt-title": "Matches an alternative title on TMDB",
  id: "Matched by ID",
  fixed: "Set by you",
};

export const POSTER_FALLBACK = "/img/tmdb/none";

export function isOpen(entry: Pick<Entry, "status">): boolean {
  return entry.status === "uncertain" || entry.status === "failed";
}

/** Title reduced to letters and digits, like the matcher on the server. */
export function matchKey(text: string | null | undefined): string {
  return (text ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/ß/g, "ss")
    .replace(/&/g, " and ")
    .replace(/[^\p{L}\p{N}]+/gu, "");
}

export function StatusBadge({ entry }: { entry: Entry }) {
  if (entry.status === "uncertain") return <Badge tone="yellow">To check</Badge>;
  if (entry.status === "failed") return <Badge tone="red">{entry.method === "error" ? "Error" : "Not found"}</Badge>;
  if (entry.status === "excluded") return <Badge tone="gray">{entry.method === "ignored" ? "Ignored" : "Excluded"}</Badge>;
  return null;
}

export function KindIcon({ kind, className = "h-4 w-4" }: { kind: string | null | undefined; className?: string }) {
  if (kind !== "movie" && kind !== "show") return null;
  const Icon = kind === "show" ? TvIcon : FilmIcon;
  return (
    <span title={KIND_LABELS[kind]} className="inline-flex text-gray-500">
      <Icon className={className} aria-hidden="true" />
      <span className="sr-only">{KIND_LABELS[kind]}</span>
    </span>
  );
}

export function Ratings({ media }: { media: Media }) {
  const parts: { key: string; icon: ReactNode; value: string; title: string }[] = [];
  if (media.imdb_rating) {
    const votes = media.imdb_votes ? `, ${media.imdb_votes.toLocaleString("en-US")} votes` : "";
    parts.push({ key: "imdb", icon: <BrandIcon icon={siImdb} />, value: media.imdb_rating.toFixed(1), title: `IMDb ${media.imdb_rating.toFixed(1)}${votes}` });
  }
  if (media.rt_critics !== null && media.rt_critics !== undefined) {
    parts.push({ key: "rt", icon: <BrandIcon icon={siRottentomatoes} />, value: `${media.rt_critics}%`, title: `Rotten Tomatoes critics ${media.rt_critics}%` });
  }
  if (media.rt_audience !== null && media.rt_audience !== undefined) {
    parts.push({ key: "audience", icon: <UserGroupIcon className="h-4 w-4 text-gray-400" aria-hidden="true" />, value: `${media.rt_audience}%`, title: `Rotten Tomatoes audience ${media.rt_audience}%` });
  }
  if (!parts.length) return null;
  return (
    <span className="ratings inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      {parts.map((part) => (
        <span key={part.key} className="inline-flex items-center gap-1" title={part.title} data-rating={part.key}>
          {part.icon}
          <span className="font-semibold text-gray-200 tabular-nums">{part.value}</span>
          <span className="sr-only">{part.title}</span>
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

function extraOf(entry: Entry): Record<string, unknown> {
  return (entry.extra ?? {}) as Record<string, unknown>;
}

function daysOf(extra: Record<string, unknown>): number | null {
  if (typeof extra.days === "number") return extra.days;
  const old = typeof extra.stat === "string" ? /^(\d+) d$/.exec(extra.stat) : null; // runs before 24 Nov 2026
  return old ? Number(old[1]) : null;
}

function Chip({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-gray-700/60 px-1.5 py-0.5 text-xs text-gray-300" title={title}>
      {icon}
      {children}
    </span>
  );
}

/** Days in the TOP 10, FlixPatrol points and originals, as the source shows them. */
export function SourceStats({ entry }: { entry: Entry }) {
  const extra = extraOf(entry);
  const days = daysOf(extra);
  const points = typeof extra.points === "number" ? extra.points : null;
  if (days === null && points === null && !extra.original) return null;
  return (
    <span className="source-stats inline-flex flex-wrap gap-1.5">
      {days !== null && (
        <Chip icon={<CalendarDaysIcon className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />} title="Days in the TOP 10 so far">
          {days} {days === 1 ? "day" : "days"} in TOP 10
        </Chip>
      )}
      {points !== null && (
        <Chip icon={<ChartBarIcon className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />} title="FlixPatrol points">
          {points.toLocaleString("en-US")} points
        </Chip>
      )}
      {extra.original === true && (
        <Chip icon={<SparklesIcon className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />} title="Original of the platform">
          Original
        </Chip>
      )}
    </span>
  );
}

/** Rank and the move since the day before (FlixPatrol country lists). */
export function Rank({ value, change }: { value: number | null | undefined; change?: unknown }) {
  let move: ReactNode = null;
  if (change === "new") move = <span className="text-[10px] font-bold text-indigo-300 uppercase">New</span>;
  else if (typeof change === "number" && change > 0)
    move = (
      <span className="inline-flex items-center text-xs font-semibold text-green-400" title={`Up ${change}`}>
        <ArrowUpIcon className="h-3 w-3" aria-hidden="true" />
        {change}
      </span>
    );
  else if (typeof change === "number" && change < 0)
    move = (
      <span className="inline-flex items-center text-xs font-semibold text-red-400" title={`Down ${-change}`}>
        <ArrowDownIcon className="h-3 w-3" aria-hidden="true" />
        {-change}
      </span>
    );
  else if (change === 0) move = <MinusIcon className="h-3 w-3 text-gray-600" aria-label="Same place" />;
  return (
    <div className="flex w-7 shrink-0 flex-col items-center gap-0.5 pt-1">
      <span className="text-lg leading-none font-bold text-gray-300 tabular-nums">{value ?? ""}</span>
      {move}
    </div>
  );
}

export function SourceLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="source-link inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-gray-400 ring-1 ring-gray-700 transition hover:text-white hover:ring-gray-500"
      title={`Open on ${linkLabel(url)}`}
    >
      {linkLabel(url)}
      <ArrowTopRightOnSquareIcon className="h-3.5 w-3.5" aria-hidden="true" />
    </a>
  );
}

// -- match score ---------------------------------------------------------------------------------

export function scoreOf(entry: Entry): number | null {
  if (entry.method === "ignored" || entry.method === "error") return null;
  if (entry.status === "failed") return 0;
  if (entry.score === null || entry.score === undefined) return null;
  return Math.round(entry.score * 100);
}

function scoreTone(score: number): string {
  if (score >= 100) return "bg-green-500/15 text-green-300 ring-green-500/40";
  if (score >= 80) return "bg-yellow-500/15 text-yellow-200 ring-yellow-500/40";
  return "bg-red-500/15 text-red-300 ring-red-500/40";
}

function ruleOf(entry: Entry): string {
  if (entry.status === "failed") return entry.error ?? "Not found on TMDB";
  if (entry.status === "uncertain") return entry.error === "Several titles fit" ? "Several titles fit" : "Only similar titles on TMDB";
  return RULES[entry.method ?? ""] ?? entry.method ?? "";
}

function useUse(entry: Entry) {
  const run = useRunJob();
  return (candidate: Candidate) =>
    run(
      () => unwrap(api.POST("/api/lists/entries/{entry_id}/fix", { params: { path: { entry_id: entry.id } }, body: { kind: candidate.kind, tmdb_id: candidate.tmdb_id } })),
      "Saving match…",
    );
}

function Compare({ label, left, right }: { label: string; left: ReactNode; right: ReactNode }) {
  const differs = right !== null && right !== undefined && String(left ?? "") !== String(right);
  return (
    <tr>
      <th scope="row" className="py-1 pr-3 text-left text-xs font-semibold text-gray-500">
        {label}
      </th>
      <td className="py-1 pr-3 text-gray-200">{left ?? "?"}</td>
      <td className={`py-1 ${differs ? "font-semibold text-yellow-200" : "text-gray-200"}`}>{right ?? (right === null ? "?" : "")}</td>
    </tr>
  );
}

function MatchDetails({ entry, onFix, close }: { entry: Entry; onFix: (entry: Entry) => void; close: () => void }) {
  const use = useUse(entry);
  const run = useRunJob();
  const media = entry.media;
  const score = scoreOf(entry);
  const source = entry.url ? linkLabel(entry.url) : entry.source ? SOURCE_LABELS[entry.source] ?? entry.source : "List";
  const ids = entry.ids as Record<string, unknown>;
  const others = entry.candidates.filter((candidate) => !media || candidate.tmdb_id !== media.tmdb_id || candidate.kind !== media.kind).slice(0, 4);
  return (
    <div className="match-details">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-white">{ruleOf(entry)}</p>
          {ids && Object.keys(ids).length > 0 && entry.method === "id" && (
            <p className="text-xs text-gray-400">{Object.entries(ids).map(([name, value]) => `${name.toUpperCase()} ${value}`).join(", ")}</p>
          )}
        </div>
        {score !== null && <span className={`rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ring-1 ring-inset ${scoreTone(score)}`}>{score}%</span>}
      </div>
      {media ? (
        <table className="mt-3 w-full table-fixed text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-500">
              <td className="w-12" />
              <th scope="col" className="pb-1 font-semibold">{source}</th>
              <th scope="col" className="pb-1 font-semibold">TMDB</th>
            </tr>
          </thead>
          <tbody>
            <Compare label="Title" left={entry.title} right={media.title} />
            <Compare label="Year" left={entry.year} right={media.year ?? null} />
            <Compare label="Type" left={entry.kind ? KIND_LABELS[entry.kind] : null} right={KIND_LABELS[media.kind]} />
          </tbody>
        </table>
      ) : (
        <p className="mt-2 text-gray-300">
          <span className="text-gray-500">{source}:</span> {entry.title}
          {entry.year ? `, ${entry.year}` : ""}
          {entry.kind ? `, ${KIND_LABELS[entry.kind]}` : ""}
        </p>
      )}
      {media && (
        <p className="mt-2 flex flex-wrap gap-x-3 text-xs">
          {tmdbUrl(media) && (
            <a href={tmdbUrl(media)!} target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-white hover:underline">
              TMDB {media.tmdb_id}
            </a>
          )}
          {media.imdb_id && (
            <a href={`https://www.imdb.com/title/${media.imdb_id}/`} target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-white hover:underline">
              IMDb {media.imdb_id}
            </a>
          )}
          {media.original_title && media.original_title !== media.title && <span className="text-gray-500">Original title {media.original_title}</span>}
        </p>
      )}
      {others.length > 0 && (
        <div className="mt-3 border-t border-gray-700 pt-3">
          <p className="mb-1 text-xs font-semibold text-gray-500">{media ? "Other titles" : "Candidates"}</p>
          <ul className="space-y-2">
            {others.map((candidate) => (
              <li key={`${candidate.kind}-${candidate.tmdb_id}`} className="flex items-center gap-2.5">
                <img src={candidate.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-8 shrink-0 rounded object-cover ring-1 ring-gray-700" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-gray-200" title={candidate.title}>
                    {candidate.title}
                  </span>
                  <span className="block truncate text-xs text-gray-500">
                    {candidate.year ?? "?"}, {KIND_LABELS[candidate.kind]},{" "}
                    <a href={tmdbUrl(candidate)!} target="_blank" rel="noopener noreferrer" className="hover:text-white hover:underline">
                      TMDB {candidate.tmdb_id}
                    </a>
                  </span>
                </span>
                {candidate.similarity !== null && candidate.similarity !== undefined && (
                  <span className="text-xs text-gray-500 tabular-nums" title="Title similarity">
                    {Math.round(candidate.similarity * 100)}%
                  </span>
                )}
                <Button size="sm" variant="ghost" onClick={async () => { close(); await use(candidate); }} data-tmdb={candidate.tmdb_id}>
                  Use
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2 border-t border-gray-700 pt-3">
        {entry.method === "fixed" && (
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              close();
              await run(() => unwrap(api.POST("/api/lists/entries/{entry_id}/reset", { params: { path: { entry_id: entry.id } } })), "Matching again…");
            }}
            data-action="reset"
          >
            Back to automatic
          </Button>
        )}
        <Button size="sm" onClick={() => { close(); onFix(entry); }}>
          {media ? "Change match" : "Search TMDB"}
        </Button>
      </div>
    </div>
  );
}

/** How sure the match is; a click shows what was compared. */
export function ScorePill({ entry, onFix }: { entry: Entry; onFix: (entry: Entry) => void }) {
  const score = scoreOf(entry);
  if (score === null) return null;
  return (
    <Popover
      label={`Match details for ${entry.title}`}
      title="Match details"
      buttonClass={`score inline-flex min-w-14 justify-center rounded-full px-2.5 py-1 text-xs font-semibold tabular-nums ring-1 ring-inset transition hover:brightness-125 ${scoreTone(score)}`}
      button={`${score}%`}
      data={{ "data-score": String(score) }}
    >
      {(close) => <MatchDetails entry={entry} onFix={onFix} close={close} />}
    </Popover>
  );
}

// -- actions -------------------------------------------------------------------------------------

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
      <Button size="sm" variant="ghost" onClick={exclude} data-action="exclude">
        Exclude
      </Button>
    </>
  );
}

/** The lists a grouped entry appears in (match log). */
export function EntryLists({ entry }: { entry: Entry }) {
  if (!entry.lists?.length) return null;
  return (
    <p className="truncate text-xs text-gray-500">
      {entry.lists.map((list, index) => (
        <span key={list.run_id}>
          {index > 0 && ", "}
          <Link to={`/lists/runs/${list.run_id}`} className="hover:text-gray-300 hover:underline">
            {list.label}
          </Link>
          {list.position ? ` #${list.position}` : ""}
        </span>
      ))}
    </p>
  );
}

/** One list entry: what was found on TMDB (or what the list says, when nothing was). */
export function EntryRow({ entry, onFix, showList = false, grouped = false }: {
  entry: Entry;
  onFix: (entry: Entry) => void;
  showList?: boolean;
  grouped?: boolean;
}) {
  const media = entry.media;
  const extra = extraOf(entry);
  const dim = entry.status === "excluded";
  const title = media?.title ?? entry.title;
  const year = media ? media.year : entry.year;
  const source = entry.url ? linkLabel(entry.url) : entry.source ? SOURCE_LABELS[entry.source] ?? entry.source : "List";
  const renamed = media && matchKey(media.title) !== matchKey(entry.title);
  const rank = grouped && typeof extra.rank === "number" ? extra.rank : entry.position;
  return (
    <div className="entry flex gap-3 px-4 py-3" data-entry={entry.id} data-status={entry.status}>
      {!showList && <Rank value={rank} change={grouped ? extra.change : undefined} />}
      <img
        src={media?.poster ?? entry.candidates[0]?.poster ?? POSTER_FALLBACK}
        alt=""
        loading="lazy"
        className={`aspect-[2/3] w-12 shrink-0 self-start rounded object-cover ring-1 ring-gray-700 ${media ? "" : "opacity-40 grayscale"} ${dim ? "opacity-50" : ""}`}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-2 lg:flex-row lg:items-center lg:gap-4">
        <div className={`min-w-0 flex-1 space-y-1 ${dim ? "opacity-60" : ""}`}>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {media && tmdbUrl(media) ? (
              <a href={tmdbUrl(media)!} target="_blank" rel="noopener noreferrer" className="truncate font-semibold text-gray-100 hover:underline" title="Open on TMDB">
                {title}
              </a>
            ) : (
              <span className="truncate font-semibold text-gray-100">{title}</span>
            )}
            {year && <span className="text-sm text-gray-400">{year}</span>}
            {!grouped && <KindIcon kind={media?.kind ?? entry.kind} />}
            <StatusBadge entry={entry} />
            {entry.plex_key && (
              <Link to={`/item/${entry.plex_key}`} className="rounded-full hover:opacity-90" title="Open in Programarr">
                <Badge tone="green">In Plex</Badge>
              </Link>
            )}
          </p>
          {renamed && (
            <p className="text-xs text-gray-500">
              {source}: {entry.title}
            </p>
          )}
          {!media && entry.error && (
            <p className={`text-sm ${entry.status === "failed" ? "text-red-300" : "text-yellow-200"}`}>{entry.error}</p>
          )}
          {(media || Object.keys(extra).length > 0) && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
              {media && <Ratings media={media} />}
              <SourceStats entry={entry} />
            </div>
          )}
          {showList && <EntryLists entry={entry} />}
        </div>
        <div className="flex flex-wrap items-center gap-2 lg:flex-nowrap">
          <div className="flex lg:w-16 lg:justify-center">
            <ScorePill entry={entry} onFix={onFix} />
          </div>
          <div className="flex lg:w-28">{entry.url && <SourceLink url={entry.url} />}</div>
          <div className="flex gap-2 lg:w-40 lg:justify-end">
            <EntryActions entry={entry} onFix={onFix} />
          </div>
        </div>
      </div>
    </div>
  );
}

/** Entries by FlixPatrol section (movies, shows, ...) when the source has sections; otherwise one group. */
export function sectionGroups(entries: Entry[]): { key: string; label: string | null; icon: ReactNode; entries: Entry[] }[] {
  const groups: { key: string; label: string | null; icon: ReactNode; entries: Entry[] }[] = [];
  for (const entry of entries) {
    const section = extraOf(entry).section;
    const key = typeof section === "string" ? section : "";
    let group = groups.find((each) => each.key === key);
    if (!group) {
      const Icon = key.endsWith("shows") ? TvIcon : key === "overall" ? Squares2X2Icon : FilmIcon;
      group = { key, label: key ? SECTION_LABELS[key] ?? key : null, icon: key ? <Icon className="h-5 w-5 text-gray-400" aria-hidden="true" /> : null, entries: [] };
      groups.push(group);
    }
    group.entries.push(entry);
  }
  return groups;
}
