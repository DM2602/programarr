import { CheckIcon } from "@heroicons/react/20/solid";
import { ArrowTopRightOnSquareIcon, PhotoIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useLayoutEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { api, errorText, unwrap, uploadImage, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useItem, useOnlineImages, usePlexImages, useSets } from "../../api/queries";
import { Button, ExternalButton } from "../../components/Button";
import { Tabs } from "../../components/Controls";
import { useToast } from "../../components/Toasts";
import { Badge, Empty, Loading, Notice } from "../../components/ui";
import { useFocusTrap } from "../../components/useFocusTrap";
import { ZoomImage } from "../../components/ZoomImage";
import { hostOf, imageCaption } from "../../lib/format";
import { Coverage } from "./Coverage";

type Detail = Schemas["ItemDetail"];
type Slot = Schemas["SlotView"];
type Image = Schemas["ImageView"];
type SetView = Schemas["SetView"];
type Tab = "sets" | "plex" | "online" | "link" | "file";
type Chosen = { image: Image; context: string };

const TABS: { key: Tab; label: string }[] = [
  { key: "sets", label: "Sets" },
  { key: "plex", label: "Plex" },
  { key: "online", label: "Online" },
  { key: "link", label: "Link" },
  { key: "file", label: "File" },
];

function matchesSlot(slot: Slot, image: Image): boolean {
  if (slot.kind === "season") return image.kind === "season" && image.season === slot.season;
  return image.kind === slot.kind;
}

function ImageChoice({ image, caption, context, chosen, onChoose, match = false, current = false, width = "w-28", showCaption = true }: {
  image: Image;
  caption?: string;
  context: string;
  chosen: Chosen | null;
  onChoose: (choice: Chosen) => void;
  match?: boolean;
  current?: boolean;
  width?: string;
  showCaption?: boolean;
}) {
  const selected = chosen?.image.token === image.token;
  return (
    <button
      type="button"
      className={`image-choice group ${width} shrink-0 text-left disabled:cursor-default`}
      aria-pressed={selected}
      data-match={match || undefined}
      title={current ? `${context} (current file)` : context}
      onClick={() => onChoose({ image, context })}
    >
      <span
        className={`relative block aspect-[2/3] overflow-hidden rounded-md bg-gray-900 ring-offset-2 ring-offset-gray-800 transition ${
          selected ? "ring-2 ring-white" : match ? "ring-2 ring-indigo-500" : "ring-1 ring-gray-700 group-hover:ring-gray-400"
        }`}
      >
        <ZoomImage src={image.thumb} large={image.large} className="h-full w-full object-cover" />
        {selected && (
          <span className="absolute top-1 right-1 rounded-full bg-white p-0.5 text-gray-900">
            <CheckIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
        )}
      </span>
      {showCaption && (
        <span className={`mt-1 block truncate text-xs ${match ? "font-semibold text-indigo-300" : "text-gray-400"}`}>
          {current ? "File in folder" : caption || image.label || " "}
        </span>
      )}
    </button>
  );
}

function movieImage(set: SetView): Image | undefined {
  return set.images.find((image) => image.kind === "movie");
}

// Movies: one poster per designer, side by side.
function MovieSetTile({ set, title, chosen, onChoose, onApplySet }: {
  set: SetView;
  title: string;
  chosen: Chosen | null;
  onChoose: (choice: Chosen) => void;
  onApplySet: (set: SetView) => void;
}) {
  const image = movieImage(set)!;
  const setTitle = set.title && !set.title.startsWith(title) ? set.title : null;
  return (
    <div className="set-tile flex min-w-0 flex-col" data-set={set.set_key}>
      <ImageChoice image={image} context={`Poster from ${set.title} by ${set.designer}`} chosen={chosen} onChoose={onChoose} width="w-full" showCaption={false} />
      <div className="mt-2 flex items-center gap-1">
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-gray-100" title={set.designer}>
          {set.designer || "Unknown designer"}
        </span>
        {set.url && <ExternalButton href={set.url} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />} label={`View at ${set.source_label}`} />}
      </div>
      <p className="truncate text-xs text-gray-400" title={setTitle ?? undefined}>
        {set.source_label}
        {setTitle ? `, ${setTitle}` : ""}
      </p>
      {(set.bound || set.rank !== null || set.hidden) && (
        <p className="mt-1 flex flex-wrap gap-1">
          {set.bound && <Badge tone="indigo">Linked</Badge>}
          {set.rank !== null && <Badge tone="green">Preferred</Badge>}
          {set.hidden && <Badge tone="red">Hidden at MediUX</Badge>}
        </p>
      )}
      <Button size="sm" variant="primary" className="mt-2" onClick={() => onApplySet(set)} data-action="apply-set">
        Apply and link
      </Button>
    </div>
  );
}

function SetBlock({ itemKey, set, data, slot, itemType, chosen, onChoose, onApplySet, onLoaded }: {
  itemKey: string;
  set: SetView;
  data: Schemas["SetsResponse"];
  slot: Slot;
  itemType: string;
  chosen: Chosen | null;
  onChoose: (choice: Chosen) => void;
  onApplySet: (set: SetView) => void;
  onLoaded: (set: SetView) => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const [loading, setLoading] = useState(false);
  const firstMatch = set.images.findIndex((image) => matchesSlot(slot, image));

  // Long sets scroll sideways: bring the poster being changed into view.
  useLayoutEffect(() => {
    const strip = stripRef.current;
    const target = strip?.querySelector<HTMLElement>("[data-match]");
    if (!strip || !target) return;
    const left = target.offsetLeft - strip.offsetLeft - (strip.clientWidth - target.offsetWidth) / 2;
    if (left > 0) strip.scrollLeft = left;
  }, [set.token]);

  const loadWhole = async () => {
    setLoading(true);
    try {
      const full = await unwrap(api.POST("/api/items/{key}/sets/full", { params: { path: { key: itemKey } }, body: { token: set.token } }));
      onLoaded({ ...full, rank: set.rank, bound: set.bound });
    } catch (error) {
      toast.show(errorText(error), { kind: "error" });
      setLoading(false);
    }
  };

  return (
    <section className="set-block border-b border-gray-700 py-5 first:pt-1 last:border-b-0" data-set={set.set_key}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-gray-100">{set.designer || "Unknown designer"}</h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-400">
            <span>{set.source_label}</span>
            <span className="text-gray-300">{set.title}</span>
            {set.bound && <Badge tone="indigo">Linked</Badge>}
            {set.rank !== null && <Badge tone="green">Preferred</Badge>}
            {set.hidden && <Badge tone="red">Hidden at MediUX</Badge>}
          </p>
          {itemType === "show" && (
            <div className="mt-2">
              {set.source === "tpdb" && !set.loaded ? (
                <Badge tone="gray">Seasons not loaded</Badge>
              ) : (
                <Coverage seasons={data.seasons} covers={set.covers} missing={set.missing} hasMain={set.has_main} />
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {set.images.length > 0 && (
            <Button size="sm" variant="primary" onClick={() => onApplySet(set)} data-action="apply-set">
              {itemType === "show" ? "Apply set" : "Apply and link"}
            </Button>
          )}
          {set.source === "tpdb" && !set.loaded && (
            <Button size="sm" onClick={loadWhole} disabled={loading}>
              {loading ? "Loading…" : "Load full set"}
            </Button>
          )}
          {set.url && (
            <ExternalButton href={set.url} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />}>
              View
            </ExternalButton>
          )}
        </div>
      </div>
      <div ref={stripRef} className="scroll-x mt-3 flex gap-3 overflow-x-auto pb-2">
        {set.images.map((image, index) => (
          <ImageChoice
            key={image.token}
            image={image}
            caption={imageCaption(image)}
            context={`${imageCaption(image)} from ${set.title} by ${set.designer}`}
            chosen={chosen}
            onChoose={onChoose}
            match={index === firstMatch}
          />
        ))}
      </div>
    </section>
  );
}

function SetsTab({ detail, slot, chosen, onChoose, onApplySet }: {
  detail: Detail;
  slot: Slot;
  chosen: Chosen | null;
  onChoose: (choice: Chosen) => void;
  onApplySet: (set: SetView) => void;
}) {
  const key = detail.item.key;
  const queryClient = useQueryClient();
  const { data, isPending, error, isFetching } = useSets(key, true);
  const [searching, setSearching] = useState(false);
  if (isPending) return <Loading text="Searching sets…" />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load sets"}</Notice>;

  const searchAgain = async () => {
    setSearching(true);
    try {
      const fresh = await unwrap(api.GET("/api/items/{key}/sets", { params: { path: { key }, query: { fresh: true } } }));
      queryClient.setQueryData(["sets", key], fresh);
    } finally {
      setSearching(false);
    }
  };
  const replace = (index: number, set: SetView) => {
    queryClient.setQueryData<Schemas["SetsResponse"]>(["sets", key], (old) => (old ? { ...old, sets: old.sets.map((s, i) => (i === index ? set : s)) } : old));
  };

  return (
    <div>
      {Object.entries(data.errors).map(([source, message]) => (
        <div key={source} className="mb-3">
          <Notice>
            {source}: {message}
          </Notice>
        </div>
      ))}
      {data.sets.length === 0 ? (
        <Empty title="No sets found">Set the ThePosterDB page under Details, or use another tab.</Empty>
      ) : (
        <>
          {detail.item.type === "movie" && data.sets.some(movieImage) && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(8rem,1fr))] gap-x-4 gap-y-6 pt-1 pb-5">
              {data.sets.filter(movieImage).map((set) => (
                <MovieSetTile key={`${set.source}-${set.set_key}`} set={set} title={detail.item.title} chosen={chosen} onChoose={onChoose} onApplySet={onApplySet} />
              ))}
            </div>
          )}
          {data.sets.map((set, index) => detail.item.type === "movie" && movieImage(set) ? null : (
          <SetBlock
            key={`${set.source}-${set.set_key}`}
            itemKey={key}
            set={set}
            data={data}
            slot={slot}
            itemType={detail.item.type}
            chosen={chosen}
            onChoose={onChoose}
            onApplySet={onApplySet}
            onLoaded={(full) => replace(index, full)}
          />
          ))}
        </>
      )}
      <div className="mt-4">
        <Button size="sm" variant="ghost" onClick={searchAgain} disabled={searching || isFetching}>
          {searching ? "Searching…" : "Search again"}
        </Button>
      </div>
    </div>
  );
}

function PlexTab({ detail, slot, chosen, onChoose }: { detail: Detail; slot: Slot; chosen: Chosen | null; onChoose: (choice: Chosen) => void }) {
  const { data, isPending, error } = usePlexImages(detail.item.key, slot.id, true);
  if (isPending) return <Loading text="Loading posters from Plex…" />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot load posters from Plex"}</Notice>;
  if (!data.images.length) return <Empty title="No posters in Plex" />;
  return (
    <div className="flex flex-wrap gap-3">
      {data.images.map((image) => (
        <ImageChoice
          key={image.token}
          image={image}
          caption={image.selected && !image.local ? "Shown in Plex" : image.label}
          context={image.local ? "File in folder" : `Plex: ${image.label}`}
          chosen={chosen}
          onChoose={onChoose}
          current={image.local}
        />
      ))}
    </div>
  );
}

function OnlineTab({ detail, slot, chosen, onChoose }: { detail: Detail; slot: Slot; chosen: Chosen | null; onChoose: (choice: Chosen) => void }) {
  const { data, isPending, error } = useOnlineImages(detail.item.key, slot.id, true);
  if (isPending) return <Loading text="Searching TMDB, fanart.tv and TVmaze…" />;
  if (error || !data) return <Notice tone="red">{error?.message ?? "Cannot reach the online sources"}</Notice>;
  return (
    <div className="space-y-6">
      {data.sources.map((source) => (
        <div key={source.key}>
          <h3 className="mb-2 font-semibold text-gray-100">{source.label}</h3>
          {!source.enabled ? (
            <p className="text-sm text-gray-400">{source.key === "tvmaze" ? "Off (Settings › Sources)" : "No key (Settings › Sources)"}</p>
          ) : source.error ? (
            <Notice>{source.error}</Notice>
          ) : !source.images.length ? (
            <p className="text-sm text-gray-400">{source.key === "tvmaze" && detail.item.type === "movie" ? "Shows only" : "No posters"}</p>
          ) : (
            <div className="flex flex-wrap gap-3">
              {source.images.map((image) => (
                <ImageChoice key={image.token} image={image} caption={image.label} context={`${source.label}: ${image.label}`} chosen={chosen} onChoose={onChoose} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function LinkTab({ slot, chosen, onChoose }: { slot: Slot; chosen: Chosen | null; onChoose: (choice: Chosen) => void }) {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<{ busy: boolean; error: string | null; image: Image | null }>({ busy: false, error: null, image: null });
  const id = useId();
  const load = async (event: FormEvent) => {
    event.preventDefault();
    const value = url.trim();
    if (!value) return;
    setState({ busy: true, error: null, image: null });
    try {
      const image = await unwrap(api.POST("/api/fetch-url", { body: { url: value, kind: slot.kind, season: slot.season } }));
      setState({ busy: false, error: null, image });
      onChoose({ image, context: `Image from ${hostOf(value)}` });
    } catch (error) {
      setState({ busy: false, error: errorText(error), image: null });
    }
  };
  return (
    <form onSubmit={load}>
      <label htmlFor={id} className="mb-1 block text-sm font-semibold text-gray-300">
        Image address
      </label>
      <div className="flex gap-2">
        <input id={id} type="url" className="field-input" placeholder="https://…/poster.jpg" value={url} onChange={(event) => setUrl(event.target.value)} autoFocus />
        <Button type="submit" disabled={state.busy || !url.trim()}>
          Load
        </Button>
      </div>
      <p className="mt-1 text-xs text-gray-400">JPEG, PNG or WebP</p>
      <div className="mt-4">
        {state.busy && <Loading text="Loading image…" />}
        {state.error && <Notice tone="red">{state.error}</Notice>}
        {state.image && <ImageChoice image={state.image} context={`Image from ${hostOf(url)}`} chosen={chosen} onChoose={onChoose} />}
      </div>
    </form>
  );
}

function FileTab({ slot, chosen, onChoose }: { slot: Slot; chosen: Chosen | null; onChoose: (choice: Chosen) => void }) {
  const [state, setState] = useState<{ busy: boolean; error: string | null; image: Image | null; name: string }>({ busy: false, error: null, image: null, name: "" });
  const [over, setOver] = useState(false);
  const send = async (file: File | undefined) => {
    if (!file) return;
    setState({ busy: true, error: null, image: null, name: file.name });
    try {
      const image = await uploadImage(file, slot.kind, slot.season);
      setState({ busy: false, error: null, image, name: file.name });
      onChoose({ image, context: file.name });
    } catch (error) {
      setState({ busy: false, error: errorText(error), image: null, name: file.name });
    }
  };
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    void send(event.dataTransfer.files[0]);
  };
  return (
    <div>
      <input id="upload-input" type="file" accept="image/*" className="sr-only" onChange={(event) => void send(event.target.files?.[0])} />
      <label
        htmlFor="upload-input"
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition ${
          over ? "border-indigo-400 bg-indigo-500/10" : "border-gray-600 hover:border-gray-400"
        }`}
      >
        <PhotoIcon className="h-8 w-8 text-gray-400" aria-hidden="true" />
        <span className="font-semibold text-gray-200">Upload file</span>
        <span className="text-xs text-gray-400">JPEG, PNG or WebP</span>
      </label>
      <div className="mt-4">
        {state.busy && <Loading text="Uploading…" />}
        {state.error && <Notice tone="red">{state.error}</Notice>}
        {state.image && <ImageChoice image={state.image} context={state.name} chosen={chosen} onChoose={onChoose} />}
      </div>
    </div>
  );
}

/** The picker for a title that is not open: loads the title, then slides in over the page. */
export function PickerDrawer({ itemKey, slotId, onClose }: { itemKey: string; slotId: string; onClose: () => void }) {
  const { data } = useItem(itemKey);
  return data ? <Picker detail={data} slotId={slotId} onClose={onClose} /> : null;
}

export function Picker({ detail, slotId, onClose }: { detail: Detail; slotId: string; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const run = useRunJob();
  const [entered, setEntered] = useState(false);
  const [tab, setTab] = useState<Tab>("sets");
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const slot = detail.slots.find((s) => s.id === slotId) ?? detail.slots[0];
  const key = detail.item.key;
  const isShow = detail.item.type === "show";

  const close = () => {
    setEntered(false);
    window.setTimeout(onClose, 220);
  };
  useFocusTrap(panelRef, true, close);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setEntered(true));
    panelRef.current?.querySelector<HTMLElement>("[data-close]")?.focus();
    return () => cancelAnimationFrame(frame);
  }, []);

  if (!slot) return null;
  const heading = slot.kind === "season" ? `${detail.item.title}, ${slot.label}` : detail.item.title;
  const target = slot.kind === "season" ? slot.label : isShow ? "the show" : "the movie";

  const keepFile = chosen?.image.local === true;
  const apply = () => {
    if (!chosen) return;
    const token = chosen.image.token;
    close();
    if (keepFile) {
      void run(() => unwrap(api.POST("/api/items/{key}/keep", { params: { path: { key } }, body: { slot: slot.id } })), "Keeping file…");
      return;
    }
    void run(() => unwrap(api.POST("/api/items/{key}/apply", { params: { path: { key } }, body: { slot: slot.id, token } })), "Setting poster…");
  };
  const applySet = (set: SetView) => {
    close();
    void run(() => unwrap(api.POST("/api/items/{key}/bind", { params: { path: { key } }, body: { token: set.token, replace_all: true } })), `Applying set ${set.title}…`);
  };

  const body = {
    sets: <SetsTab detail={detail} slot={slot} chosen={chosen} onChoose={setChosen} onApplySet={applySet} />,
    plex: <PlexTab detail={detail} slot={slot} chosen={chosen} onChoose={setChosen} />,
    online: <OnlineTab detail={detail} slot={slot} chosen={chosen} onChoose={setChosen} />,
    link: <LinkTab slot={slot} chosen={chosen} onChoose={setChosen} />,
    file: <FileTab slot={slot} chosen={chosen} onChoose={setChosen} />,
  }[tab];

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className={`absolute inset-0 bg-gray-900/70 transition-opacity duration-200 ${entered ? "opacity-100" : "opacity-0"}`} onClick={close} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`picker absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col bg-gray-800 shadow-2xl ring-1 ring-gray-700 transition-transform duration-200 ease-out ${
          entered ? "translate-x-0" : "translate-x-full"
        }`}
        data-open={entered || undefined}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div className="min-w-0">
            <h2 id={titleId} className="truncate text-lg font-bold text-gray-100">
              {heading}
            </h2>
            {slot.kind !== "season" && isShow && <p className="text-sm text-gray-400">Pick one image or apply the whole set.</p>}
          </div>
          <button type="button" data-close onClick={close} className="rounded-md p-1 text-gray-400 hover:bg-gray-700 hover:text-white" aria-label="Close">
            <XMarkIcon className="h-6 w-6" />
          </button>
        </div>
        <div className="px-5" role="tablist">
          <Tabs label="Image sources" items={TABS.map((item) => ({ key: item.key, label: item.label, active: tab === item.key, onClick: () => setTab(item.key) }))} />
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{body}</div>
        <div className="flex items-center gap-3 border-t border-gray-700 bg-gray-800 px-5 py-3">
          <div className="chosen flex min-w-0 flex-1 items-center gap-3" aria-live="polite">
            {chosen ? (
              <>
                <img src={chosen.image.thumb} alt="" className="h-14 w-10 shrink-0 rounded object-cover ring-1 ring-gray-600" />
                <span className="truncate text-sm text-gray-300">{chosen.context}</span>
              </>
            ) : (
              <span className="text-sm text-gray-500">Nothing selected</span>
            )}
          </div>
          <Button variant="primary" disabled={!chosen} onClick={apply} data-action="apply-image">
            {keepFile ? "Keep the file" : `Apply to ${target}`}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
