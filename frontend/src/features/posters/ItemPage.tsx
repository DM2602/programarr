import { CheckBadgeIcon, FilmIcon, MinusCircleIcon, PlayCircleIcon, RectangleStackIcon, TvIcon } from "@heroicons/react/20/solid";
import { ArrowPathIcon, ArrowTopRightOnSquareIcon } from "@heroicons/react/24/outline";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useItem } from "../../api/queries";
import { Button, ButtonLink, ExternalButton } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { useToast } from "../../components/Toasts";
import { Badge, Card, Empty, Loading, Notice, Section, type Tone } from "../../components/ui";
import { dateOnly, seasonLabel, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { HistoryList } from "./HistoryList";
import { Picker } from "./Picker";

type Detail = Schemas["ItemDetail"];
type Slot = Schemas["SlotView"];

const STATE_TONES: Record<Slot["state"], Tone> = { ok: "green", warn: "yellow", bad: "red", none: "gray" };

function SlotCard({ detail, slot, onPick }: { detail: Detail; slot: Slot; onPick: (id: string) => void }) {
  const run = useRunJob();
  const { confirm } = useDialogs();
  const key = detail.item.key;
  const origin = slot.file && slot.source_label ? [slot.source_label, slot.designer, slot.source !== "extern" ? slot.mode_label : null].filter(Boolean).join(", ") : null;

  const remove = async () => {
    const ok = await confirm({ title: `Remove the file for ${slot.label}?`, body: "The file moves to the archive. Plex shows its own poster again.", confirm: "Remove", danger: true });
    if (ok) await run(() => unwrap(api.POST("/api/items/{key}/remove", { params: { path: { key } }, body: { slot: slot.id } })), "Removing file…");
  };

  return (
    <div className={`slot-card ${slot.gone ? "opacity-60" : ""}`} data-slot={slot.id}>
      <button type="button" className="pick group block w-full text-left" onClick={() => onPick(slot.id)} aria-label={`Change poster for ${slot.label}`} data-slot={slot.id}>
        <div className="relative aspect-[2/3] overflow-hidden rounded-lg bg-gray-800 shadow-poster ring-1 ring-gray-700 transition duration-300 group-hover:ring-gray-500">
          <img src={slot.thumb} alt="" loading="lazy" className={`h-full w-full object-cover ${slot.file ? "" : "opacity-60 grayscale-[35%]"}`} />
          <div className="absolute top-2 left-2">
            {slot.waiting ? <Badge tone="indigo">Waiting</Badge> : slot.state === "bad" ? <Badge tone="red">Plex</Badge> : null}
          </div>
        </div>
      </button>
      <h3 className="mt-2 text-sm font-semibold text-gray-100">
        {slot.label}
        {slot.gone && <span className="font-normal text-gray-400"> (no longer in Plex)</span>}
      </h3>
      <p className="truncate text-xs text-gray-400" title={slot.file ?? undefined}>
        {slot.file ? `${slot.folder_name ? `${slot.folder_name}/` : ""}${slot.file_name}` : "No file"}
      </p>
      {origin && <p className="truncate text-xs text-gray-500">{origin}</p>}
      <div className="mt-1.5">
        {slot.waiting ? (
          <Badge tone="indigo">Waiting since {dateOnly(slot.waiting_since)}</Badge>
        ) : (
          <Badge tone={STATE_TONES[slot.state]}>{slot.state_text}</Badge>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button size="sm" onClick={() => onPick(slot.id)}>
          Change
        </Button>
        {slot.needs_force && (
          <Button size="sm" variant="ghost" onClick={() => run(() => unwrap(api.POST("/api/items/{key}/force", { params: { path: { key } }, body: { slot: slot.id } })), "Switching Plex to the file…")}>
            Select in Plex
          </Button>
        )}
        {slot.file && (
          <Button size="sm" variant="ghost" onClick={remove}>
            Remove
          </Button>
        )}
        {slot.waiting && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => run(() => unwrap(api.POST("/api/items/{key}/slots/stopwait", { params: { path: { key } }, body: { slot: slot.id } })), "Stop waiting…")}
          >
            Stop waiting
          </Button>
        )}
      </div>
    </div>
  );
}

function SetCard({ detail, onPick }: { detail: Detail; onPick: () => void }) {
  const run = useRunJob();
  const { confirm } = useDialogs();
  const key = detail.item.key;
  const binding = detail.binding;
  const unlink = async () => {
    const ok = await confirm({ title: "Unlink the set?", body: "The files stay. New seasons no longer come automatically.", confirm: "Unlink", danger: true });
    if (ok) await run(() => unwrap(api.POST("/api/items/{key}/unbind", { params: { path: { key } } })), "Unlinking…");
  };

  if (binding) {
    const state =
      binding.state === "gone" ? (
        <span className="font-semibold text-red-300">The set is no longer available.</span>
      ) : detail.complete ? (
        "Show complete, the set is no longer checked."
      ) : (
        `Checked ${timeAgo(binding.checked_at)}.`
      );
    return (
      <Card className={`set-card p-4 ${binding.state === "gone" ? "border-red-500/60" : ""}`}>
        <p className="text-xs font-semibold text-gray-400">Linked set</p>
        <h2 className="mt-1 font-bold text-gray-100" data-testid="linked-set">
          {binding.set_title}
        </h2>
        <p className="mt-1 text-sm text-gray-300">
          {binding.designer}, {binding.source_label}
        </p>
        <p className="mt-1 text-sm text-gray-400">{state}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" onClick={onPick}>
            Change set
          </Button>
          <Button size="sm" onClick={() => run(() => unwrap(api.POST("/api/items/{key}/check", { params: { path: { key } } })), "Checking set…")}>
            Check set
          </Button>
          {binding.set_url && (
            <ExternalButton href={binding.set_url} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />}>
              {binding.source_label}
            </ExternalButton>
          )}
          <Button size="sm" variant="ghost" onClick={unlink}>
            Unlink
          </Button>
        </div>
      </Card>
    );
  }
  return (
    <Card className="set-card p-4">
      <p className="text-xs font-semibold text-gray-400">Set</p>
      <h2 className="mt-1 font-bold text-gray-100">{detail.complete ? "Complete" : "No set linked"}</h2>
      <p className="mt-1 text-sm text-gray-400">{detail.complete ? "The show has ended and every poster is in the folder." : "Pick a set to sync automatically."}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" variant={detail.complete ? "default" : "primary"} onClick={onPick} data-action="pick-set">
          Pick a set
        </Button>
        {detail.has_files && !detail.complete && (
          <Button
            size="sm"
            title="Finds the set the posters in the folder came from"
            onClick={() => run(() => unwrap(api.POST("/api/items/{key}/detect", { params: { path: { key } } })), "Comparing posters…")}
          >
            Match existing
          </Button>
        )}
      </div>
    </Card>
  );
}

function DetailsCard({ detail }: { detail: Detail }) {
  const run = useRunJob();
  const toast = useToast();
  const { prompt } = useDialogs();
  const key = detail.item.key;
  const setTpdb = async () => {
    const url = await prompt({
      title: "ThePosterDB page",
      label: "Link of the title page",
      initial: detail.item.tpdb_url ?? "",
      placeholder: "https://theposterdb.com/posters/12345",
      confirm: "Save",
      type: "url",
    });
    if (!url) return;
    await run(() => unwrap(api.POST("/api/items/{key}/tpdb", { params: { path: { key } }, body: { url } })), "Saving…");
  };
  const folderDiffers = detail.item.folder && detail.item.local_folder !== detail.item.folder;
  return (
    <Card className="p-4 text-sm">
      <p className="text-xs font-semibold text-gray-400">Folder</p>
      <p className="mt-1 font-mono text-xs break-all text-gray-200">{detail.item.local_folder ?? "unknown"}</p>
      {folderDiffers && (
        <>
          <p className="mt-3 text-xs font-semibold text-gray-400">In Plex</p>
          <p className="mt-1 font-mono text-xs break-all text-gray-300">{detail.item.folder}</p>
        </>
      )}
      {detail.links.length > 0 && (
        <>
          <p className="mt-4 text-xs font-semibold text-gray-400">Links</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {detail.links.map((link) => (
              <ExternalButton key={link.label} href={link.url}>
                {link.label}
              </ExternalButton>
            ))}
          </div>
        </>
      )}
      <div className="mt-4 flex flex-wrap gap-2 border-t border-gray-700 pt-4">
        <Button size="sm" variant="ghost" title="Read the poster files in the folder again" onClick={() => run(() => unwrap(api.POST("/api/items/{key}/rescan", { params: { path: { key } } })), "Rescanning folder…")}>
          Rescan folder
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setTpdb().catch((error) => toast.show(String(error), { kind: "error" }))}>
          Set ThePosterDB page
        </Button>
      </div>
    </Card>
  );
}

function Header({ detail, onPick }: { detail: Detail; onPick: () => void }) {
  const run = useRunJob();
  const { item } = detail;
  const main = detail.slots[0];
  const isShow = item.type === "show";
  const seasons = `${detail.seasons} ${detail.seasons === 1 ? "season" : "seasons"}${detail.specials ? " and specials" : ""}`;
  return (
    <div className="relative -mx-4 overflow-hidden md:-mx-8">
      {main && <img src={main.thumb} alt="" aria-hidden="true" className="absolute inset-0 h-full w-full scale-125 object-cover opacity-40 blur-3xl" />}
      <div className="absolute inset-0 bg-gradient-to-b from-gray-900/30 via-gray-900/70 to-gray-900" />
      <div className="relative flex flex-col items-center gap-6 px-4 pt-8 pb-6 md:flex-row md:items-end md:px-8">
        <div className="w-36 shrink-0 md:w-44">
          {main && (
            <div className="aspect-[2/3] overflow-hidden rounded-lg shadow-2xl ring-1 ring-gray-600">
              <img src={main.thumb} alt={`Current poster of ${item.title}`} className={`h-full w-full object-cover ${main.file ? "" : "opacity-70"}`} />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1 text-center md:text-left">
          <h1 className="text-2xl font-bold text-white md:text-4xl">
            {item.title}
            {item.year && <span className="ml-2 font-normal text-gray-400">({item.year})</span>}
          </h1>
          <div className="mt-3 flex flex-wrap items-center justify-center gap-2 md:justify-start">
            <Badge tone={isShow ? "purple" : "blue"} icon={isShow ? <TvIcon className="h-3.5 w-3.5" /> : <FilmIcon className="h-3.5 w-3.5" />}>
              {isShow ? "Show" : "Movie"}
            </Badge>
            {isShow && <Badge tone="gray">{seasons}</Badge>}
            {detail.complete ? (
              <Badge tone="green" icon={<CheckBadgeIcon className="h-3.5 w-3.5" />} title="Ended, every poster is a file">
                Complete
              </Badge>
            ) : item.show_status === "ended" ? (
              <Badge tone="gray" icon={<MinusCircleIcon className="h-3.5 w-3.5" />}>
                Ended
              </Badge>
            ) : item.show_status === "running" ? (
              <Badge tone="gray" icon={<PlayCircleIcon className="h-3.5 w-3.5" />}>
                Running
              </Badge>
            ) : null}
            {detail.library && (
              <Link to={`/library?library=${encodeURIComponent(detail.library.key)}`} className="rounded-full hover:opacity-90" title="Plex library">
                <Badge tone="dark" icon={<RectangleStackIcon className="h-3.5 w-3.5" />}>
                  {detail.library.title}
                </Badge>
              </Link>
            )}
          </div>
          {(item.collection_name || (item.search_title && item.search_title !== item.title)) && (
            <p className="mt-2 text-sm text-gray-400">
              {item.collection_name && <span>Collection {item.collection_name}</span>}
              {item.collection_name && item.search_title && item.search_title !== item.title && <span>, </span>}
              {item.search_title && item.search_title !== item.title && <span>English title {item.search_title}</span>}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap justify-center gap-2">
          <Button variant="primary" onClick={onPick}>
            {detail.binding ? "Change set" : "Pick a set"}
          </Button>
          <Button
            icon={<ArrowPathIcon className="h-4 w-4" />}
            title="Like Refresh Metadata in Plex"
            onClick={() => run(() => unwrap(api.POST("/api/items/{key}/refresh", { params: { path: { key: item.key } } })), "Refreshing metadata…")}
          >
            Refresh Metadata
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ItemPage() {
  const { key = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { data, isPending, error } = useItem(key);
  const [picking, setPicking] = useState<string | null>(null);
  useTitle(data?.item.title ?? "Title");

  // Waiting links here with #pick=<slot> to open the picker for that season.
  useEffect(() => {
    if (!data || !location.hash.startsWith("#pick=")) return;
    setPicking(decodeURIComponent(location.hash.slice(6)));
    navigate(location.pathname, { replace: true });
  }, [data, location.hash, location.pathname, navigate]);

  if (isPending) return <Loading />;
  if (error || !data) {
    return (
      <div className="mt-16">
        <Empty title={error?.message === "Title not found" ? "Title not found" : "Cannot load this title"} action={<ButtonLink to="/library">Open the library</ButtonLink>}>
          {error?.message !== "Title not found" ? error?.message : "It may no longer be in Plex."}
        </Empty>
      </div>
    );
  }
  const main = data.slots[0];
  const openMain = () => main && setPicking(main.id);

  return (
    <>
      <Header detail={data} onPick={openMain} />
      {data.item.gone && (
        <div className="mt-4">
          <Notice>Plex no longer has this title.</Notice>
        </div>
      )}
      {data.reviews.map((review) => (
        <div key={review.id} className="mt-4">
          <Notice>
            {review.kind_label}
            {review.season !== null ? `, ${seasonLabel(review.season)}` : ""}: {review.reason}{" "}
            <Link to={`/review?kind=${review.kind}`} className="font-semibold underline underline-offset-2">
              Review
            </Link>
          </Notice>
        </div>
      ))}
      <div className="mt-2 grid gap-x-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0">
          <Section title="Posters" id="posters">
            <div className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-x-4 gap-y-8">
              {data.slots.map((slot) => (
                <SlotCard key={slot.id} detail={data} slot={slot} onPick={setPicking} />
              ))}
            </div>
          </Section>
          <Section title="History" id="item-history">
            {data.history.length ? <HistoryList rows={data.history} /> : <p className="text-sm text-gray-400">No changes yet.</p>}
          </Section>
        </div>
        <aside className="mt-10 space-y-4">
          <SetCard detail={data} onPick={openMain} />
          <DetailsCard detail={data} />
        </aside>
      </div>
      {picking && <Picker detail={data} slotId={picking} onClose={() => setPicking(null)} />}
    </>
  );
}
