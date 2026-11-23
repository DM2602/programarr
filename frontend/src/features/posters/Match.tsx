import { useQueryClient } from "@tanstack/react-query";
import { api, errorText, unwrap } from "../../api/client";
import { jobMessage, jobRunning, useRunJob } from "../../api/jobs";
import { useDetectStats, useReviews } from "../../api/queries";
import { Button } from "../../components/Button";
import { useToast } from "../../components/Toasts";
import { Badge, Card, Empty, Loading, Notice, PageHeader, ProgressBar, Section } from "../../components/ui";
import { useTitle } from "../../lib/useTitle";
import { ReviewCard } from "./Review";

export function Match() {
  useTitle("Match existing");
  const { data: stats, isPending, error } = useDetectStats();
  const { data: found } = useReviews("detect");
  const run = useRunJob();
  const toast = useToast();
  const queryClient = useQueryClient();
  const job = stats?.job ?? null;
  const running = jobRunning(job);
  const certain = (found?.reviews ?? []).filter((review) => review.confidence === "high").length;

  const start = (scope: "shows" | "movies") =>
    run(() => unwrap(api.POST("/api/detect", { params: { query: { scope } } })), scope === "shows" ? "Matching shows…" : "Matching movies…");

  const cancel = async () => {
    if (!job) return;
    await unwrap(api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: job.id } } })).catch(() => null);
    toast.show("Cancelling…", { timeout: 2000 });
    void queryClient.invalidateQueries({ queryKey: ["detect-stats"] });
  };

  const linkAll = async () => {
    try {
      const result = await unwrap(api.POST("/api/reviews/confirm-all"));
      toast.show(result.message, { kind: "success" });
      await queryClient.invalidateQueries();
    } catch (problem) {
      toast.show(errorText(problem), { kind: "error" });
    }
  };

  return (
    <>
      <PageHeader title="Match existing" subtitle="Finds the set your poster files came from. The files stay as they are." />
      {isPending ? (
        <Loading />
      ) : error || !stats ? (
        <Notice tone="red">{error?.message ?? "Cannot load"}</Notice>
      ) : (
        <>
          <div className="grid max-w-2xl grid-cols-2 gap-4">
            <div className="rounded-lg bg-gray-800 p-4 ring-1 ring-gray-700">
              <span className="block text-3xl font-bold text-gray-100">{stats.shows}</span>
              <span className="mt-1 block text-sm text-gray-400">shows with posters but no set</span>
            </div>
            <div className="rounded-lg bg-gray-800 p-4 ring-1 ring-gray-700">
              <span className="block text-3xl font-bold text-gray-100">{stats.movies}</span>
              <span className="mt-1 block text-sm text-gray-400">movies with posters but no set</span>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap gap-2">
            <Button variant="primary" disabled={running} onClick={() => start("shows")} data-action="detect-shows">
              Match shows
            </Button>
            <Button disabled={running} onClick={() => start("movies")}>
              Match movies
            </Button>
            {running && (
              <Button variant="ghost" onClick={cancel}>
                Cancel
              </Button>
            )}
          </div>
          <p className="mt-2 text-sm text-gray-400">
            Complete shows are skipped{stats.complete ? ` (${stats.complete})` : ""}. Large libraries take a while.
          </p>
          {job && (
            <Card className="mt-4 max-w-2xl p-4" >
              <div className="flex items-center justify-between gap-3">
                <p className="min-w-0 truncate text-sm text-gray-100">
                  {job.title}
                  {running && job.message ? <span className="text-gray-400">: {job.message}</span> : null}
                </p>
                <Badge tone={running ? "indigo" : job.status === "done" ? "green" : job.status === "failed" ? "red" : "gray"}>
                  {running ? (job.total ? `${job.done} of ${job.total}` : "Running") : job.status === "done" ? "Done" : job.status === "failed" ? "Failed" : "Cancelled"}
                </Badge>
              </div>
              {running && job.total > 0 && (
                <div className="mt-3">
                  <ProgressBar done={job.done} total={job.total} />
                </div>
              )}
              {!running && <p className="mt-2 text-xs text-gray-400">{jobMessage(job)}</p>}
            </Card>
          )}
        </>
      )}

      <Section title="Plex uploads" id="adopt">
        <p className="max-w-2xl text-sm text-gray-400">
          Posters uploaded to Plex, for example with Posteria or Marquee, are saved as files in the folder and unlocked in Plex.
        </p>
        <Button className="mt-3" onClick={() => run(() => unwrap(api.POST("/api/adopt")), "Saving Plex uploads as files…")} data-action="adopt">
          Save Plex uploads as files
        </Button>
      </Section>

      <Section
        title="Found"
        id="found"
        aside={
          certain > 0 ? (
            <Button size="sm" variant="primary" onClick={linkAll} data-action="confirm-all">
              {certain === 1 ? "Link the certain match" : `Link all ${certain} certain`}
            </Button>
          ) : undefined
        }
      >
        {found?.reviews.length ? (
          <div className="space-y-4">
            {found.reviews.map((review) => (
              <ReviewCard key={review.id} review={review} />
            ))}
          </div>
        ) : (
          <Empty title="Nothing found yet" />
        )}
      </Section>
    </>
  );
}
