import { Link, useSearchParams } from "react-router";
import { useFlowRuns, useFlows } from "../../api/queries";
import { SelectField } from "../../components/Controls";
import { Pager } from "../../components/Pager";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { ModeBadge, RunStatus, RunTotals, triggerLabel } from "./common";

export function FlowHistory() {
  useTitle("Flow history");
  const [params, setParams] = useSearchParams();
  const flow = Number(params.get("flow") ?? 0) || 0;
  const page = Number(params.get("page") ?? 1) || 1;
  const flows = useFlows();
  const runs = useFlowRuns(flow, page);
  const go = (next: { flow?: number; page?: number }) => {
    const query = new URLSearchParams();
    const chosen = next.flow ?? flow;
    if (chosen) query.set("flow", String(chosen));
    if ((next.page ?? 1) > 1) query.set("page", String(next.page));
    setParams(query);
  };
  return (
    <>
      <PageHeader
        title="Flow history"
        actions={
          flows.data && flows.data.flows.length > 1 ? (
            <SelectField
              label="Flow"
              value={String(flow)}
              onChange={(value) => go({ flow: Number(value), page: 1 })}
              options={[{ value: "0", label: "All flows" }, ...flows.data.flows.map((entry) => ({ value: String(entry.id), label: entry.name }))]}
            />
          ) : undefined
        }
      />
      {runs.isPending ? (
        <Loading />
      ) : runs.error || !runs.data ? (
        <Notice tone="red">{runs.error?.message ?? "Cannot load the runs"}</Notice>
      ) : runs.data.runs.length ? (
        <>
          <Card className="divide-y divide-gray-700">
            {runs.data.runs.map((run) => (
              <Link key={run.id} to={`/flows/runs/${run.id}`} className="history-run flex flex-col gap-2 px-4 py-3 hover:bg-gray-700/30 sm:flex-row sm:items-center" data-run={run.id}>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold text-gray-100">{run.flow_name}</p>
                  <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                    <ModeBadge dryRun={run.dry_run} />
                    <RunStatus run={run} />
                    <span>
                      {triggerLabel(run.trigger)}, {timeAgo(run.started_at)}
                    </span>
                    {run.error && <span className="text-red-300">{run.error}</span>}
                  </p>
                </div>
                <RunTotals run={run} />
              </Link>
            ))}
          </Card>
          <Pager page={runs.data.page} pages={runs.data.pages} onPage={(next) => go({ page: next })} newer="Newer" older="Older" />
        </>
      ) : (
        <Empty title="No runs yet" />
      )}
    </>
  );
}
