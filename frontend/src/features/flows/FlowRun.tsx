import { ArrowPathIcon, ChevronRightIcon } from "@heroicons/react/20/solid";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useFlowRun, useFlowRunTitles } from "../../api/queries";
import { Button, ButtonLink } from "../../components/Button";
import { Tabs } from "../../components/Controls";
import { Pager } from "../../components/Pager";
import { Badge, Card, Empty, Loading, Notice, PageHeader, Section } from "../../components/ui";
import { plural, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { KindIcon, POSTER_FALLBACK, Ratings, type Media } from "../lists/common";
import { COLLECTION_TYPE, ModeBadge, NodeIcon, OUTCOME_TONES, OutputFacts, outcomeCount, outcomeLabel, RunStatus, triggerLabel, type FlowRunDetail, type RunNode } from "./common";

type Step = Schemas["TraceStep"];
type Row = Schemas["FlowTitle"];

function stepText(step: Step, dry: boolean): string {
  if (step.category === "source") return step.port ? `${step.label} ${step.detail}`.trim() : `${step.label}: ${step.detail}`;
  if (step.category === "output") return `${step.label}: ${outcomeLabel(step.port ?? "", dry, step.type)}`;
  const detail = step.detail && step.detail !== step.port_label ? ` (${step.detail})` : "";
  return `${step.label}: ${step.port_label}${detail}`;
}

function MediaPills({ media }: { media: Media[] }) {
  return (
    <ul className="mt-1.5 flex flex-wrap gap-1.5">
      {media.map((item) => (
        <li key={item.id} className="rounded-md bg-gray-700/60 px-2 py-0.5 text-xs text-gray-200" data-media={item.id}>
          {item.title}
          {item.year ? ` (${item.year})` : ""}
        </li>
      ))}
    </ul>
  );
}

function OutputCard({ node, dry, onShow }: { node: RunNode; dry: boolean; onShow: (outcome: string) => void }) {
  const compare = node.compare;
  return (
    <Card className="output-card p-4">
      <div className="flex min-w-0 items-center gap-2" data-output={node.id}>
        <NodeIcon type={node.type} category="output" className="h-5 w-5" />
        {node.collection_id ? (
          <h3 className="min-w-0 truncate font-semibold text-gray-100">
            <Link to={`/collections/${node.collection_id}`} className="hover:underline">
              {node.summary}
            </Link>
          </h3>
        ) : (
          <h3 className="font-semibold text-gray-100">{node.label}</h3>
        )}
        {!node.facts.length && !node.collection_id && <span className="truncate text-xs text-gray-400">{node.summary}</span>}
      </div>
      {node.facts.length > 0 && <OutputFacts facts={node.facts} />}
      {node.error && <p className="mt-2 text-sm text-red-300">{node.error}</p>}
      <ul className="mt-3 flex flex-wrap gap-2">
        {node.ports.length ? (
          node.ports.map((port) => (
            <li key={port.key}>
              <button type="button" onClick={() => onShow(port.key)} className="rounded-full" data-outcome={port.key}>
                <Badge tone={OUTCOME_TONES[port.key] ?? "gray"}>
                  {port.count} {outcomeCount(port.key, dry, node.type)}
                </Badge>
              </button>
            </li>
          ))
        ) : (
          <li className="text-sm text-gray-400">No titles reached it</li>
        )}
      </ul>
      {compare && (
        <div className="compare mt-4 border-t border-gray-700 pt-3 text-sm text-gray-300" data-compare={node.id}>
          <p>
            Compared with MDBList{" "}
            {compare.lists.map((list, index) => (
              <span key={list.ref}>
                {index > 0 && ", "}
                {list.run ? (
                  <Link to={`/lists/runs/${list.run}`} title={list.ref} className="text-indigo-300 hover:underline">
                    {list.name ?? list.ref}
                  </Link>
                ) : (
                  <span title={list.ref}>{list.name ?? list.ref}</span>
                )}
                {list.error && <span className="text-red-300"> ({list.error})</span>}
              </span>
            ))}
            : <span className="font-semibold text-gray-100" data-both={compare.both}>{compare.both}</span> in both
          </p>
          {compare.flow_only.length > 0 && (
            <div className="mt-2" data-flow-only={compare.flow_only.length}>
              <p className="text-xs font-semibold text-gray-400">Only in the flow ({compare.flow_only.length})</p>
              <MediaPills media={compare.flow_only} />
            </div>
          )}
          {compare.list_only.length > 0 && (
            <div className="mt-2" data-list-only={compare.list_only.length}>
              <p className="text-xs font-semibold text-gray-400">Only in the list ({compare.list_only.length})</p>
              <MediaPills media={compare.list_only} />
            </div>
          )}
          {!compare.flow_only.length && !compare.list_only.length && <p className="mt-1 text-xs text-green-300">Same titles</p>}
        </div>
      )}
    </Card>
  );
}

function NodeList({ nodes, dry }: { nodes: RunNode[]; dry: boolean }) {
  return (
    <Card className="divide-y divide-gray-700">
      {nodes.map((node) => (
        <div key={node.id} className="run-node flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:gap-4" data-node={node.id}>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <NodeIcon type={node.type} category={node.category} />
            <span className="font-medium text-gray-100">{node.label}</span>
            <span className="truncate text-sm text-gray-400">{node.summary}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-400 sm:justify-end">
            {node.category === "source" ? (
              <>
                {node.error ? (
                  <span className="text-red-300">{node.error}</span>
                ) : (
                  node.entries && (
                    <span>
                      <span className="font-semibold text-gray-100">{node.entries.matched ?? 0}</span> of {node.count_in} matched
                      {(node.entries.uncertain ?? 0) + (node.entries.failed ?? 0) > 0 && `, ${(node.entries.uncertain ?? 0) + (node.entries.failed ?? 0)} unresolved`}
                      {(node.entries.excluded ?? 0) > 0 && `, ${node.entries.excluded} excluded`}
                    </span>
                  )
                )}
                {node.run && (
                  <Link to={`/lists/runs/${node.run}`} className="text-indigo-300 hover:underline">
                    Match log
                  </Link>
                )}
              </>
            ) : (
              <>
                <span>
                  <span className="font-semibold text-gray-100">{node.count_in}</span> in
                </span>
                {node.ports.map((port) => (
                  <span key={port.key}>
                    {node.category === "output" ? outcomeLabel(port.key, dry, node.type) : port.label}{" "}
                    <span className="font-semibold text-gray-100">{port.count}</span>
                  </span>
                ))}
              </>
            )}
          </div>
        </div>
      ))}
    </Card>
  );
}

function TitleRow({ row, dry }: { row: Row; dry: boolean }) {
  const media = row.media;
  const path = row.steps.filter((step) => step.category !== "output");
  return (
    <div className="flow-title flex gap-3 px-4 py-3" data-title={media.title}>
      <img src={media.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="h-[60px] w-10 shrink-0 rounded bg-gray-700 object-cover" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <KindIcon kind={media.kind} />
          <span className="font-semibold text-gray-100">{media.title}</span>
          {media.year && <span className="text-sm text-gray-400">{media.year}</span>}
          <Ratings media={media} />
        </div>
        <p className="mt-1 flex flex-wrap items-center gap-x-1 text-xs text-gray-400">
          {path.map((step, index) => (
            <span key={step.node} className="inline-flex items-center gap-1">
              {index > 0 && <ChevronRightIcon className="h-3.5 w-3.5 text-gray-600" aria-hidden="true" />}
              <span className={step.port === "no_match" || step.port === null ? "text-gray-500" : ""}>{stepText(step, dry)}</span>
            </span>
          ))}
        </p>
        {row.outcomes.length > 0 && (
          <ul className="mt-1.5 space-y-1">
            {row.outcomes.map((step) => (
              <li key={step.node} className="flex flex-wrap items-center gap-2 text-xs text-gray-300" data-outcome={step.port}>
                <Badge tone={OUTCOME_TONES[step.port ?? ""] ?? "gray"}>
                  {step.label}: {outcomeLabel(step.port ?? "", dry, step.type)}
                </Badge>
                {step.detail && <span className="text-gray-400">{step.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// "present" is "Already exists" in Radarr or Sonarr, "In the collection" for a flow with only collections.
const SHOW: { key: string; label: (dry: boolean, collectionsOnly: boolean) => string }[] = [
  { key: "all", label: () => "All" },
  { key: "added", label: (dry) => outcomeLabel("added", dry) },
  { key: "tagged", label: (dry) => outcomeLabel("tagged", dry) },
  { key: "queued", label: (dry) => outcomeLabel("queued", dry) },
  { key: "removed", label: (dry) => outcomeLabel("removed", dry) },
  { key: "present", label: (dry, collectionsOnly) => outcomeLabel("present", dry, collectionsOnly ? COLLECTION_TYPE : undefined) },
  { key: "missing", label: (dry) => outcomeLabel("missing", dry) },
  { key: "skipped", label: () => "Skipped" },
  { key: "failed", label: () => "Failed" },
  { key: "filtered", label: () => "Filtered out" },
];

function Titles({ run, show, setShow }: { run: FlowRunDetail; show: string; setShow: (show: string) => void }) {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const titles = useFlowRunTitles(run.id, { show, q: query, page }, run.status !== "running");
  useEffect(() => setPage(1), [show]);
  const counts = titles.data?.counts ?? {};
  const outputs = run.nodes.filter((node) => node.category === "output");
  const collectionsOnly = outputs.length > 0 && outputs.every((node) => node.type === COLLECTION_TYPE);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setQuery(q);
    setPage(1);
  };
  return (
    <>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <Tabs
          label="Titles"
          items={SHOW.filter((entry) => entry.key === "all" || entry.key === show || counts[entry.key]).map((entry) => ({
            key: entry.key,
            label: entry.label(run.dry_run, collectionsOnly),
            active: entry.key === show,
            count: counts[entry.key],
            onClick: () => {
              setShow(entry.key);
              setPage(1);
            },
          }))}
        />
        <form role="search" onSubmit={submit} className="lg:w-64">
          <input type="search" className="field-input" placeholder="Title" aria-label="Filter by title" value={q} onChange={(event) => setQ(event.target.value)} />
        </form>
      </div>
      <div className="mt-4">
        {titles.isPending ? (
          <Loading />
        ) : titles.error || !titles.data ? (
          <Notice tone="red">{titles.error?.message ?? "Cannot load the titles"}</Notice>
        ) : titles.data.rows.length ? (
          <Card className="divide-y divide-gray-700">
            {titles.data.rows.map((row) => (
              <TitleRow key={row.media.id} row={row} dry={run.dry_run} />
            ))}
          </Card>
        ) : (
          <Empty title="No titles" />
        )}
        {titles.data && <Pager page={titles.data.page} pages={titles.data.pages} onPage={setPage} />}
      </div>
    </>
  );
}

export function FlowRun() {
  const { id = "" } = useParams();
  const runId = Number(id);
  const { data: run, isPending, error } = useFlowRun(runId);
  const [show, setShow] = useState("all");
  const runJob = useRunJob();
  const navigate = useNavigate();
  useTitle(run ? `${run.flow_name}: run` : "Run");

  if (isPending) return <Loading />;
  if (error || !run) return <Notice tone="red">{error?.message ?? "Run not found"}</Notice>;

  const again = async () => {
    const job = await runJob(
      () => unwrap(api.POST("/api/flows/{flow_id}/run", { params: { path: { flow_id: run.flow_id } }, body: { dry_run: run.dry_run } })),
      run.dry_run ? "Dry run…" : "Running…",
    );
    const next = job?.result?.run;
    if (next) navigate(`/flows/runs/${next}`);
  };
  const outputs = run.nodes.filter((node) => node.category === "output");

  return (
    <>
      <PageHeader
        title={run.flow_name}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <ModeBadge dryRun={run.dry_run} />
            <RunStatus run={run} />
            <span>
              {triggerLabel(run.trigger)}, {timeAgo(run.started_at)}
            </span>
            {!run.current && <span className="text-yellow-200">The flow has changed since</span>}
          </span>
        }
        actions={
          <>
            {run.previous && (
              <ButtonLink to={`/flows/runs/${run.previous}`} variant="ghost">
                Previous run
              </ButtonLink>
            )}
            {run.next && (
              <ButtonLink to={`/flows/runs/${run.next}`} variant="ghost">
                Next run
              </ButtonLink>
            )}
            <ButtonLink to={`/flows/${run.flow_id}`}>Edit flow</ButtonLink>
            <Button icon={<ArrowPathIcon className="h-4 w-4" />} onClick={() => void again()} disabled={run.status === "running"} data-action="again">
              {run.dry_run ? "Dry run again" : "Run again"}
            </Button>
          </>
        }
      />
      {run.status === "running" && <Loading text="Running…" />}
      {run.error && <Notice tone="red">{run.error}</Notice>}
      {run.notes.length > 0 && (
        <Notice>
          <ul className="run-notes space-y-1">
            {run.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Notice>
      )}
      {outputs.length > 0 && run.status !== "running" && (
        <div className="mt-6 grid gap-4 xl:grid-cols-2">
          {outputs.map((node) => (
            <OutputCard
              key={node.id}
              node={node}
              dry={run.dry_run}
              onShow={(outcome) => {
                setShow(outcome);
                document.getElementById("run-titles")?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            />
          ))}
        </div>
      )}
      {run.status !== "running" && run.titles > 0 && (
        <Section title={plural(run.titles, "title", "titles")} id="run-titles">
          <Titles run={run} show={show} setShow={setShow} />
        </Section>
      )}
      {run.nodes.length > 0 && run.status !== "running" && (
        <Section title="Nodes" id="run-nodes">
          <NodeList nodes={run.nodes} dry={run.dry_run} />
        </Section>
      )}
    </>
  );
}
