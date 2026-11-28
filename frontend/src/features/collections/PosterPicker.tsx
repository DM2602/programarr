import { ArrowTopRightOnSquareIcon, MagnifyingGlassIcon } from "@heroicons/react/24/outline";
import { useEffect, useId, useState, type FormEvent } from "react";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useCollectionTpdb, useCollectionTpdbPosters } from "../../api/queries";
import { Button, ExternalButton } from "../../components/Button";
import { Tabs } from "../../components/Controls";
import { Drawer, useDrawer } from "../../components/Drawer";
import { Empty, Loading, Notice } from "../../components/ui";
import { ChosenFooter, FileTab, ImageChoice, LinkTab, type Chosen } from "../posters/Picker";

type Collection = Schemas["CollectionDetail"];
type Tab = "tpdb" | "link" | "file";

const TABS: { key: Tab; label: string }[] = [
  { key: "tpdb", label: "ThePosterDB" },
  { key: "link", label: "Link" },
  { key: "file", label: "File" },
];

function TpdbTab({ collection, chosen, onChoose }: { collection: Collection; chosen: Chosen | null; onChoose: (choice: Chosen) => void }) {
  const id = useId();
  const [text, setText] = useState(collection.title);
  const [term, setTerm] = useState(collection.title);
  const [page, setPage] = useState<string | null>(null);
  const search = useCollectionTpdb(collection.id, term, !!term.trim());
  const posters = useCollectionTpdbPosters(collection.id, page);
  const results = search.data?.results ?? [];

  // One page found: show its posters right away.
  useEffect(() => {
    if (results.length === 1 && !page) setPage(results[0].url);
  }, [results, page]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTerm(text.trim());
    setPage(null);
  };

  return (
    <div>
      <form role="search" onSubmit={submit} className="flex gap-2">
        <label htmlFor={id} className="sr-only">
          Collection name
        </label>
        <input id={id} type="search" className="field-input" value={text} onChange={(event) => setText(event.target.value)} placeholder="Collection name" />
        <Button type="submit" icon={<MagnifyingGlassIcon className="h-4 w-4" />} disabled={!text.trim()}>
          Search
        </Button>
      </form>
      <div className="mt-4">
        {search.isFetching && !search.data ? (
          <Loading text="Searching ThePosterDB…" />
        ) : search.data?.error ? (
          <Notice tone="red">{search.data.error}</Notice>
        ) : !results.length ? (
          term.trim() ? <Empty title="No collections found" /> : null
        ) : (
          <ul className="flex flex-wrap gap-2" aria-label="Collections on ThePosterDB">
            {results.map((result) => (
              <li key={result.id}>
                <button
                  type="button"
                  aria-pressed={page === result.url}
                  onClick={() => setPage(result.url)}
                  className={`rounded-full border px-3 py-1 text-sm transition ${
                    page === result.url ? "border-indigo-500 bg-indigo-600/80 text-white" : "border-gray-600 bg-gray-800 text-gray-300 hover:border-gray-400 hover:text-white"
                  }`}
                  data-tpdb-page={result.id}
                >
                  {result.title}
                  {result.year ? ` (${result.year})` : ""}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {page && (
        <div className="mt-5">
          {posters.isPending ? (
            <Loading text="Loading posters…" />
          ) : posters.error || posters.data?.error ? (
            <Notice tone="red">{posters.error?.message ?? posters.data?.error}</Notice>
          ) : !posters.data?.images.length ? (
            <Empty title="No posters on this page" />
          ) : (
            <>
              <div className="flex flex-wrap gap-3">
                {posters.data.images.map((image) => (
                  <ImageChoice
                    key={image.token}
                    image={image}
                    caption={image.designer ?? image.label}
                    context={`ThePosterDB: ${image.label}${image.designer ? ` by ${image.designer}` : ""}`}
                    chosen={chosen}
                    onChoose={onChoose}
                  />
                ))}
              </div>
              <div className="mt-4">
                <ExternalButton href={page} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />}>
                  View at ThePosterDB
                </ExternalButton>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function CollectionPosterPicker({ collection, onClose }: { collection: Collection; onClose: () => void }) {
  const drawer = useDrawer(onClose);
  const run = useRunJob();
  const [tab, setTab] = useState<Tab>("tpdb");
  const [chosen, setChosen] = useState<Chosen | null>(null);

  const apply = () => {
    if (!chosen) return;
    const token = chosen.image.token;
    drawer.close();
    void run(
      () => unwrap(api.POST("/api/collections/{collection_id}/poster", { params: { path: { collection_id: collection.id } }, body: { token } })),
      "Setting poster…",
    );
  };

  const body = {
    tpdb: <TpdbTab collection={collection} chosen={chosen} onChoose={setChosen} />,
    link: <LinkTab kind="collection" chosen={chosen} onChoose={setChosen} />,
    file: <FileTab kind="collection" chosen={chosen} onChoose={setChosen} />,
  }[tab];

  return (
    <Drawer
      drawer={drawer}
      className="picker"
      heading={collection.title}
      header={
        <div role="tablist">
          <Tabs label="Image sources" items={TABS.map((item) => ({ key: item.key, label: item.label, active: tab === item.key, onClick: () => setTab(item.key) }))} />
        </div>
      }
      footer={<ChosenFooter chosen={chosen} label="Apply" onApply={apply} />}
    >
      {body}
    </Drawer>
  );
}
