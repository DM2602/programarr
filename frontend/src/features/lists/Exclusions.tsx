import { PlusIcon } from "@heroicons/react/24/outline";
import { useState } from "react";
import { Link } from "react-router";
import { api, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useExclusions } from "../../api/queries";
import { Button } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { dateOnly } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { KIND_LABELS, POSTER_FALLBACK, Ratings } from "./common";
import { TitleSearch } from "./FixDialog";

export function Exclusions() {
  useTitle("Exclusions");
  const { data, isPending, error } = useExclusions();
  const [adding, setAdding] = useState(false);
  const run = useRunJob();
  const dialogs = useDialogs();

  const remove = (id: number) => run(() => unwrap(api.DELETE("/api/exclusions/{exclusion_id}", { params: { path: { exclusion_id: id } } })), "Removing…");

  return (
    <>
      <PageHeader
        title="Exclusions"
        subtitle="Never in a list."
        actions={
          <Button variant="primary" icon={<PlusIcon className="h-4 w-4" />} onClick={() => setAdding(true)} data-action="add-exclusion">
            Add title
          </Button>
        }
      />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load"}</Notice>
      ) : data.rows.length ? (
        <Card className="divide-y divide-gray-700">
          {data.rows.map((row) => (
            <div key={row.id} className="exclusion flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center" data-exclusion={row.id}>
              <div className="flex min-w-0 flex-1 items-center gap-4">
                <img src={row.media.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-11 shrink-0 rounded object-cover ring-1 ring-gray-700" />
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-x-2">
                    <span className="font-semibold text-gray-100">{row.media.title}</span>
                    <span className="text-sm text-gray-400">{row.media.year ?? "?"}</span>
                    <span className="text-xs text-gray-500">{KIND_LABELS[row.media.kind]}</span>
                    {row.plex_key && (
                      <Link to={`/item/${row.plex_key}`} className="text-xs font-semibold text-green-300 hover:underline">
                        In Plex
                      </Link>
                    )}
                  </p>
                  <p className="text-sm text-gray-400">
                    {row.reason ? `${row.reason}, ` : ""}
                    {dateOnly(row.created_at)}
                  </p>
                  <Ratings media={row.media} />
                </div>
              </div>
              <Button size="sm" variant="ghost" onClick={() => remove(row.id)}>
                Remove
              </Button>
            </div>
          ))}
        </Card>
      ) : (
        <Empty title="No exclusions" />
      )}
      {adding && (
        <TitleSearch
          target={{ title: "", candidates: [] }}
          heading="Exclude a title"
          confirm="Exclude"
          onClose={() => setAdding(false)}
          onPick={async (candidate) => {
            setAdding(false);
            const reason = await dialogs.prompt({ title: `Exclude ${candidate.title}`, label: "Reason", placeholder: "Optional", confirm: "Exclude" });
            if (reason === null) return;
            await run(() => unwrap(api.POST("/api/exclusions", { body: { kind: candidate.kind, tmdb_id: candidate.tmdb_id, reason: reason || null } })), "Excluding…");
          }}
        />
      )}
    </>
  );
}
