import { useState } from "react";
import { Link } from "react-router";
import { api, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { usePending } from "../../api/queries";
import { Button } from "../../components/Button";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { dateOnly, seasonLabel, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { PickerDrawer } from "./Picker";

/** Seasons Plex has but the linked set does not have yet. */
export function Pending() {
  useTitle("Pending");
  const { data, isPending, error } = usePending();
  const run = useRunJob();
  const [picking, setPicking] = useState<{ item: string; slot: string } | null>(null);
  const subtitle = data?.max_days ? `In Plex, not yet in the linked set. Pending for up to ${data.max_days} days.` : "In Plex, not yet in the linked set.";

  return (
    <>
      <PageHeader
        title="Pending"
        subtitle={subtitle}
        actions={data?.rows.length ? <Button onClick={() => run(() => unwrap(api.POST("/api/setcheck")), "Checking linked sets…")}>Check all sets now</Button> : undefined}
      />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the list"}</Notice>
      ) : data.rows.length ? (
        <Card className="pending-list divide-y divide-gray-700">
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
                    {row.pending
                      ? `Pending since ${dateOnly(row.pending_since)}${row.set_title ? ` for ${row.set_title} by ${row.designer} (${row.source_label})` : ""}. Checked ${timeAgo(row.checked_at)}.`
                      : "The set has no poster for it."}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 sm:justify-end">
                <Button size="sm" onClick={() => setPicking({ item: row.item_key, slot: row.slot })} data-action="pick">
                  Select manually
                </Button>
                {row.pending && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/items/{key}/check", { params: { path: { key: row.item_key } } })), "Checking set…")}>
                      Check now
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => run(() => unwrap(api.POST("/api/items/{key}/slots/dismiss", { params: { path: { key: row.item_key } }, body: { slot: row.slot } })), "Dismissing…")}
                      data-action="dismiss"
                    >
                      Dismiss
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </Card>
      ) : (
        <Empty title="Nothing pending" />
      )}
      {picking && <PickerDrawer itemKey={picking.item} slotId={picking.slot} onClose={() => setPicking(null)} />}
    </>
  );
}
