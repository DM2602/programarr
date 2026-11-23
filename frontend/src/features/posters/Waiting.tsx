import { Link } from "react-router";
import { api, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useWaiting } from "../../api/queries";
import { Button, ButtonLink } from "../../components/Button";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { dateOnly, seasonLabel, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";

export function Waiting() {
  useTitle("Waiting");
  const { data, isPending, error } = useWaiting();
  const run = useRunJob();
  const subtitle = data?.max_days ? `In Plex, not yet in the linked set. Checked daily, for up to ${data.max_days} days.` : "In Plex, not yet in the linked set. Checked daily.";

  return (
    <>
      <PageHeader
        title="Waiting"
        subtitle={subtitle}
        actions={data?.rows.length ? <Button onClick={() => run(() => unwrap(api.POST("/api/setcheck")), "Checking linked sets…")}>Check all sets now</Button> : undefined}
      />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the list"}</Notice>
      ) : data.rows.length ? (
        <Card className="waiting-list divide-y divide-gray-700">
          {data.rows.map((row) => (
            <div key={row.slot} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center" data-slot={row.slot}>
              <div className="flex min-w-0 flex-1 items-center gap-4">
                <Link to={`/item/${row.item_key}`} className="w-12 shrink-0" tabIndex={-1} aria-hidden="true">
                  <img src={row.thumb} alt="" loading="lazy" className="aspect-[2/3] w-12 rounded object-cover opacity-70 ring-1 ring-gray-700" />
                </Link>
                <div className="min-w-0">
                  <p className="truncate font-semibold text-gray-100">
                    <Link to={`/item/${row.item_key}`} className="hover:underline">
                      {row.title}
                    </Link>
                    , {seasonLabel(row.season)}
                  </p>
                  <p className="text-sm text-gray-400">
                    {row.waiting
                      ? `Waiting since ${dateOnly(row.waiting_since)}${row.set_title ? ` for ${row.set_title} by ${row.designer} (${row.source_label})` : ""}. Checked ${timeAgo(row.checked_at)}.`
                      : "Stopped waiting, the set has no poster for it."}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 sm:justify-end">
                <ButtonLink size="sm" to={`/item/${row.item_key}#pick=${encodeURIComponent(row.slot)}`}>
                  Pick myself
                </ButtonLink>
                {row.waiting && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/items/{key}/check", { params: { path: { key: row.item_key } } })), "Checking set…")}>
                      Check now
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => run(() => unwrap(api.POST("/api/items/{key}/slots/stopwait", { params: { path: { key: row.item_key } }, body: { slot: row.slot } })), "Stop waiting…")}
                    >
                      Stop waiting
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </Card>
      ) : (
        <Empty title="No season is waiting" />
      )}
    </>
  );
}
