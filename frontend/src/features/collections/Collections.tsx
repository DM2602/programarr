import { ExclamationTriangleIcon } from "@heroicons/react/20/solid";
import { TrashIcon } from "@heroicons/react/24/outline";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useCollections, useOtherCollections } from "../../api/queries";
import { Button, ButtonLink } from "../../components/Button";
import { SelectField, Tabs } from "../../components/Controls";
import { useDialogs } from "../../components/Dialogs";
import { Badge, Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { plural, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { CollectionState, type Collection } from "./common";

type Other = Schemas["OtherCollection"];

function CollectionCard({ collection }: { collection: Collection }) {
  const { in_plex: count, queued } = collection.counts;
  return (
    <Link to={`/collections/${collection.id}`} className="collection-card group block" data-collection={collection.title}>
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl bg-gray-800 shadow-poster ring-1 ring-gray-700 transition duration-300 group-hover:scale-[1.03] group-hover:ring-gray-500 group-focus-visible:ring-indigo-400">
        <img src={collection.image} alt="" loading="lazy" width={200} height={300} className={`h-full w-full object-cover ${collection.live ? "" : "opacity-60 grayscale-[35%]"}`} />
        <div className="absolute inset-x-0 top-0 flex items-start justify-between gap-1 p-2">
          <Badge tone={collection.kind === "movie" ? "blue" : "purple"}>{collection.kind === "movie" ? "Movies" : "Shows"}</Badge>
          <CollectionState collection={collection} />
        </div>
        {collection.error && (
          <span className="absolute right-2 bottom-2 rounded-full bg-gray-900/80 p-1 text-red-400" title={collection.error}>
            <ExclamationTriangleIcon className="h-5 w-5" aria-label="Error" />
          </span>
        )}
      </div>
      <p className="mt-2 truncate text-sm font-semibold text-gray-100" title={collection.title}>
        {collection.title}
      </p>
      <p className="truncate text-xs text-gray-400">
        {plural(count, "title", "titles")}
        {queued ? `, ${queued} queued` : ""}
      </p>
    </Link>
  );
}

function Managed() {
  const { data, isPending, error } = useCollections();
  if (isPending) return <Loading />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load the collections"}</Notice>;
  if (!data.collections.length) {
    return (
      <Empty title="No collections" action={<ButtonLink to="/flows">Flows</ButtonLink>}>
        Add a Plex collection output to a flow.
      </Empty>
    );
  }
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-x-4 gap-y-6" data-testid="collections-grid">
      {data.collections.map((collection) => (
        <CollectionCard key={collection.id} collection={collection} />
      ))}
    </div>
  );
}

function OtherRow({ row, selected, onToggle }: { row: Other; selected: boolean; onToggle: () => void }) {
  const id = `other-${row.key}`;
  return (
    <div className="other-collection flex items-center gap-4 px-4 py-3" data-other={row.title}>
      <input id={id} type="checkbox" className="h-4 w-4 shrink-0 rounded border-gray-500 bg-gray-700 text-indigo-600 focus:ring-indigo-500" checked={selected} onChange={onToggle} />
      <label htmlFor={id} className="flex min-w-0 flex-1 cursor-pointer items-center gap-4">
        <img src={row.image} alt="" loading="lazy" className="aspect-[2/3] w-11 shrink-0 rounded object-cover ring-1 ring-gray-700" />
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-gray-100">{row.title}</span>
            {row.smart && <Badge tone="gray">Smart</Badge>}
            {row.labels.map((label) => (
              <Badge key={label} tone="dark">
                {label}
              </Badge>
            ))}
          </span>
          <span className="block text-sm text-gray-400">
            {row.library}, {row.smart ? "filter" : plural(row.count, "title", "titles")}
            {row.updated_at ? `, changed ${timeAgo(row.updated_at)}` : ""}
          </span>
        </span>
      </label>
    </div>
  );
}

function Others({ selected, setSelected }: { selected: Set<string>; setSelected: (next: Set<string>) => void }) {
  const { data, isPending, error } = useOtherCollections(true);
  const [library, setLibrary] = useState("");
  const [query, setQuery] = useState("");
  const libraries = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of data?.collections ?? []) seen.set(row.library_key, row.library);
    return [...seen.entries()].map(([value, label]) => ({ value, label }));
  }, [data]);
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.collections ?? []).filter((row) => (!library || row.library_key === library) && (!needle || row.title.toLowerCase().includes(needle)));
  }, [data, library, query]);

  if (isPending) return <Loading text="Reading Plex…" />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load the collections"}</Notice>;
  if (data.error) return <Notice tone="red">{data.error}</Notice>;
  if (!data.collections.length) return <Empty title="No other collections" />;

  const toggle = (key: string) => {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelected(next);
  };
  const allShown = rows.length > 0 && rows.every((row) => selected.has(row.key));
  const toggleAll = () => {
    const next = new Set(selected);
    for (const row of rows) {
      if (allShown) next.delete(row.key);
      else next.add(row.key);
    }
    setSelected(next);
  };

  return (
    <>
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="flex flex-wrap items-end gap-3">
          {libraries.length > 1 && (
            <SelectField label="Plex library" id="other-library" value={library} onChange={setLibrary} options={[{ value: "", label: "All libraries" }, ...libraries]} />
          )}
          <Button size="sm" variant="ghost" onClick={toggleAll} data-action="select-all">
            {allShown ? "Select none" : "Select all"}
          </Button>
        </div>
        <input className="field-input md:w-64" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Title" aria-label="Filter by title" />
      </div>
      {rows.length ? (
        <Card className="divide-y divide-gray-700">
          {rows.map((row) => (
            <OtherRow key={row.key} row={row} selected={selected.has(row.key)} onToggle={() => toggle(row.key)} />
          ))}
        </Card>
      ) : (
        <Empty title="No matches" />
      )}
    </>
  );
}

export function Collections() {
  useTitle("Collections");
  const [params] = useSearchParams();
  const tab = params.get("tab") === "other" ? "other" : "managed";
  const managed = useCollections();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const run = useRunJob();
  const dialogs = useDialogs();

  const remove = async () => {
    const keys = [...selected];
    const ok = await dialogs.confirm({
      title: `Delete ${plural(keys.length, "collection", "collections")} in Plex?`,
      body: "The titles stay in the library.",
      confirm: "Delete",
      danger: true,
    });
    if (!ok) return;
    await run(() => unwrap(api.POST("/api/collections/other/delete", { body: { keys } })), "Deleting collections…", { onDone: () => setSelected(new Set()) });
  };

  return (
    <>
      <PageHeader
        title="Collections"
        actions={
          tab === "other" && selected.size > 0 ? (
            <Button variant="danger" icon={<TrashIcon className="h-4 w-4" />} onClick={() => void remove()} data-action="delete-collections">
              Delete {selected.size}
            </Button>
          ) : undefined
        }
      />
      <div className="mb-6">
        <Tabs
          label="Collections"
          items={[
            { key: "managed", label: "Managed", to: "/collections", active: tab === "managed", count: managed.data?.collections.length },
            { key: "other", label: "Other", to: "/collections?tab=other", active: tab === "other" },
          ]}
        />
      </div>
      {tab === "managed" ? <Managed /> : <Others selected={selected} setSelected={setSelected} />}
    </>
  );
}
