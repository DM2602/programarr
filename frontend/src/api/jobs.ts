import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useToast } from "../components/Toasts";
import { plural } from "../lib/format";
import { api, ApiError, errorText, unwrap, type Schemas } from "./client";

export type Job = Schemas["Job"];
type Started = { job: number } | { ok?: boolean; message?: string | null };

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

export function jobRunning(job: { status: string } | null | undefined): boolean {
  return !!job && (job.status === "queued" || job.status === "running");
}

export function jobOk(job: Job): boolean {
  if ((job.kind === "preview" || job.kind === "flow") && job.result?.outcome === "error") return false;
  return job.status === "done" && !(job.result?.outcomes ?? []).some((outcome) => !outcome.ok);
}

export function jobProgress(job: Pick<Job, "title" | "done" | "total" | "message">, fallback = ""): string {
  const title = job.title || fallback;
  if (job.total) return `${title} (${job.done} of ${job.total})`;
  return job.message ? `${title}: ${job.message}` : title;
}

const AUTO_OUTCOMES: Record<string, string> = {
  applied: "Set applied",
  review: "Added to Review",
  bound: "Already linked",
  missing: "Title not found",
  error: "Sources not reachable, see Review",
};

/** One line about a finished job, from its kind and result. */
export function jobMessage(job: Job): string {
  const r = job.result;
  if (job.status === "failed") return job.message || "Failed";
  if (job.status === "cancelled") return "Cancelled";
  if (!r) return "Done";
  switch (job.kind) {
    case "apply":
    case "restore":
      return r.outcomes?.[0]?.message ?? "Done";
    case "bind": {
      const waiting = Array.isArray(r.waiting) ? r.waiting.length : 0;
      let text = `${plural(r.written ?? 0, "poster", "posters")} set`;
      if (r.failed) text += `, ${r.failed} failed`;
      if (waiting) text += `, ${plural(waiting, "season waits", "seasons wait")} for the set`;
      const bad = r.outcomes?.find((outcome) => !outcome.ok);
      return bad ? `${text}: ${bad.message}` : text;
    }
    case "sync": {
      if (r.items === null || r.items === undefined) return "Sync done";
      const text = `Sync done: ${plural(r.new_items ?? 0, "new title", "new titles")}, ${plural(r.new_seasons ?? 0, "new season", "new seasons")}`;
      return r.skipped ? `${text} (automation off)` : text;
    }
    case "setcheck":
    case "setcheck_one":
      return `${plural(r.checked ?? 1, "set", "sets")} checked, ${r.applied ?? 0} filled in, ${r.changed ?? 0} changed`;
    case "detect":
      return `${r.checked ?? 0} checked, ${r.found ?? 0} found, ${r.high ?? 0} certain`;
    case "detect_one":
      if (!r.found) return "No matching set found";
      return r.confidence === "high" ? "Set found, confirm it under Match existing" : "Possible set found, compare it under Match existing";
    case "adopt": {
      let text = `${plural(r.adopted ?? 0, "poster", "posters")} saved as files`;
      if (r.failed) text += `, ${r.failed} failed`;
      return `${text} (${r.checked ?? 0} checked)`;
    }
    case "accept":
      return r.written ? `${plural(r.written, "poster", "posters")} taken` : "Kept current posters";
    case "auto":
      return AUTO_OUTCOMES[r.outcome ?? ""] ?? "Done";
    case "preview":
      return r.message || "Preview done";
    case "flow":
      return r.message || "Flow done";
    case "rescan":
    case "rescan_one":
      return `${plural(r.scanned ?? 0, "folder", "folders")} checked`;
    default:
      return r.message || "Done";
  }
}

export async function waitForJob(id: number, onProgress?: (job: Job) => void): Promise<Job> {
  const started = Date.now();
  for (;;) {
    await sleep(Date.now() - started < 4000 ? 400 : 1200);
    let job: Job;
    try {
      job = await unwrap(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } }));
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 401)) throw error;
      continue;
    }
    if (jobRunning(job)) {
      onProgress?.(job);
      continue;
    }
    return job;
  }
}

// Picker data is expensive to load (online sources) and settings forms keep their own edits.
const KEEP = new Set(["sets", "plex-images", "online-images", "settings", "auth"]);

/** Start an action; for jobs, show progress in a toast, then refresh what is on screen. */
export function useRunJob() {
  const toast = useToast();
  const queryClient = useQueryClient();
  return useCallback(
    async (start: () => Promise<Started>, label: string, options: { onDone?: (job: Job | null) => void } = {}): Promise<Job | null> => {
      const note = toast.show(label, { spinner: true });
      const refresh = () => queryClient.invalidateQueries({ predicate: (query) => !KEEP.has(String(query.queryKey[0])) });
      let started: Started;
      try {
        started = await start();
      } catch (error) {
        note.update(errorText(error), { kind: "error", timeout: 8000 });
        return null;
      }
      if (!("job" in started)) {
        note.update(started.message || "Done", { kind: "success", timeout: 3000 });
        await refresh();
        options.onDone?.(null);
        return null;
      }
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      let job: Job;
      try {
        job = await waitForJob(started.job, (current) => note.update(jobProgress(current, label), { spinner: true }));
      } catch (error) {
        note.update(errorText(error), { kind: "error", timeout: 8000 });
        return null;
      }
      const ok = jobOk(job);
      note.update(jobMessage(job), { kind: ok ? "success" : "error", timeout: ok ? 3600 : 9000 });
      await refresh();
      options.onDone?.(job);
      return job;
    },
    [toast, queryClient],
  );
}
