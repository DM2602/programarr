import { LockOpenIcon, PhotoIcon, TrashIcon } from "@heroicons/react/24/outline";
import { Link } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { Button } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { Card } from "../../components/ui";
import { timeAgo } from "../../lib/format";

export function HistoryList({ rows, linkTitles = false }: { rows: Schemas["HistoryRow"][]; linkTitles?: boolean }) {
  const run = useRunJob();
  const { confirm } = useDialogs();

  const restore = async (row: Schemas["HistoryRow"]) => {
    const ok = await confirm({ title: "Restore this poster?", body: "The current file goes to the archive.", confirm: "Restore" });
    if (!ok) return;
    await run(() => unwrap(api.POST("/api/history/{history_id}/restore", { params: { path: { history_id: row.id } } })), "Restoring…");
  };

  return (
    <Card className="divide-y divide-gray-700">
      {rows.map((row) => {
        const meta = [
          timeAgo(row.at),
          row.origin_label,
          row.source_label && (row.designer ? `${row.source_label} (${row.designer})` : row.source_label),
        ].filter(Boolean);
        return (
          <div key={row.id} className="history-row flex items-center gap-4 px-4 py-3" data-history={row.id}>
            <div className="w-10 shrink-0">
              {row.archived ? (
                <img src={row.archived} alt="Previous poster" loading="lazy" className="aspect-[2/3] w-10 rounded object-cover ring-1 ring-gray-600" />
              ) : (
                <span className="flex aspect-[2/3] w-10 items-center justify-center text-gray-600" aria-hidden="true">
                  {row.action === "remove" ? <TrashIcon className="h-5 w-5" /> : row.action === "unlock" ? <LockOpenIcon className="h-5 w-5" /> : <PhotoIcon className="h-5 w-5" />}
                </span>
              )}
            </div>
            <div className="min-w-0 flex-1 text-sm">
              <p className="truncate text-gray-100">
                <span className="font-semibold">{row.action_label}</span>
                {", "}
                {linkTitles && row.item_key ? (
                  <Link to={`/item/${row.item_key}`} className="hover:underline">
                    {row.title}
                  </Link>
                ) : (
                  row.title
                )}
              </p>
              <p className="truncate text-xs text-gray-400">
                {meta.join(", ")}
                {row.note ? `. ${row.note}` : ""}
              </p>
            </div>
            {row.archived && (
              <Button size="sm" onClick={() => restore(row)} data-action="restore">
                Restore
              </Button>
            )}
          </div>
        );
      })}
    </Card>
  );
}
