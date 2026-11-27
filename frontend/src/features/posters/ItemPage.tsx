import { CheckBadgeIcon, FilmIcon, MinusCircleIcon, PlayCircleIcon, Squares2X2Icon, TvIcon } from "@heroicons/react/20/solid";
import { ArrowPathIcon, ArrowTopRightOnSquareIcon } from "@heroicons/react/24/outline";
import { useEffect, useState } from "react";
import { siRadarr, siSonarr } from "simple-icons";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useItem, useItemArr, useItemMissing } from "../../api/queries";
import { BrandIcon } from "../../components/BrandIcon";
import { Button, ButtonLink, ExternalButton, buttonClass } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
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
    <div className={`slot-card flex flex-col ${slot.gone ? "opacity-60" : ""}`} data-slot={slot.id}>
      <button type="button" className="pick group block w-full text-left" onClick={() => onPick(slot.id)} aria-label={`Change poster for ${slot.label}`} data-slot={slot.id}>
        <div className="relative aspect-[2/3] overflow-hidden rounded-lg bg-gray-800 shadow-poster ring-1 ring-gray-700 transition duration-300 group-hover:ring-gray-500">
          <img src={slot.thumb} alt="" loading="lazy" className={`h-full w-full object-cover ${slot.file ? "" : "opacity-60 grayscale-[35%]"}`} />
          <div className="absolute top-2 left-2">
            {slot.pending ? <Badge tone="indigo">Pending</Badge> : slot.state === "bad" ? <Badge tone="red">Plex</Badge> : null}
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
      {/* Always one line, so badges and buttons line up across the cards */}
      <p className="min-h-4 truncate text-xs text-gray-500" title={origin ?? undefined}>
        {origin}
      </p>
      <div className="mt-1.5">
        {slot.pending ? (
          <Badge tone="indigo">Pending since {dateOnly(slot.pending_since)}</Badge>
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
        {!slot.file && !slot.gone && (
          <Button
            size="sm"
            variant="ghost"
            title="Save the current Plex poster as file"
            onClick={() => run(() => unwrap(api.POST("/api/items/{key}/keep", { params: { path: { key } }, body: { slot: slot.id } })), "Keeping Plex poster…")}
            data-action="keep"
          >
            Keep
          </Button>
        )}
        {slot.pending && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => run(() => unwrap(api.POST("/api/items/{key}/slots/dismiss", { params: { path: { key } }, body: { slot: slot.id } })), "Dismissing…")}
          >
            Dismiss
          </Button>
        )}
      </div>
    </div>
  );
}

type Missing = Schemas["MissingSeason"];

function airDate(date: string): string {
  return new Date(`${date}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** A season TMDB or Sonarr knows that Plex does not have yet: greyed out, no poster to set. */
function MissingSeasonCard({ season }: { season: Missing }) {
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = !season.air_date || season.air_date > today;
  const facts = [season.episodes ? `${season.episodes} ${season.episodes === 1 ? "episode" : "episodes"}` : null, season.air_date && !upcoming ? season.air_date.slice(0, 4) : null]
    .filter(Boolean)
    .join(", ");
  const state = upcoming
    ? season.air_date
      ? `Airs ${airDate(season.air_date)}`
      : "Not aired yet"
    : season.sonarr === "monitored"
      ? "Monitored in Sonarr"
      : season.sonarr === "unmonitored"
        ? "Not monitored"
        : season.sonarr === "absent"
          ? "Not in Sonarr"
          : null;
  return (
    <div className="slot-card flex flex-col" data-missing={season.number}>
      <div className="relative aspect-[2/3] overflow-hidden rounded-lg border border-dashed border-gray-700 bg-gray-800/40">
        {season.poster && <img src={season.poster} alt="" loading="lazy" className="h-full w-full object-cover opacity-30 grayscale" />}
      </div>
      <h3 className="mt-2 text-sm font-semibold text-gray-400">{seasonLabel(season.number)}</h3>
      <p className="text-xs text-gray-500">Not in Plex</p>
      <p className="min-h-4 truncate text-xs text-gray-600">{facts}</p>
      <div className="mt-1.5">{state && <Badge tone="gray">{state}</Badge>}</div>
    </div>
  );
}

function SetCard({ detail, onPick }: { detail: Detail; onPick: () => void }) {
  const run = useRunJob();
  const { confirm } = useDialogs();
  const key = detail.item.key;
  const binding = detail.binding;
  const unlink = async () => {
    const ok = await confirm({ title: "Unlink the set?", body: "Files are kept. New seasons are no longer set automatically.", confirm: "Unlink", danger: true });
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
  const { prompt } = useDialogs();
  const key = detail.item.key;
  const setTpdb = async () => {
    const url = await prompt({
      title: "ThePosterDB page",
      label: "Title page URL",
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
      {detail.item.search_title && detail.item.search_title !== detail.item.title && (
        <>
          <p className="mt-4 text-xs font-semibold text-gray-400">Poster search title</p>
          <p className="mt-1 text-gray-200">{detail.item.search_title}</p>
          <p className="mt-0.5 text-xs text-gray-500">English title from TMDB. ThePosterDB lists titles under it.</p>
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
        <Button size="sm" variant="ghost" onClick={setTpdb}>
          Set ThePosterDB page
        </Button>
      </div>
    </Card>
  );
}

/** Open the title in Radarr or Sonarr, or add it there. Nothing when the service is not set up. */
function ArrButton({ itemKey }: { itemKey: string }) {
  const { data } = useItemArr(itemKey);
  if (!data?.service || !data.url) {
    return data?.error ? <span className="self-center text-xs text-red-300" title={data.error}>{data.label} unreachable</span> : null;
  }
  const state = data.present ? (data.downloaded ? "Downloaded" : data.monitored ? "Monitored, missing files" : "Not monitored") : "Not added yet";
  const dot = data.present ? (data.downloaded ? "bg-green-400" : data.monitored ? "bg-yellow-400" : "bg-gray-500") : null;
  return (
    <a
      href={data.url}
      target="_blank"
      rel="noopener noreferrer"
      className={buttonClass("default")}
      title={`${data.label}: ${state}`}
      data-arr={data.service}
      data-present={data.present}
    >
      <BrandIcon icon={data.service === "radarr" ? siRadarr : siSonarr} />
      {data.present ? data.label : `Add to ${data.label}`}
      {dot && <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden="true" />}
      <span className="sr-only">{state}</span>
    </a>
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
              <Badge tone="green" icon={<CheckBadgeIcon className="h-3.5 w-3.5" />} title="Ended, all posters are files">
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
              <Link to={`/library?library=${encodeURIComponent(detail.library.key)}`} className="inline-flex rounded-full hover:opacity-90" title="Plex library">
                <Badge tone="dark" icon={<Squares2X2Icon className="h-3.5 w-3.5" />}>
                  {detail.library.title}
                </Badge>
              </Link>
            )}
          </div>
          {item.collection_name && <p className="mt-2 text-sm text-gray-400">Collection {item.collection_name}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap justify-center gap-2">
          <ArrButton itemKey={item.key} />
          <Button variant="primary" onClick={onPick}>
            {detail.binding ? "Change set" : "Pick a set"}
          </Button>
          <Button
            icon={<ArrowPathIcon className="h-4 w-4" />}
            title="Same as Refresh Metadata in Plex"
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
  const missing = useItemMissing(key, data?.item.type === "show");
  const [picking, setPicking] = useState<string | null>(null);
  useTitle(data?.item.title ?? "Title");

  // Links with #pick=<slot> open the picker for that season.
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

  // Plex' slots and the seasons Plex does not have yet, in season order (the show poster first).
  type Card = { order: number; slot?: Slot; missing?: Missing };
  const cards: Card[] = [
    ...data.slots.map((slot): Card => ({ order: slot.kind === "season" ? (slot.season ?? 0) : -1, slot })),
    ...(missing.data?.seasons ?? []).map((season): Card => ({ order: season.number, missing: season })),
  ].sort((a, b) => a.order - b.order || (a.slot ? -1 : 1));

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
              {cards.map((card) =>
                card.slot ? (
                  <SlotCard key={card.slot.id} detail={data} slot={card.slot} onPick={setPicking} />
                ) : (
                  <MissingSeasonCard key={`missing-${card.missing!.number}`} season={card.missing!} />
                ),
              )}
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
