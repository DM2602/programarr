import { ArrowPathIcon, CheckCircleIcon } from "@heroicons/react/24/outline";
import { Link } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { jobRunning, useRunJob } from "../../api/jobs";
import { useOverview } from "../../api/queries";
import { Button, ButtonLink } from "../../components/Button";
import { Badge, Card, Empty, Loading, Notice, PageHeader, Poster, ProgressBar, Section } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";

const JOB_STATES: Record<string, { label: string; tone: "gray" | "indigo" | "green" | "red" }> = {
  queued: { label: "Queued", tone: "gray" },
  running: { label: "Running", tone: "indigo" },
  done: { label: "Done", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  cancelled: { label: "Cancelled", tone: "gray" },
};

function Setup({ data }: { data: Schemas["Overview"] }) {
  const run = useRunJob();
  const steps = [
    { done: data.steps.plex, title: "Connect Plex", text: "Address and token", to: "/settings/plex" },
    { done: data.steps.libraries, title: "Libraries and paths", text: "Pick libraries, map folders if needed", to: "/settings/plex" },
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
              <p className="text-sm text-gray-400">What is in Plex now counts as existing and stays as it is.</p>
            </div>
            <Button
              size="sm"
              variant="primary"
              disabled={!ready}
              title={ready ? undefined : "Connect Plex and pick libraries first"}
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

function TodoTile({ to, count, text, tone }: { to: string; count: number; text: string; tone: "yellow" | "red" | "indigo" }) {
  const ring = { yellow: "hover:ring-yellow-500/70", red: "hover:ring-red-500/70", indigo: "hover:ring-indigo-500/70" }[tone];
  const number = { yellow: "text-yellow-300", red: "text-red-300", indigo: "text-indigo-300" }[tone];
  return (
    <Link to={to} className={`block rounded-lg bg-gray-800 p-4 ring-1 ring-gray-700 transition ${ring}`}>
      <span className={`block text-3xl font-bold ${number}`}>{count}</span>
      <span className="mt-1 block text-sm text-gray-300">{text}</span>
    </Link>
  );
}

function Fact({ value, label }: { value: number; label: string }) {
  return (
    <div className="rounded-lg bg-gray-800 p-4 ring-1 ring-gray-700">
      <span className="block text-3xl font-bold text-gray-100">{value}</span>
      <span className="mt-1 block text-sm text-gray-400">{label}</span>
    </div>
  );
}

function RecentStrip({ cards }: { cards: Schemas["RecentCard"][] }) {
  if (!cards.length) {
    return (
      <Empty title="No posters set yet" action={<ButtonLink to="/library">Open the library</ButtonLink>}>
        Pick a set for a title, or link existing posters under Match existing.
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
            {timeAgo(card.at)}, {card.origin_label}
          </p>
        </Link>
      ))}
    </div>
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

export function Dashboard() {
  useTitle("Dashboard");
  const { data, isPending, error } = useOverview();
  const run = useRunJob();
  if (isPending) return <Loading />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load the dashboard"}</Notice>;
  if (!data.setup_done) return <Setup data={data} />;

  const { todo } = data;
  const tiles = [
    todo.review > 0 && { to: "/review", count: todo.review, text: "to review", tone: "yellow" as const },
    todo.waiting > 0 && { to: "/waiting", count: todo.waiting, text: `${todo.waiting === 1 ? "season waits" : "seasons wait"} for the set`, tone: "indigo" as const },
    todo.problems > 0 && { to: "/library?status=problem", count: todo.problems, text: `${todo.problems === 1 ? "poster" : "posters"} Plex doesn't show`, tone: "red" as const },
    todo.flows_failed > 0 && {
      to: "/flows",
      count: todo.flows_failed,
      text: `${todo.flows_failed === 1 ? "flow" : "flows"} failed`,
      tone: "red" as const,
    },
    todo.match_open > 0 && {
      to: "/lists/matches",
      count: todo.match_open,
      text: `${todo.match_open === 1 ? "title" : "titles"} to match`,
      tone: "yellow" as const,
    },
    todo.unlinked_with_files > 0 && !todo.detect_open && {
      to: "/match",
      count: todo.unlinked_with_files,
      text: `${todo.unlinked_with_files === 1 ? "show" : "shows"} with posters but no set`,
      tone: "indigo" as const,
    },
  ].filter(Boolean) as { to: string; count: number; text: string; tone: "yellow" | "red" | "indigo" }[];

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={`Synced ${timeAgo(data.last_sync)}${data.last_set_check ? `, sets checked ${timeAgo(data.last_set_check)}` : ""}`}
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
          Automation is off. Programarr writes posters only when you click.{" "}
          <Link to="/settings/automation" className="font-semibold underline underline-offset-2">
            Settings
          </Link>
        </Notice>
      )}

      <Section title="To do" id="todo">
        {tiles.length ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4" data-testid="todo">
            {tiles.map((tile) => (
              <TodoTile key={tile.to} {...tile} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">Nothing to do.</p>
        )}
      </Section>

      <Section title="Recently set" id="recent" aside={<Link to="/history" className="text-indigo-300 hover:text-indigo-200">History</Link>}>
        <RecentStrip cards={data.recent} />
      </Section>

      <Section title="Library" id="library" aside={<Link to="/library" className="text-indigo-300 hover:text-indigo-200">All titles</Link>}>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Fact value={data.facts.movies} label={data.facts.movies === 1 ? "movie" : "movies"} />
          <Fact value={data.facts.shows} label={data.facts.shows === 1 ? "show" : "shows"} />
          <Fact value={data.facts.linked} label="linked to a set" />
          <Fact value={data.facts.complete} label={data.facts.complete === 1 ? "show complete" : "shows complete"} />
        </div>
      </Section>

      {data.jobs.length > 0 && (
        <Section title="Jobs" id="jobs">
          <Jobs jobs={data.jobs} />
        </Section>
      )}
    </>
  );
}
