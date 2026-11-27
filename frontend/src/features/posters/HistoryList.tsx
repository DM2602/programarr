import {
  ArchiveBoxArrowDownIcon,
  ArrowUturnLeftIcon,
  BoltIcon,
  Cog6ToothIcon,
  HandRaisedIcon,
  LinkIcon,
  LockOpenIcon,
  PhotoIcon,
  TrashIcon,
} from "@heroicons/react/24/outline";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { siPlex } from "simple-icons";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { MonoBrandIcon } from "../../components/BrandIcon";
import { Button } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { Badge, Card, type Tone } from "../../components/ui";
import { timeAgo } from "../../lib/format";

type Row = Schemas["HistoryRow"];

const ICON = "h-3.5 w-3.5";
const plex = { tone: "yellow" as Tone, icon: <MonoBrandIcon icon={siPlex} className={ICON} /> };
const automatic = { tone: "green" as Tone, icon: <BoltIcon className={ICON} /> };

// How a change came about: manual, automatic, through Plex or restored from backup.
const HOW: Record<string, { tone: Tone; icon: ReactNode }> = {
  manual: { tone: "indigo", icon: <HandRaisedIcon className={ICON} /> },
  restore: { tone: "indigo", icon: <ArrowUturnLeftIcon className={ICON} /> },
  auto: automatic,
  season: automatic,
  set_update: automatic,
  detect: { tone: "blue", icon: <LinkIcon className={ICON} /> },
  adopt: plex,
  plex: plex,
  plex_file: plex,
  backup: { tone: "purple", icon: <ArchiveBoxArrowDownIcon className={ICON} /> },
  system: { tone: "gray", icon: <Cog6ToothIcon className={ICON} /> },
};

function placeholderIcon(row: Row) {
  if (row.action === "remove") return <TrashIcon className="h-5 w-5" />;
  if (row.action === "unlock") return <LockOpenIcon className="h-5 w-5" />;
  if (row.origin === "backup") return <ArchiveBoxArrowDownIcon className="h-5 w-5" />;
  return <PhotoIcon className="h-5 w-5" />;
}

export function HistoryList({ rows, linkTitles = false }: { rows: Row[]; linkTitles?: boolean }) {
  const run = useRunJob();
  const { confirm } = useDialogs();

  const restore = async (row: Row) => {
    const ok = await confirm({ title: "Restore this poster?", body: "The current file moves to the archive.", confirm: "Restore" });
    if (!ok) return;
    await run(() => unwrap(api.POST("/api/history/{history_id}/restore", { params: { path: { history_id: row.id } } })), "Restoring…");
  };

  return (
    <Card className="divide-y divide-gray-700">
      {rows.map((row) => {
        const how = HOW[row.origin ?? ""] ?? { tone: "gray" as Tone, icon: null };
        const source = row.source_label && (row.designer ? `${row.source_label}, ${row.designer}` : row.source_label);
        return (
          <div key={row.id} className="history-row flex items-center gap-4 px-4 py-3" data-history={row.id}>
            <div className="w-10 shrink-0">
              {row.archived ? (
                <img src={row.archived} alt="Previous poster" loading="lazy" className="aspect-[2/3] w-10 rounded object-cover ring-1 ring-gray-600" />
              ) : (
                <span className="flex aspect-[2/3] w-10 items-center justify-center text-gray-600" aria-hidden="true">
                  {placeholderIcon(row)}
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
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-gray-400">
                {row.origin_label && (
                  <Badge tone={how.tone} icon={how.icon}>
                    {row.origin_label}
                  </Badge>
                )}
                {source && <Badge tone="gray">{source}</Badge>}
                <span>
                  {timeAgo(row.at)}
                  {row.note ? `. ${row.note}` : ""}
                </span>
              </div>
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
