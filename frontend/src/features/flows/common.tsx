import { ArrowsRightLeftIcon, FunnelIcon, QueueListIcon, RectangleStackIcon } from "@heroicons/react/24/outline";
import type { ReactNode } from "react";
import { siPlex, siRadarr, siSonarr } from "simple-icons";
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
export const COLLECTION_TYPE = "collection";

/** An output's outcome; a collection output also removes, queues and lists titles Plex lacks. */
export function outcomeLabel(outcome: string, dry: boolean, type?: string): string {
  if (type === COLLECTION_TYPE && outcome === "present") return "In the collection";
  switch (outcome) {
    case "added":
      return dry ? "Would add" : "Added";
    case "tagged":
      return dry ? "Would tag" : "Tags added";
    case "present":
      return "Already exists";
    case "removed":
      return dry ? "Would remove" : "Removed";
    case "queued":
      return "Queued";
    case "missing":
      return "Not in Plex";
    case "skipped":
      return "Skipped";
    case "failed":
      return "Failed";
    default:
      return outcome;
  }
}

/** The outcome after a count: "3 to add", "3 added", "25 existing". */
export function outcomeCount(outcome: string, dry: boolean, type?: string): string {
  if (type === COLLECTION_TYPE && outcome === "present") return "in the collection";
  switch (outcome) {
    case "removed":
      return dry ? "to remove" : "removed";
    case "queued":
      return "queued";
    case "missing":
      return "not in Plex";
    case "added":
      return dry ? "to add" : "added";
    case "tagged":
      return dry ? "to tag" : "tagged";
    case "present":
      return "existing";
    default:
      return outcomeLabel(outcome, dry).toLowerCase();
  }
}

export const OUTCOME_TONES: Record<string, Tone> = {
  added: "green",
  tagged: "blue",
  present: "gray",
  queued: "indigo",
  removed: "purple",
  missing: "dark",
  skipped: "yellow",
  failed: "red",
};

export function NodeIcon({ type, category, className = "h-4 w-4" }: { type: string; category: string; className?: string }) {
  if (type === "radarr") return <BrandIcon icon={siRadarr} className={className} />;
  if (type === "sonarr") return <BrandIcon icon={siSonarr} className={className} />;
  if (type === "plex_library") return <BrandIcon icon={siPlex} className={className} />;
  if (type === COLLECTION_TYPE) return <RectangleStackIcon className={`${className} shrink-0 text-amber-400`} aria-hidden="true" />;
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
    <Badge tone="green" title="Changes Radarr, Sonarr and Plex collections">
      Live
    </Badge>
  );
}

export function RunStatus({ run }: { run: Pick<FlowRunBrief, "status" | "error"> }) {
  if (run.status === "running") return <Badge tone="indigo">Running</Badge>;
  if (run.status === "error") return <Badge tone="red" title={run.error ?? undefined}>Failed</Badge>;
  if (run.status === "partial") return <Badge tone="yellow">Warnings</Badge>;
  return <Badge tone="green">Done</Badge>;
}

const COLLECTION_TOTALS = ["added", "removed", "queued", "failed"] as const;

/** What a run did, e.g. "3 to add, 25 existing", and in its collections. */
export function RunTotals({ run }: { run: FlowRunBrief }) {
  if (run.status === "running" || (run.status === "error" && !run.titles)) return null;
  const parts: ReactNode[] = [];
  const total = (key: string, count: number, label: string, red: boolean) => (
    <span key={key} className="inline-flex items-center gap-1" data-total={key}>
      <span className={`font-semibold tabular-nums ${red ? "text-red-300" : "text-gray-100"}`}>{count}</span>
      <span>{label}</span>
    </span>
  );
  for (const outcome of OUTCOMES) {
    const count = run[outcome];
    if (count) parts.push(total(outcome, count, outcomeCount(outcome, run.dry_run), outcome === "failed"));
  }
  const collected = run.collections;
  const changes = collected ? COLLECTION_TOTALS.filter((key) => collected[key]) : [];
  if (collected && changes.length) {
    parts.push(
      <span key="collections" className="inline-flex flex-wrap items-center gap-x-3 gap-y-1" data-collections>
        <RectangleStackIcon className="h-3.5 w-3.5 text-amber-400" aria-label="Collections" />
        {changes.map((key) => total(`collection-${key}`, collected[key], outcomeCount(key, run.dry_run, COLLECTION_TYPE), key === "failed"))}
      </span>,
    );
  }
  if (!parts.length) return <span className="text-xs text-gray-400">{collected ? "No changes" : "Nothing to add"}</span>;
  return <span className="run-totals flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-400">{parts}</span>;
}

export function triggerLabel(trigger: string): string {
  return trigger === "schedule" ? "Scheduled" : "Manual";
}

export type NodeFact = { label: string; value: string };

/**
 * Profile, folder and tags of a Radarr or Sonarr output, each with its name, so outputs of the same
 * service tell apart: in a row on run pages, one per line on the editor's nodes.
 */
export function OutputFacts({ facts, stacked = false }: { facts: NodeFact[]; stacked?: boolean }) {
  if (stacked) {
    return (
      <dl className="space-y-0.5 px-3 pt-1 pb-2.5 text-xs leading-4" data-facts>
        {facts.map((fact) => (
          <div key={fact.label} className="flex min-w-0 gap-1.5" data-fact={fact.label}>
            <dt className="w-11 shrink-0 text-gray-500">{fact.label}</dt>
            <dd className="min-w-0 truncate text-gray-300">{fact.value}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm" data-facts>
      {facts.map((fact) => (
        <div key={fact.label} className="flex min-w-0 items-baseline gap-1.5" data-fact={fact.label}>
          <dt className="text-xs text-gray-400">{fact.label}</dt>
          <dd className="min-w-0 truncate font-medium text-gray-200">{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}
