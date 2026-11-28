import { PhotoIcon } from "@heroicons/react/24/outline";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, errorText, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useCollection } from "../../api/queries";
import { Button, ButtonLink } from "../../components/Button";
import { Tabs } from "../../components/Controls";
import { useDialogs } from "../../components/Dialogs";
import { useToast } from "../../components/Toasts";
import { Card, Empty, Loading, Notice, PageHeader, Section } from "../../components/ui";
import { dateOnly, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { OutputFacts } from "../flows/common";
import { KindIcon, POSTER_FALLBACK, Ratings } from "../lists/common";
import { CollectionState, ORDER_LABELS, ServiceBadge, SYNC_LABELS, type CollectionDetail } from "./common";
import { CollectionPosterPicker } from "./PosterPicker";

type Title = Schemas["CollectionTitle"];
type State = Title["state"];
type Visibility = "show_recommended" | "show_home" | "show_shared";

const STATES: { key: State; label: string; empty: string }[] = [
  { key: "in_plex", label: "In the collection", empty: "No titles in the collection" },
  { key: "queued", label: "Queued", empty: "Nothing queued" },
  { key: "missing", label: "Not in Plex", empty: "All titles are in Plex" },
];

const POSTER_SOURCES: Record<string, string> = { tpdb: "ThePosterDB", url: "Link", upload: "Upload" };

const VISIBILITY: { key: Visibility; label: string }[] = [
  { key: "show_recommended", label: "Library recommended" },
  { key: "show_home", label: "Home" },
  { key: "show_shared", label: "Friends' Home" },
];

function Switch({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex cursor-pointer items-center justify-between gap-4 py-2 text-sm text-gray-200">
      {label}
      <input id={id} type="checkbox" className="peer sr-only" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span
        aria-hidden="true"
        className="relative h-5 w-9 shrink-0 rounded-full bg-gray-600 transition peer-checked:bg-indigo-600 peer-disabled:opacity-50 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-indigo-400 after:absolute after:top-0.5 after:left-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-4"
      />
    </label>
  );
}

function VisibilityCard({ collection }: { collection: CollectionDetail }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const change = async (key: Visibility, value: boolean) => {
    setBusy(true);
    try {
      const updated = await unwrap(api.PUT("/api/collections/{collection_id}", { params: { path: { collection_id: collection.id } }, body: { [key]: value } }));
      queryClient.setQueryData(["collection", collection.id], updated);
      void queryClient.invalidateQueries({ queryKey: ["collections"] });
    } catch (error) {
      toast.show(errorText(error), { kind: "error" });
      void queryClient.invalidateQueries({ queryKey: ["collection", collection.id] });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="visibility px-4 py-2">
      <h2 className="pt-1 text-sm font-semibold text-gray-300">Show in Plex</h2>
      <div className="divide-y divide-gray-700">
        {VISIBILITY.map((entry) => (
          <div key={entry.key} data-visibility={entry.key}>
            <Switch label={entry.label} checked={collection[entry.key]} disabled={busy} onChange={(value) => void change(entry.key, value)} />
          </div>
        ))}
      </div>
    </Card>
  );
}

function TitleRow({ collection, row, place }: { collection: CollectionDetail; row: Title; place: number }) {
  const run = useRunJob();
  const media = row.media;
  const unqueue = () =>
    run(() => unwrap(api.DELETE("/api/queue/{collection_id}/{media_id}", { params: { path: { collection_id: collection.id, media_id: media.id } } })), "Removing…");
  return (
    <div className="collection-title flex items-center gap-4 px-4 py-3" data-title={media.title} data-state={row.state}>
      {row.state === "in_plex" && collection.item_order === "list" && <span className="w-6 shrink-0 text-right text-sm tabular-nums text-gray-500">{place}</span>}
      <img src={media.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-10 shrink-0 rounded object-cover ring-1 ring-gray-700" />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2">
          <KindIcon kind={media.kind} />
          {row.item_key ? (
            <Link to={`/item/${row.item_key}`} className="font-semibold text-gray-100 hover:underline">
              {media.title}
            </Link>
          ) : (
            <span className="font-semibold text-gray-100">{media.title}</span>
          )}
          {media.year && <span className="text-sm text-gray-400">{media.year}</span>}
        </p>
        {row.state === "queued" ? (
          <p className="text-sm text-gray-400">
            Queued {timeAgo(row.queued_at)}
            {row.expires_at ? `, until ${dateOnly(row.expires_at)}` : ""}
          </p>
        ) : (
          <Ratings media={media} />
        )}
      </div>
      {row.state === "queued" && (
        <div className="flex items-center gap-3">
          <ServiceBadge service={row.service} />
          <Button size="sm" variant="ghost" onClick={() => void unqueue()} data-action="unqueue">
            Remove
          </Button>
        </div>
      )}
    </div>
  );
}

function Titles({ collection }: { collection: CollectionDetail }) {
  const [state, setState] = useState<State>("in_plex");
  const rows = collection.titles.filter((row) => row.state === state);
  const entry = STATES.find((item) => item.key === state)!;
  return (
    <>
      <Tabs
        label="Titles"
        items={STATES.filter((item) => item.key === "in_plex" || item.key === state || collection.counts[item.key]).map((item) => ({
          key: item.key,
          label: item.label,
          active: item.key === state,
          count: collection.counts[item.key],
          onClick: () => setState(item.key),
        }))}
      />
      <div className="mt-4">
        {rows.length ? (
          <Card className="divide-y divide-gray-700">
            {rows.map((row, index) => (
              <TitleRow key={row.media.id} collection={collection} row={row} place={index + 1} />
            ))}
          </Card>
        ) : (
          <Empty title={entry.empty} />
        )}
      </div>
    </>
  );
}

export function CollectionPage() {
  const { id = "" } = useParams();
  const collectionId = Number(id);
  const { data: collection, isPending, error } = useCollection(collectionId);
  const [picking, setPicking] = useState(false);
  const run = useRunJob();
  const dialogs = useDialogs();
  const navigate = useNavigate();
  useTitle(collection?.title ?? "Collection");

  if (isPending) return <Loading />;
  if (error || !collection) return <Notice tone="red">{error?.message ?? "Collection not found"}</Notice>;

  const facts = [
    { label: "Library", value: collection.library },
    { label: "Sync", value: SYNC_LABELS[collection.sync_mode] },
    { label: "Order", value: ORDER_LABELS[collection.item_order] },
    ...(collection.max_items ? [{ label: "Max", value: String(collection.max_items) }] : []),
    ...(collection.synced_at ? [{ label: "Synced", value: timeAgo(collection.synced_at) }] : []),
  ];

  const remove = async (keep: boolean) => {
    const ok = await dialogs.confirm(
      keep
        ? { title: `Stop managing ${collection.title}?`, body: "The collection stays in Plex.", confirm: "Stop managing" }
        : { title: `Delete ${collection.title} in Plex?`, body: "The titles stay in the library.", confirm: "Delete", danger: true },
    );
    if (!ok) return;
    await run(
      () => unwrap(api.DELETE("/api/collections/{collection_id}", { params: { path: { collection_id: collection.id }, query: { keep } } })),
      keep ? "Removing…" : "Deleting…",
      { onDone: () => navigate("/collections") },
    );
  };

  return (
    <>
      <PageHeader
        title={collection.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <CollectionState collection={collection} />
            {collection.flow_id ? (
              <Link to={`/flows/${collection.flow_id}`} className="text-indigo-300 hover:underline">
                Flow {collection.flow_name}
              </Link>
            ) : (
              <span>No flow</span>
            )}
          </span>
        }
        actions={
          !collection.in_flow ? (
            <>
              <Button variant="ghost" onClick={() => void remove(true)} data-action="keep-collection">
                Stop managing
              </Button>
              <Button variant="danger" onClick={() => void remove(false)} data-action="delete-collection">
                Delete in Plex
              </Button>
            </>
          ) : collection.flow_id ? (
            <ButtonLink to={`/flows/${collection.flow_id}`}>Edit flow</ButtonLink>
          ) : undefined
        }
      />
      {collection.error && (
        <div className="mb-4">
          <Notice tone="red">{collection.error}</Notice>
        </div>
      )}
      <div className="grid gap-6 md:grid-cols-[12rem_1fr]">
        <div>
          <button type="button" onClick={() => setPicking(true)} className="group block w-full max-w-48" aria-label="Change poster">
            <span className="block aspect-[2/3] overflow-hidden rounded-xl bg-gray-800 shadow-poster ring-1 ring-gray-700 transition group-hover:ring-gray-500 group-focus-visible:ring-indigo-400">
              <img src={collection.image} alt="" className="h-full w-full object-cover" />
            </span>
          </button>
          <div className="mt-3 flex max-w-48 flex-wrap items-center justify-between gap-2">
            <Button size="sm" icon={<PhotoIcon className="h-4 w-4" />} onClick={() => setPicking(true)} data-action="change-poster">
              Change poster
            </Button>
            {collection.poster_source && <span className="text-xs text-gray-400">{POSTER_SOURCES[collection.poster_source] ?? collection.poster_source}</span>}
          </div>
        </div>
        <div className="min-w-0 space-y-6">
          <OutputFacts facts={facts} />
          <div className="max-w-md">
            <VisibilityCard collection={collection} />
          </div>
        </div>
      </div>
      <Section title="Titles" id="collection-titles">
        <Titles collection={collection} />
      </Section>
      {picking && <CollectionPosterPicker collection={collection} onClose={() => setPicking(false)} />}
    </>
  );
}
