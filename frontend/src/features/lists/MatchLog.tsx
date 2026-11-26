import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useMatches, useOverrides, useProblems, useStatus } from "../../api/queries";
import { Button } from "../../components/Button";
import { Segmented, Tabs } from "../../components/Controls";
import { useDialogs } from "../../components/Dialogs";
import { Pager } from "../../components/Pager";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { EntryLists, EntryRow, KindIcon, KIND_LABELS, POSTER_FALLBACK, SOURCE_LABELS, SourceLink, StatusBadge, type Candidate, type Entry } from "./common";
import { FixDialog } from "./FixDialog";

function Problem({ entry, onFix }: { entry: Entry; onFix: (entry: Entry) => void }) {
  const run = useRunJob();
  const use = (candidate: Candidate) =>
    run(
      () => unwrap(api.POST("/api/lists/entries/{entry_id}/fix", { params: { path: { entry_id: entry.id } }, body: { kind: candidate.kind, tmdb_id: candidate.tmdb_id } })),
      "Saving match…",
    );
  return (
    <Card className="problem p-4" >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start" data-entry={entry.id}>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <KindIcon kind={entry.kind} />
            <span className="font-semibold text-gray-100">{entry.title}</span>
            {entry.year && <span className="text-sm text-gray-400">{entry.year}</span>}
            <StatusBadge entry={entry} />
          </p>
          <p className="mt-0.5 text-sm text-gray-400">{entry.error}</p>
          <EntryLists entry={entry} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {entry.url && <SourceLink url={entry.url} />}
          <Button size="sm" variant="primary" onClick={() => onFix(entry)} data-action="fix">
            Search
          </Button>
          <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/lists/entries/{entry_id}/ignore", { params: { path: { entry_id: entry.id } } })), "Ignoring…")} data-action="ignore">
            Ignore
          </Button>
        </div>
      </div>
      {entry.candidates.length > 0 && (
        <div className="scroll-x -mx-4 mt-4 flex gap-3 overflow-x-auto px-4 pb-1">
          {entry.candidates.map((candidate) => (
            <div key={`${candidate.kind}-${candidate.tmdb_id}`} className="candidate w-28 shrink-0" data-tmdb={candidate.tmdb_id}>
              <img src={candidate.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-full rounded object-cover ring-1 ring-gray-700" />
              <p className="mt-1.5 truncate text-xs font-semibold text-gray-100" title={candidate.title}>
                {candidate.title}
              </p>
              <p className="truncate text-xs text-gray-400">
                {candidate.year ?? "?"}, {KIND_LABELS[candidate.kind]}
              </p>
              <Button size="sm" className="mt-1.5 w-full" onClick={() => use(candidate)}>
                Use
              </Button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function OpenTab({ onFix }: { onFix: (entry: Entry) => void }) {
  const { data, isPending, error } = useProblems();
  if (isPending) return <Loading />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load"}</Notice>;
  if (!data.rows.length) return <Empty title="Nothing to check" />;
  return (
    <div className="space-y-4">
      {data.rows.map((entry) => (
        <Problem key={`${entry.source}-${entry.fingerprint}`} entry={entry} onFix={onFix} />
      ))}
    </div>
  );
}

function AllTab({ onFix }: { onFix: (entry: Entry) => void }) {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const { data, isPending, error } = useMatches({ status, q, page });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setQ(query.trim());
    setPage(1);
  };
  return (
    <>
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Segmented
          label="Status"
          value={status}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
          options={[
            { value: "", label: "All" },
            { value: "matched", label: "Matched" },
            { value: "open", label: "To check" },
            { value: "excluded", label: "Excluded" },
          ]}
        />
        <form onSubmit={submit} className="flex gap-2" role="search">
          <input className="field-input md:w-64" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Title" aria-label="Filter by title" />
          <Button type="submit">Filter</Button>
        </form>
      </div>
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load"}</Notice>
      ) : data.rows.length ? (
        <>
          <Card className="divide-y divide-gray-700">
            {data.rows.map((entry) => (
              <EntryRow key={`${entry.source}-${entry.fingerprint}`} entry={entry} onFix={onFix} showList />
            ))}
          </Card>
          <Pager page={data.page} pages={Math.ceil(data.total / data.per_page)} onPage={setPage} />
        </>
      ) : (
        <Empty title="No matches" />
      )}
    </>
  );
}

function CorrectionsTab() {
  const { data, isPending, error } = useOverrides();
  const run = useRunJob();
  const dialogs = useDialogs();
  if (isPending) return <Loading />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load"}</Notice>;
  if (!data.rows.length) return <Empty title="No corrections" />;
  const remove = async (id: number, title: string) => {
    const ok = await dialogs.confirm({ title: `Remove the correction for ${title}?`, body: "The title is matched automatically again.", confirm: "Remove", danger: true });
    if (ok) await run(() => unwrap(api.DELETE("/api/lists/overrides/{override_id}", { params: { path: { override_id: id } } })), "Removing…");
  };
  return (
    <Card className="divide-y divide-gray-700">
      {data.rows.map((row) => (
        <div key={row.id} className="correction flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center" data-override={row.id}>
          <img src={row.media?.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="hidden aspect-[2/3] w-11 shrink-0 rounded object-cover ring-1 ring-gray-700 sm:block" />
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-x-2">
              <KindIcon kind={row.kind} />
              <span className="font-semibold text-gray-100">{row.title}</span>
              {row.year && <span className="text-sm text-gray-400">{row.year}</span>}
            </p>
            <p className="text-sm text-gray-400">
              {row.ignore ? "Ignored" : row.media ? `${row.media.title} (${row.media.year ?? "?"}), TMDB ${row.media.tmdb_id}` : "Title missing"}
            </p>
            <p className="text-xs text-gray-500">
              {row.source ? SOURCE_LABELS[row.source] ?? row.source : "All sources"}, {timeAgo(row.created_at)}
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={() => remove(row.id, row.title)}>
            Remove
          </Button>
        </div>
      ))}
    </Card>
  );
}

export function MatchLog() {
  useTitle("Match log");
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "open";
  const [fixing, setFixing] = useState<Entry | null>(null);
  const { data: status } = useStatus();
  const tabs = [
    { key: "open", label: "To check", count: status?.counts.match_open },
    { key: "all", label: "All matches" },
    { key: "corrections", label: "Corrections" },
  ];
  return (
    <>
      <PageHeader title="Match log" subtitle="Latest preview of each list." />
      <Tabs
        label="Match log"
        items={tabs.map((entry) => ({ ...entry, active: entry.key === tab, onClick: () => setParams(entry.key === "open" ? {} : { tab: entry.key }) }))}
      />
      <div className="mt-6">
        {tab === "all" ? <AllTab onFix={setFixing} /> : tab === "corrections" ? <CorrectionsTab /> : <OpenTab onFix={setFixing} />}
      </div>
      {fixing && <FixDialog entry={fixing} onClose={() => setFixing(null)} />}
    </>
  );
}
