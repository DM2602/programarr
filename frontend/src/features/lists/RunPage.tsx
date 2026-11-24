import { ArrowPathIcon } from "@heroicons/react/24/outline";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useRun } from "../../api/queries";
import { Button, ButtonLink } from "../../components/Button";
import { Tabs } from "../../components/Controls";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { EntryRow, isOpen, sectionGroups, SOURCE_LABELS, type Entry } from "./common";
import { FixDialog } from "./FixDialog";
import { RunCounts } from "./Lists";

const FILTERS: { key: string; label: string; test: (entry: Entry) => boolean }[] = [
  { key: "all", label: "All", test: () => true },
  { key: "open", label: "To check", test: isOpen },
  { key: "matched", label: "Matched", test: (entry) => entry.status === "matched" },
  { key: "excluded", label: "Excluded", test: (entry) => entry.status === "excluded" },
];

export function RunPage() {
  const { id = "" } = useParams();
  const runId = Number(id);
  const { data, isPending, error } = useRun(runId);
  const [filter, setFilter] = useState("all");
  const [fixing, setFixing] = useState<Entry | null>(null);
  const run = useRunJob();
  const navigate = useNavigate();
  useTitle(data?.label ?? "Preview");

  if (isPending) return <Loading />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Preview not found"}</Notice>;

  const again = async () => {
    const job = await run(() => unwrap(api.POST("/api/lists/runs/{run_id}/again", { params: { path: { run_id: runId } } })), "Preview…");
    const next = job?.result?.run;
    if (next) navigate(`/lists/runs/${next}`);
  };
  const current = FILTERS.find((entry) => entry.key === filter) ?? FILTERS[0];
  const entries = data.entries.filter(current.test);

  return (
    <>
      <PageHeader
        title={data.label}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              {SOURCE_LABELS[data.source] ?? data.source}, {timeAgo(data.started_at)}
            </span>
            <RunCounts run={data} />
          </span>
        }
        actions={
          <>
            {data.previous && (
              <ButtonLink to={`/lists/runs/${data.previous}`} variant="ghost">
                Previous run
              </ButtonLink>
            )}
            <Button variant="primary" icon={<ArrowPathIcon className="h-4 w-4" />} onClick={again} disabled={data.status === "running"} data-action="again">
              Run again
            </Button>
          </>
        }
      />
      {data.status === "error" && data.error && <Notice tone="red">{data.error}</Notice>}
      {data.notes.length > 0 && (
        <div className="space-y-2">
          {data.notes.map((note) => (
            <Notice key={note}>{note}</Notice>
          ))}
        </div>
      )}
      {data.entries.length > 0 && (
        <>
          <Tabs
            label="Filter"
            items={FILTERS.map((entry) => ({
              key: entry.key,
              label: entry.label,
              active: entry.key === filter,
              onClick: () => setFilter(entry.key),
              count: entry.key === "all" ? undefined : data.entries.filter(entry.test).length,
            }))}
          />
          <div className="mt-4 space-y-6">
            {entries.length ? (
              sectionGroups(entries).map((group) => (
                <section key={group.key} aria-label={group.label ?? undefined} data-section={group.key || undefined}>
                  {group.label && (
                    <h2 className="mb-2 flex items-center gap-2 text-lg font-semibold text-gray-100">
                      {group.icon}
                      {group.label}
                      <span className="text-sm font-normal text-gray-500">{group.entries.length}</span>
                    </h2>
                  )}
                  <Card className="divide-y divide-gray-700">
                    {group.entries.map((entry) => (
                      <EntryRow key={entry.id} entry={entry} onFix={setFixing} grouped={!!group.label} />
                    ))}
                  </Card>
                </section>
              ))
            ) : (
              <Empty title="Nothing here" />
            )}
          </div>
        </>
      )}
      {data.status === "ok" && data.entries.length === 0 && <Empty title="The list is empty" />}
      {fixing && <FixDialog entry={fixing} onClose={() => setFixing(null)} />}
    </>
  );
}
