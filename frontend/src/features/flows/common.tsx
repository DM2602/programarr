import { ArrowsRightLeftIcon, FunnelIcon, QueueListIcon } from "@heroicons/react/24/outline";
import type { ReactNode } from "react";
import { siRadarr, siSonarr } from "simple-icons";
import type { Schemas } from "../../api/client";
import { BrandIcon } from "../../components/BrandIcon";
import { Badge, type Tone } from "../../components/ui";

export type FlowView = Schemas["FlowView"];
export type FlowGraph = Schemas["FlowGraph-Output"];
export type FlowRunBrief = Schemas["FlowRunBrief"];
export type FlowRunDetail = Schemas["FlowRunDetail"];
export type NodeType = Schemas["NodeTypeView"];
export type NodeField = Schemas["SourceField"];
export type RunNode = Schemas["RunNode"];
export type Outcome = "added" | "tagged" | "present" | "skipped" | "failed";

/** "tagged" only appears in runs from before 26 Nov 2026, when existing titles still got missing tags. */
export const OUTCOMES: Outcome[] = ["added", "tagged", "present", "skipped", "failed"];

export function outcomeLabel(outcome: string, dry: boolean): string {
  switch (outcome) {
    case "added":
      return dry ? "To add" : "Added";
    case "tagged":
      return dry ? "To tag" : "Tags added";
    case "present":
      return "Already there";
    case "skipped":
      return "Skipped";
    case "failed":
      return "Failed";
    default:
      return outcome;
  }
}

/** The outcome after a count: "3 to add", "3 added", "25 already there". */
export function outcomeCount(outcome: string, dry: boolean): string {
  switch (outcome) {
    case "added":
      return dry ? "to add" : "added";
    case "tagged":
      return dry ? "to tag" : "tagged";
    case "present":
      return "already there";
    default:
      return outcomeLabel(outcome, dry).toLowerCase();
  }
}

export const OUTCOME_TONES: Record<string, Tone> = {
  added: "green",
  tagged: "blue",
  present: "gray",
  skipped: "yellow",
  failed: "red",
};

export function NodeIcon({ type, category, className = "h-4 w-4" }: { type: string; category: string; className?: string }) {
  if (type === "radarr") return <BrandIcon icon={siRadarr} className={className} />;
  if (type === "sonarr") return <BrandIcon icon={siSonarr} className={className} />;
  if (type === "type") return <ArrowsRightLeftIcon className={`${className} shrink-0 text-gray-400`} aria-hidden="true" />;
  if (category === "source") return <QueueListIcon className={`${className} shrink-0 text-indigo-300`} aria-hidden="true" />;
  return <FunnelIcon className={`${className} shrink-0 text-gray-400`} aria-hidden="true" />;
}

export function ModeBadge({ dryRun }: { dryRun: boolean }) {
  return dryRun ? (
    <Badge tone="gray" title="Shows what it would do, writes nothing">
      Dry run
    </Badge>
  ) : (
    <Badge tone="green" title="Adds new titles to Radarr and Sonarr">
      Live
    </Badge>
  );
}

export function RunStatus({ run }: { run: Pick<FlowRunBrief, "status" | "error"> }) {
  if (run.status === "running") return <Badge tone="indigo">Running</Badge>;
  if (run.status === "error") return <Badge tone="red" title={run.error ?? undefined}>Failed</Badge>;
  if (run.status === "partial") return <Badge tone="yellow">With notes</Badge>;
  return <Badge tone="green">Done</Badge>;
}

/** What a run did, e.g. "3 to add, 25 already there". */
export function RunTotals({ run }: { run: FlowRunBrief }) {
  if (run.status === "running" || (run.status === "error" && !run.titles)) return null;
  const parts: ReactNode[] = [];
  for (const outcome of OUTCOMES) {
    const count = run[outcome];
    if (!count) continue;
    parts.push(
      <span key={outcome} className="inline-flex items-center gap-1" data-total={outcome}>
        <span className={`font-semibold tabular-nums ${outcome === "failed" ? "text-red-300" : "text-gray-100"}`}>{count}</span>
        <span>{outcomeCount(outcome, run.dry_run)}</span>
      </span>,
    );
  }
  if (!parts.length) return <span className="text-xs text-gray-400">Nothing to add</span>;
  return <span className="run-totals flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-400">{parts}</span>;
}

export function triggerLabel(trigger: string): string {
  return trigger === "schedule" ? "Scheduled" : "Manual";
}
