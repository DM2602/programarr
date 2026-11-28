import { Link } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useQueue } from "../../api/queries";
import { Button } from "../../components/Button";
import { Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { dateOnly, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { KindIcon, POSTER_FALLBACK } from "../lists/common";
import { ServiceBadge } from "./common";

type Entry = Schemas["QueueEntry"];

function QueueRow({ entry, onRemove }: { entry: Entry; onRemove: () => void }) {
  const media = entry.media;
  return (
    <div className="queue-entry flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center" data-title={media.title}>
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <img src={media.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-11 shrink-0 rounded object-cover ring-1 ring-gray-700" />
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-x-2">
            <KindIcon kind={media.kind} />
            <span className="font-semibold text-gray-100">{media.title}</span>
            <span className="text-sm text-gray-400">{media.year ?? "?"}</span>
          </p>
          <p className="text-sm text-gray-400">
            <Link to={`/collections/${entry.collection_id}`} className="text-indigo-300 hover:underline">
              {entry.collection}
            </Link>
            , queued {timeAgo(entry.queued_at)}
            {entry.expires_at ? `, until ${dateOnly(entry.expires_at)}` : ""}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3 self-end sm:self-auto">
        <ServiceBadge service={entry.service} />
        <Button size="sm" variant="ghost" onClick={onRemove} data-action="unqueue">
          Remove
        </Button>
      </div>
    </div>
  );
}

export function Queue() {
  useTitle("Queue");
  const { data, isPending, error } = useQueue();
  const run = useRunJob();
  const remove = (entry: Entry) =>
    run(
      () => unwrap(api.DELETE("/api/queue/{collection_id}/{media_id}", { params: { path: { collection_id: entry.collection_id, media_id: entry.media.id } } })),
      "Removing…",
    );
  return (
    <>
      <PageHeader title="Queue" subtitle={data ? (data.days ? `Kept for ${data.days} days` : "Kept until they arrive") : " "} />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the queue"}</Notice>
      ) : data.entries.length ? (
        <Card className="divide-y divide-gray-700">
          {data.entries.map((entry) => (
            <QueueRow key={`${entry.collection_id}-${entry.media.id}`} entry={entry} onRemove={() => void remove(entry)} />
          ))}
        </Card>
      ) : (
        <Empty title="Queue is empty" />
      )}
    </>
  );
}
