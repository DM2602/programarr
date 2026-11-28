import { PlayIcon, PlusIcon } from "@heroicons/react/24/outline";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api, errorText, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useFlows } from "../../api/queries";
import { Button } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { useToast } from "../../components/Toasts";
import { Badge, Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { joinAnd, plural, timeAgo, timeUntil } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { ModeBadge, RunStatus, RunTotals, type FlowView } from "./common";

function FlowRow({ flow }: { flow: FlowView }) {
  const runJob = useRunJob();
  const navigate = useNavigate();
  const last = flow.last_run;
  const start = async () => {
    const job = await runJob(
      () => unwrap(api.POST("/api/flows/{flow_id}/run", { params: { path: { flow_id: flow.id } }, body: {} })),
      flow.dry_run ? "Dry run…" : "Running…",
    );
    const runId = job?.result?.run;
    if (runId) navigate(`/flows/runs/${runId}`);
  };
  return (
    <div className="flow-row flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center" data-flow={flow.id}>
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
        <p className="mt-1 text-xs text-gray-400">
          {flow.outputs.length ? joinAnd(flow.outputs) : "No output"}, {flow.schedule_text.toLowerCase()}
          {flow.next_run_at ? `, next ${timeUntil(flow.next_run_at)}` : ""}
        </p>
      </div>
      <div className="flex min-w-0 flex-col gap-1 sm:w-80 sm:items-end">
        {last ? (
          <Link to={`/flows/runs/${last.id}`} className="flex flex-wrap items-center gap-2 text-xs text-gray-400 hover:text-gray-200 sm:justify-end">
            <RunStatus run={last} />
            <span>{timeAgo(last.started_at)}</span>
          </Link>
        ) : (
          <span className="text-xs text-gray-500">Never run</span>
        )}
        {last && <RunTotals run={last} />}
      </div>
      <Button size="sm" icon={<PlayIcon className="h-4 w-4" />} onClick={() => void start()} disabled={last?.status === "running" || flow.problems.length > 0} data-action="run">
        {flow.dry_run ? "Dry run" : "Run"}
      </Button>
    </div>
  );
}

export function Flows() {
  useTitle("Flows");
  const { data, isPending, error } = useFlows();
  const dialogs = useDialogs();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const create = async () => {
    const name = await dialogs.prompt({ title: "New flow", label: "Name", placeholder: "Streaming TOP 10", confirm: "Create" });
    if (!name) return;
    try {
      const flow = await unwrap(api.POST("/api/flows", { body: { name } }));
      queryClient.invalidateQueries({ queryKey: ["flows"] });
      navigate(`/flows/${flow.id}`);
    } catch (problem) {
      toast.show(errorText(problem), { kind: "error", timeout: 8000 });
    }
  };

  return (
    <>
      <PageHeader
        title="Flows"
        subtitle="New flows start as a dry run."
        actions={
          <Button variant="primary" icon={<PlusIcon className="h-4 w-4" />} onClick={() => void create()} data-action="new-flow">
            New flow
          </Button>
        }
      />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the flows"}</Notice>
      ) : data.flows.length ? (
        <Card className="divide-y divide-gray-700">
          {data.flows.map((flow) => (
            <FlowRow key={flow.id} flow={flow} />
          ))}
        </Card>
      ) : (
        <Empty
          title="No flows yet"
          action={
            <Button variant="primary" icon={<PlusIcon className="h-4 w-4" />} onClick={() => void create()}>
              New flow
            </Button>
          }
        />
      )}
    </>
  );
}
