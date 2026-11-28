import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  InboxStackIcon,
  LinkIcon,
  QueueListIcon,
  RectangleStackIcon,
  ShareIcon,
} from "@heroicons/react/24/outline";
import type { ComponentType, ReactNode } from "react";
import { Link } from "react-router";
import { siRadarr, siSonarr } from "simple-icons";
import { api, unwrap, type Schemas } from "../../api/client";
import { jobRunning, useRunJob } from "../../api/jobs";
import { useOverview } from "../../api/queries";
import { BrandIcon } from "../../components/BrandIcon";
import { Button, ButtonLink } from "../../components/Button";
import { Badge, Card, Empty, Loading, Notice, PageHeader, Poster, ProgressBar, Section } from "../../components/ui";
import { fileSize, plural, timeAgo, timeUntil } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { ModeBadge, RunStatus, RunTotals } from "../flows/common";
import { POSTER_FALLBACK } from "../lists/common";

type Overview = Schemas["Overview"];

const JOB_STATES: Record<string, { label: string; tone: "gray" | "indigo" | "green" | "red" }> = {
  queued: { label: "Queued", tone: "gray" },
  running: { label: "Running", tone: "indigo" },
  done: { label: "Done", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  cancelled: { label: "Cancelled", tone: "gray" },
};

const SERVICE_ICONS: Record<string, { path: string; hex: string }> = { Radarr: siRadarr, Sonarr: siSonarr, radarr: siRadarr, sonarr: siSonarr };

const LINK = "text-indigo-300 hover:text-indigo-200";

// -- setup ------------------------------------------------------------------------------------

function Setup({ data }: { data: Overview }) {
  const run = useRunJob();
  const steps = [
    { done: data.steps.plex, title: "Connect Plex", text: "Address and token", to: "/settings/plex" },
    { done: data.steps.libraries, title: "Libraries and paths", text: "Select libraries, map folders if needed", to: "/settings/plex" },
    { done: data.steps.sources, title: "Sources", text: "MediUX, ThePosterDB, optionally TMDB and fanart.tv", to: "/settings/sources" },
    { done: data.steps.designers, title: "Preferred designers", text: "Their sets are used for new titles", to: "/settings/automation" },
  ];
  const ready = data.steps.plex && data.steps.libraries;
  return (
    <>
      <PageHeader title="Set up Programarr" subtitle="Four steps, then the first sync." />
      <ol className="max-w-3xl space-y-3">
        {steps.map((step) => (
          <li key={step.title}>
            <Card className="flex items-center gap-4 p-4">
              <CheckCircleIcon className={`h-7 w-7 shrink-0 ${step.done ? "text-green-400" : "text-gray-600"}`} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-gray-100">
                  {step.title}
                  <span className="sr-only">{step.done ? " (done)" : ""}</span>
                </p>
                <p className="text-sm text-gray-400">{step.text}</p>
              </div>
              <ButtonLink to={step.to} size="sm">
                {step.done ? "Change" : "Set up"}
              </ButtonLink>
            </Card>
          </li>
        ))}
        <li>
          <Card className="flex items-center gap-4 p-4">
            <ArrowPathIcon className="h-7 w-7 shrink-0 text-indigo-400" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-gray-100">First sync</p>
              <p className="text-sm text-gray-400">Titles already in Plex are left as they are.</p>
            </div>
            <Button
              size="sm"
              variant="primary"
              disabled={!ready}
              title={ready ? undefined : "Connect Plex and select libraries first"}
              onClick={() => run(() => unwrap(api.POST("/api/setup/finish")), "First sync with Plex")}
            >
              Finish and sync
            </Button>
          </Card>
        </li>
      </ol>
    </>
  );
}

// -- to do ------------------------------------------------------------------------------------

type Tone = "yellow" | "red" | "indigo";
type Todo = { key: string; to: string; count: number; text: string; tone: Tone; icon: ComponentType<{ className?: string }> };

const TONE_STYLES: Record<Tone, { ring: string; icon: string }> = {
  yellow: { ring: "hover:ring-yellow-500/70", icon: "bg-yellow-500/15 text-yellow-300" },
  red: { ring: "hover:ring-red-500/70", icon: "bg-red-500/15 text-red-300" },
  indigo: { ring: "hover:ring-indigo-500/70", icon: "bg-indigo-500/15 text-indigo-300" },
};

function todos(todo: Overview["todo"]): Todo[] {
  const items: (Todo | false)[] = [
    todo.review > 0 && { key: "review", to: "/review", count: todo.review, text: "to review", tone: "yellow", icon: InboxStackIcon },
    todo.pending > 0 && {
      key: "pending",
      to: "/pending",
      count: todo.pending,
      text: todo.pending === 1 ? "season pending" : "seasons pending",
      tone: "indigo",
      icon: ClockIcon,
    },
    todo.problems > 0 && {
      key: "problems",
      to: "/library?status=problem",
      count: todo.problems,
      text: `${todo.problems === 1 ? "poster" : "posters"} not shown in Plex`,
      tone: "red",
      icon: ExclamationTriangleIcon,
    },
    todo.flows_failed > 0 && {
      key: "flows",
      to: "/flows",
      count: todo.flows_failed,
      text: todo.flows_failed === 1 ? "flow failed" : "flows failed",
      tone: "red",
      icon: ShareIcon,
    },
    todo.match_open > 0 && {
      key: "matches",
      to: "/lists/matches",
      count: todo.match_open,
      text: todo.match_open === 1 ? "unresolved match" : "unresolved matches",
      tone: "yellow",
      icon: QueueListIcon,
    },
    todo.unlinked_with_files > 0 &&
      !todo.detect_open && {
        key: "unlinked",
        to: "/match",
        count: todo.unlinked_with_files,
        text: `${todo.unlinked_with_files === 1 ? "show" : "shows"} with posters but no set`,
        tone: "indigo",
        icon: LinkIcon,
      },
  ];
  return items.filter((item): item is Todo => !!item);
}

function TodoTile({ item }: { item: Todo }) {
  const styles = TONE_STYLES[item.tone];
  const Icon = item.icon;
  return (
    <Link to={item.to} className={`flex items-center gap-4 rounded-lg bg-gray-800 p-4 ring-1 ring-gray-700 transition ${styles.ring}`} data-todo={item.key}>
      <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${styles.icon}`}>
        <Icon className="h-6 w-6" aria-hidden="true" />
      </span>
      <span className="min-w-0">
        <span className="block text-2xl leading-7 font-bold text-gray-100 tabular-nums">{item.count}</span>
        <span className="block truncate text-sm text-gray-400">{item.text}</span>
      </span>
    </Link>
  );
}

// -- posters ----------------------------------------------------------------------------------

function RecentStrip({ cards }: { cards: Schemas["RecentCard"][] }) {
  if (!cards.length) {
    return (
      <Empty title="No posters set yet" action={<ButtonLink to="/library">Open the library</ButtonLink>}>
        Select a set for a title, or link existing posters under Match existing.
      </Empty>
    );
  }
  return (
    <div className="scroll-x -mx-4 flex gap-4 overflow-x-auto px-4 pb-3 md:-mx-8 md:px-8">
      {cards.map((card) => (
        <Link key={`${card.item_key}-${card.at}`} to={`/item/${card.item_key}`} className="recent-card group w-36 shrink-0 sm:w-40">
          <Poster src={card.thumb} className="transition duration-300 group-hover:ring-gray-500" />
          <p className="mt-2 truncate text-sm font-semibold text-gray-100" title={card.title}>
            {card.title}
          </p>
          <p className="truncate text-xs text-gray-400">{card.what ?? [card.source_label, card.designer].filter(Boolean).join(", ")}</p>
          <p className="truncate text-xs text-gray-500">
            {card.origin_label}, {timeAgo(card.at)}
          </p>
        </Link>
      ))}
    </div>
  );
}

// -- flows ------------------------------------------------------------------------------------

function CompareLine({ flow }: { flow: Schemas["FlowCard"] }) {
  const compare = flow.compare;
  const last = flow.last_run;
  if (!compare || !last) return null;
  const names = compare.lists.map((list) => list.name || list.ref).join(", ");
  const failed = compare.lists.some((list) => list.error);
  const parts = [`${compare.both} in both`];
  if (compare.flow_only) parts.push(`${compare.flow_only} only in the flow`);
  if (compare.list_only) parts.push(`${compare.list_only} only in the list`);
  return (
    <Link to={`/flows/runs/${last.id}`} className="block truncate text-xs text-gray-400 hover:text-gray-200" data-compare={flow.id}>
      <span className="text-gray-300">MDBList {names}:</span> {failed ? "could not be read" : parts.join(", ")}
    </Link>
  );
}

function FlowCard({ flow }: { flow: Schemas["FlowCard"] }) {
  const last = flow.last_run;
  return (
    <Card className="flow-card flex flex-col gap-3 p-4">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 flex shrink-0 gap-1">
          {flow.outputs.length ? (
            flow.outputs.map((output) =>
              SERVICE_ICONS[output] ? (
                <BrandIcon key={output} icon={SERVICE_ICONS[output]} className="h-5 w-5" />
              ) : output === "Plex collection" ? (
                <RectangleStackIcon key={output} className="h-5 w-5 text-amber-400" aria-label="Plex collection" />
              ) : null,
            )
          ) : (
            <ShareIcon className="h-5 w-5 text-gray-500" aria-hidden="true" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/flows/${flow.id}`} className="truncate font-semibold text-gray-100 hover:text-white hover:underline">
              {flow.name}
            </Link>
            <ModeBadge dryRun={flow.dry_run} />
            {flow.schedule && !flow.enabled && <Badge tone="gray">Off</Badge>}
            {flow.problems.length > 0 && (
              <Badge tone="red" title={flow.problems.map((problem) => problem.message).join("\n")}>
                {plural(flow.problems.length, "problem", "problems")}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-gray-400">
            {flow.schedule_text}
            {flow.next_run_at ? `, next ${timeUntil(flow.next_run_at)}` : ""}
          </p>
        </div>
      </div>
      <div className="mt-auto space-y-1.5 border-t border-gray-700 pt-3">
        {last ? (
          <>
            <Link to={`/flows/runs/${last.id}`} className="flex flex-wrap items-center gap-2 text-xs text-gray-400 hover:text-gray-200">
              <RunStatus run={last} />
              <span>{timeAgo(last.started_at)}</span>
            </Link>
            <RunTotals run={last} />
            <CompareLine flow={flow} />
          </>
        ) : (
          <p className="text-xs text-gray-500">Never run</p>
        )}
      </div>
    </Card>
  );
}

function FlowAdds({ adds }: { adds: Schemas["FlowAdd"][] }) {
  return (
    <div className="scroll-x -mx-4 flex gap-4 overflow-x-auto px-4 pb-3 md:-mx-8 md:px-8">
      {adds.map((add) => (
        <Link
          key={`${add.media.id}-${add.run_id}`}
          to={add.plex_key ? `/item/${add.plex_key}` : `/flows/runs/${add.run_id}`}
          className="flow-add group w-32 shrink-0 sm:w-36"
          data-media={add.media.id}
        >
          <Poster src={add.media.poster ?? POSTER_FALLBACK} className="transition duration-300 group-hover:ring-gray-500" />
          <p className="mt-2 truncate text-sm font-semibold text-gray-100" title={add.media.title}>
            {add.media.title}
          </p>
          <p className="flex items-center gap-1 truncate text-xs text-gray-400">
            <BrandIcon icon={SERVICE_ICONS[add.service]} className="h-3.5 w-3.5" />
            <span className="truncate">{add.flow_name}</span>
          </p>
          <p className="truncate text-xs text-gray-500">
            {add.plex_key ? <span className="text-green-300">In Plex</span> : add.media.year ?? ""}
            {add.plex_key || add.media.year ? ", " : ""}
            {timeAgo(add.at)}
          </p>
        </Link>
      ))}
    </div>
  );
}

// -- library and schedule ---------------------------------------------------------------------

function FactRow({ label, value, to }: { label: string; value: ReactNode; to?: string }) {
  const body = (
    <>
      <dt className="text-gray-400">{label}</dt>
      <dd className="font-semibold text-gray-100 tabular-nums">{value}</dd>
    </>
  );
  return to ? (
    <Link to={to} className="flex items-baseline justify-between gap-4 px-4 py-2.5 text-sm hover:bg-gray-700/40" data-fact={label}>
      {body}
    </Link>
  ) : (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2.5 text-sm" data-fact={label}>
      {body}
    </div>
  );
}

function LibraryCard({ facts }: { facts: Overview["facts"] }) {
  const backup = facts.backup_files === null ? "Off" : `${plural(facts.backup_files, "file", "files")}, ${fileSize(facts.backup_bytes ?? 0)}`;
  return (
    <Card className="library-facts">
      <dl className="divide-y divide-gray-700/70">
        <FactRow label="Movies" value={facts.movies} to="/library?type=movie" />
        <FactRow label="Shows" value={facts.shows} to="/library?type=show" />
        <FactRow label="Linked to a set" value={facts.linked} to="/library?status=linked" />
        <FactRow label="Complete shows" value={facts.complete} to="/library?status=complete" />
        <FactRow label="Poster files" value={facts.files} />
        <FactRow label="Backup" value={backup} to="/settings/files" />
        <FactRow label="Exclusions" value={facts.exclusions} to="/lists/exclusions" />
        <FactRow label="Collections" value={facts.collections} to="/collections" />
        {facts.queued > 0 && <FactRow label="Queued for collections" value={facts.queued} to="/collections/queue" />}
      </dl>
    </Card>
  );
}

function every(seconds: number): string {
  if (seconds < 3600) return `Every ${Math.round(seconds / 60)} minutes`;
  const hours = Math.round(seconds / 3600);
  if (hours === 1) return "Every hour";
  if (hours > 24 && hours % 24 === 0) return `Every ${hours / 24} days`;
  return `Every ${hours} hours`;
}

function ScheduleCard({ rows }: { rows: Overview["schedule"] }) {
  return (
    <Card className="schedule">
      <ul className="divide-y divide-gray-700/70">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm" data-schedule={row.key}>
            <div className="min-w-0">
              <p className="text-gray-100">{row.label}</p>
              <p className="text-xs text-gray-400">
                {row.state === "off" ? "Off" : row.state === "paused" ? "Paused, automation is off" : every(row.interval ?? 0)}
              </p>
            </div>
            <div className="shrink-0 text-right text-xs">
              <p className="text-gray-300">{row.last ? `Last ${timeAgo(row.last)}` : "Never run"}</p>
              {row.next && <p className="text-gray-500">Next {timeUntil(row.next)}</p>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Jobs({ jobs }: { jobs: Schemas["Job"][] }) {
  return (
    <Card className="divide-y divide-gray-700">
      {jobs.map((job) => {
        const state = JOB_STATES[job.status];
        return (
          <div key={job.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-gray-100">{job.title}</p>
              {job.message && (job.status === "failed" || job.status === "running") && <p className="truncate text-xs text-gray-400">{job.message}</p>}
              {jobRunning(job) && job.total > 0 && (
                <div className="mt-2 max-w-sm">
                  <ProgressBar done={job.done} total={job.total} />
                </div>
              )}
            </div>
            <div className="flex items-center gap-3 text-xs text-gray-400">
              <span>{timeAgo(job.finished_at ?? job.started_at ?? job.created_at)}</span>
              <Badge tone={state.tone}>{state.label}</Badge>
            </div>
          </div>
        );
      })}
    </Card>
  );
}

// -- page -------------------------------------------------------------------------------------

export function Dashboard() {
  useTitle("Dashboard");
  const { data, isPending, error } = useOverview();
  const run = useRunJob();
  if (isPending) return <Loading />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load the dashboard"}</Notice>;
  if (!data.setup_done) return <Setup data={data} />;

  const items = todos(data.todo);
  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={`Synced ${timeAgo(data.last_sync)}`}
        actions={
          <>
            <Button variant="primary" icon={<ArrowPathIcon className="h-4 w-4" />} onClick={() => run(() => unwrap(api.POST("/api/sync")), "Sync with Plex")}>
              Sync now
            </Button>
            <Button onClick={() => run(() => unwrap(api.POST("/api/setcheck")), "Check linked sets")}>Check sets</Button>
          </>
        }
      />
      {!data.automation && (
        <Notice>
          Automation is off.{" "}
          <Link to="/settings/automation" className="font-semibold underline underline-offset-2">
            Turn on
          </Link>
        </Notice>
      )}

      <Section title="To do" id="todo">
        {items.length ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" data-testid="todo">
            {items.map((item) => (
              <TodoTile key={item.key} item={item} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">Nothing to do.</p>
        )}
      </Section>

      <Section title="Recently set" id="recent" aside={<Link to="/history" className={LINK}>History</Link>}>
        <RecentStrip cards={data.recent} />
      </Section>

      <Section title="Flows" id="flows" aside={<Link to="/flows" className={LINK}>All flows</Link>}>
        {data.flows.length ? (
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {data.flows.map((flow) => (
              <FlowCard key={flow.id} flow={flow} />
            ))}
          </div>
        ) : (
          <Empty title="No flows yet" action={<ButtonLink to="/flows">Open flows</ButtonLink>} />
        )}
      </Section>

      {data.flow_adds.length > 0 && (
        <Section title="Added by flows" id="flow-adds" aside={<Link to="/flows/history" className={LINK}>Flow history</Link>}>
          <FlowAdds adds={data.flow_adds} />
        </Section>
      )}

      <div className="grid gap-x-6 lg:grid-cols-2">
        <Section title="Library" id="library" aside={<Link to="/library" className={LINK}>All titles</Link>}>
          <LibraryCard facts={data.facts} />
        </Section>
        <Section title="Schedule" id="schedule" aside={<Link to="/settings/automation" className={LINK}>Settings</Link>}>
          <ScheduleCard rows={data.schedule} />
        </Section>
      </div>

      {data.jobs.length > 0 && (
        <Section title="Jobs" id="jobs">
          <Jobs jobs={data.jobs} />
        </Section>
      )}
    </>
  );
}
