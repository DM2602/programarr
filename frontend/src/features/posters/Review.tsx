import { ArrowTopRightOnSquareIcon, PlusIcon } from "@heroicons/react/24/outline";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, errorText, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useReviews } from "../../api/queries";
import { Button, ButtonLink, ExternalButton } from "../../components/Button";
import { Tabs } from "../../components/Controls";
import { useToast } from "../../components/Toasts";
import { Badge, Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { imageCaption, seasonLabel, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { ZoomImage } from "../../components/ZoomImage";
import { Coverage } from "./Coverage";
import { PickerDrawer } from "./Picker";

type ReviewView = Schemas["ReviewView"];

function Thumbs({ images }: { images: { src: string; large?: string | null; caption: string }[] }) {
  return (
    <div className="scroll-x flex gap-2 overflow-x-auto pb-1">
      {images.map((image, index) => (
        <figure key={`${image.src}-${index}`} className="w-20 shrink-0">
          <ZoomImage src={image.src} large={image.large} className="aspect-[2/3] w-20 rounded object-cover ring-1 ring-gray-700" />
          <figcaption className="mt-1 truncate text-center text-xs text-gray-400">{image.caption}</figcaption>
        </figure>
      ))}
    </div>
  );
}

function Compare({ before, after, pairs }: { before: string; after: string; pairs: Schemas["ComparePair"][] }) {
  return (
    <div className="mt-3 grid gap-2 sm:grid-cols-[6rem_minmax(0,1fr)] sm:items-center">
      <span className="text-xs font-semibold text-gray-400">{before}</span>
      <Thumbs images={pairs.map((pair) => ({ src: pair.current, caption: pair.label }))} />
      <span className="text-xs font-semibold text-gray-400">{after}</span>
      <Thumbs images={pairs.map((pair) => ({ src: pair.other, caption: pair.label }))} />
    </div>
  );
}

function useReviewActions(review: ReviewView) {
  const run = useRunJob();
  const toast = useToast();
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ predicate: (query) => !["sets", "settings", "auth"].includes(String(query.queryKey[0])) });
  return {
    dismiss: async () => {
      try {
        await unwrap(api.POST("/api/reviews/{review_id}/dismiss", { params: { path: { review_id: review.id } } }));
        toast.show("Dismissed", { kind: "success", timeout: 2500 });
        await refresh();
      } catch (error) {
        toast.show(errorText(error), { kind: "error" });
      }
    },
    confirm: async () => {
      try {
        const result = await unwrap(api.POST("/api/reviews/{review_id}/confirm", { params: { path: { review_id: review.id } } }));
        toast.show(result.message ?? "Linked", { kind: "success", timeout: 2500 });
        await refresh();
      } catch (error) {
        toast.show(errorText(error), { kind: "error" });
      }
    },
    bind: (token: string) =>
      run(
        () => unwrap(api.POST("/api/items/{key}/bind", { params: { path: { key: review.item_key } }, body: { token, replace_all: false, review: review.id } })),
        "Applying set…",
      ),
    accept: (take: boolean) =>
      run(
        () => unwrap(api.POST("/api/reviews/{review_id}/accept", { params: { path: { review_id: review.id }, query: { take } } })),
        take ? "Applying new posters…" : "Keeping current posters…",
      ),
    retry: () => run(() => unwrap(api.POST("/api/items/{key}/retry", { params: { path: { key: review.item_key } } })), "Searching sets…"),
    keepPlex: () =>
      run(() => unwrap(api.POST("/api/reviews/{review_id}/keep-plex", { params: { path: { review_id: review.id } } })), "Keeping Plex posters…"),
    force: (slot: string) =>
      run(() => unwrap(api.POST("/api/items/{key}/force", { params: { path: { key: review.item_key } }, body: { slot } })), "Switching Plex to the file…"),
  };
}

function currentSource(posters: ReviewView["current"]): string {
  const files = posters.filter((poster) => poster.file).length;
  return files === posters.length ? "In folder" : files ? "In folder and Plex" : "From Plex";
}

function pickerSlot(review: ReviewView): string {
  if (review.type === "movie") return `m:${review.item_key}`;
  if (review.kind === "new_season" && review.season !== null) return `S:${review.item_key}:${review.season}`;
  return `s:${review.item_key}`;
}

// Movies: one poster per designer, side by side; first what Plex shows, last the way to all sources.
function MovieCandidates({ review, onBind, onKeepPlex, onPick }: {
  review: ReviewView;
  onBind: (token: string) => void;
  onKeepPlex: () => void;
  onPick: () => void;
}) {
  const current = review.current[0];
  return (
    <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(7rem,1fr))] gap-x-3 gap-y-5 sm:grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] sm:gap-x-4">
      {current && (
        <div className="candidate current-candidate flex min-w-0 flex-col">
          <ZoomImage src={current.thumb} large={current.large} className="aspect-[2/3] w-full rounded-md object-cover ring-1 ring-gray-700" />
          <p className="mt-2 truncate text-sm font-semibold text-gray-100">{current.file ? "File in folder" : "Plex"}</p>
          <p className="truncate text-xs text-gray-400">Current poster</p>
          <div className="mt-auto pt-2">
            <Button size="sm" className="w-full" onClick={onKeepPlex} data-action="review-keep-plex">
              Keep
            </Button>
          </div>
        </div>
      )}
      {review.candidates.map((set) => {
        const image = set.images.find((candidate) => candidate.kind === "movie") ?? set.images[0];
        const setTitle = set.title && !set.title.startsWith(review.title) ? set.title : null;
        return (
          <div key={set.token} className="candidate flex min-w-0 flex-col" data-set={set.set_key}>
            {image ? (
              <ZoomImage src={image.thumb} large={image.large} className="aspect-[2/3] w-full rounded-md object-cover ring-1 ring-gray-700" />
            ) : (
              <div className="aspect-[2/3] w-full rounded-md bg-gray-900 ring-1 ring-gray-700" />
            )}
            <div className="mt-2 flex items-center gap-1">
              <span className="min-w-0 flex-1 truncate text-sm font-semibold text-gray-100" title={set.designer}>
                {set.designer}
              </span>
              {set.url && <ExternalButton href={set.url} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />} label={`View at ${set.source_label}`} />}
            </div>
            <p className="truncate text-xs text-gray-400" title={setTitle ?? undefined}>
              {set.source_label}
              {setTitle ? `, ${setTitle}` : ""}
            </p>
            {set.rank !== null && (
              <span className="mt-1">
                <Badge tone="green">Preferred</Badge>
              </span>
            )}
            <div className="mt-auto pt-2">
              <Button size="sm" variant="primary" className="w-full" onClick={() => onBind(set.token)} data-action="review-bind">
                Apply
              </Button>
            </div>
          </div>
        );
      })}
      <button
        type="button"
        onClick={onPick}
        className="flex aspect-[2/3] w-full flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-gray-600 text-sm font-medium text-gray-300 hover:border-indigo-400 hover:text-white focus-visible:border-indigo-400"
        data-action="review-pick"
      >
        <PlusIcon className="h-7 w-7" aria-hidden="true" />
        More posters
      </button>
    </div>
  );
}

function Candidates(props: { review: ReviewView; onBind: (token: string) => void; onKeepPlex: () => void; onPick: () => void }) {
  const { review, onBind, onKeepPlex } = props;
  const none = !review.candidates.length && (
    <p className="mt-3 text-sm text-gray-400">
      No sets found.
      {Object.entries(review.errors).map(([source, message]) => ` ${source}: ${message}.`)}
    </p>
  );
  if (review.type === "movie") {
    return (
      <>
        {none}
        <MovieCandidates {...props} />
      </>
    );
  }
  return (
    <div className="mt-3 space-y-3">
      {none}
      {review.current.length > 0 && (
        <div className="candidate current-candidate rounded-md border border-gray-700 bg-gray-900/40 p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-semibold text-gray-100">Current posters</span>
            <span className="text-sm text-gray-400">{currentSource(review.current)}</span>
          </div>
          <div className="mt-2">
            <Thumbs images={review.current.map((poster) => ({ src: poster.thumb, large: poster.large, caption: poster.label }))} />
          </div>
          <div className="mt-2">
            <Button size="sm" onClick={onKeepPlex} data-action="review-keep-plex">
              Keep
            </Button>
          </div>
        </div>
      )}
      {review.candidates.map((set) => {
        let images = set.images;
        if (review.kind === "new_season" && review.season !== null) {
          images = [...images].sort((a, b) => Number(!(a.kind === "season" && a.season === review.season)) - Number(!(b.kind === "season" && b.season === review.season)));
        }
        return (
          <div key={set.token} className="candidate rounded-md border border-gray-700 bg-gray-900/40 p-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-semibold text-gray-100">{set.designer}</span>
              <span className="text-sm text-gray-400">
                {set.source_label}, {set.title}
              </span>
              {set.rank !== null && <Badge tone="green">Preferred</Badge>}
              {review.type === "show" &&
                (set.loaded ? (
                  <Coverage seasons={review.seasons} covers={set.covers} missing={set.missing} hasMain={set.has_main} />
                ) : (
                  <Badge tone="gray">Seasons load when applied</Badge>
                ))}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <Thumbs images={images.map((image) => ({ src: image.thumb, large: image.large, caption: imageCaption(image) }))} />
              {set.more > 0 && <span className="text-xs text-gray-400">+{set.more}</span>}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" onClick={() => onBind(set.token)} data-action="review-bind">
                Apply set
              </Button>
              {set.url && (
                <ExternalButton href={set.url} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />}>
                  View
                </ExternalButton>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ReviewCard({ review }: { review: ReviewView }) {
  const actions = useReviewActions(review);
  const [picking, setPicking] = useState(false);
  const heading = (
    <Link to={`/item/${review.item_key}`} className="hover:underline">
      {review.title}
      {review.year ? ` (${review.year})` : ""}
    </Link>
  );
  const kindLine =
    review.kind === "detect"
      ? `${review.confidence === "high" ? "Certain" : "Uncertain, compare"}${review.via ? `, ${review.via}` : ""}`
      : `${review.kind_label}${review.season !== null ? `, ${seasonLabel(review.season)}` : ""}, ${timeAgo(review.created_at)}`;

  return (
    <Card className="review-card flex gap-4 p-4" data-review={review.id} data-kind={review.kind}>
      <Link to={`/item/${review.item_key}`} className="w-16 shrink-0 sm:w-20" tabIndex={-1} aria-hidden="true">
        <img src={review.thumb} alt="" loading="lazy" className="aspect-[2/3] w-full rounded-md object-cover ring-1 ring-gray-700" />
      </Link>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-gray-400">{kindLine}</p>
        <h2 className="mt-0.5 text-lg font-bold text-gray-100">{heading}</h2>
        {review.reason && <p className="mt-1 text-sm text-gray-300">{review.reason}</p>}

        {(review.kind === "new_item" || review.kind === "new_season") && (
          <>
            <Candidates review={review} onBind={(token) => void actions.bind(token)} onKeepPlex={() => void actions.keepPlex()} onPick={() => setPicking(true)} />
            {picking && <PickerDrawer itemKey={review.item_key} slotId={pickerSlot(review)} onClose={() => setPicking(false)} />}
            <div className="mt-3 flex flex-wrap gap-2">
              {review.type !== "movie" && (
                <Button size="sm" onClick={() => setPicking(true)} data-action="review-pick">
                  Select manually
                </Button>
              )}
              {review.kind === "new_item" && (
                <Button size="sm" variant="ghost" onClick={() => void actions.retry()}>
                  Search again
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={actions.dismiss}>
                Dismiss
              </Button>
            </div>
          </>
        )}

        {review.kind === "detect" && (
          <>
            <Compare before="In folder" after="In set" pairs={review.pairs} />
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" onClick={actions.confirm} data-action="review-confirm">
                Link
              </Button>
              {review.set?.url && (
                <ExternalButton href={review.set.url} icon={<ArrowTopRightOnSquareIcon className="h-4 w-4" />}>
                  View set
                </ExternalButton>
              )}
              <Button size="sm" variant="ghost" onClick={actions.dismiss}>
                Dismiss
              </Button>
            </div>
          </>
        )}

        {review.kind === "set_changed" && (
          <>
            <Compare before="Current" after="New" pairs={review.changes} />
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" onClick={() => void actions.accept(true)}>
                Apply new posters
              </Button>
              <Button size="sm" onClick={() => void actions.accept(false)}>
                Keep
              </Button>
            </div>
          </>
        )}

        {review.kind === "plex" && (
          <>
            <p className="mt-2 text-sm text-gray-400">The file is in the folder, but Plex shows another poster. Check "Use local assets" in the library's advanced settings in Plex.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {review.slot && (
                <Button size="sm" variant="primary" onClick={() => void actions.force(review.slot!)}>
                  Select file in Plex
                </Button>
              )}
              <ButtonLink to={`/item/${review.item_key}`} size="sm">
                Open title
              </ButtonLink>
              <Button size="sm" variant="ghost" onClick={actions.dismiss}>
                Dismiss
              </Button>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

export function Review() {
  useTitle("Review");
  const [params] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const { data, isPending, error } = useReviews(kind);

  const tabs = [
    { key: "", label: "All", to: "/review", active: kind === "", count: data?.total },
    ...(data?.kinds ?? [])
      .filter((entry) => entry.count > 0 || entry.key === kind)
      .map((entry) => ({ key: entry.key, label: entry.label, to: `/review?kind=${entry.key}`, active: kind === entry.key, count: entry.count })),
  ];

  return (
    <>
      <PageHeader
        title="Review"
        subtitle="Items that need a decision."
        actions={kind === "detect" && data?.reviews.length ? <ButtonLink to="/match">Link all certain matches</ButtonLink> : undefined}
      />
      <Tabs label="Kinds" items={tabs} />
      <div className="mt-6">
        {isPending ? (
          <Loading />
        ) : error || !data ? (
          <Notice tone="red">{error?.message ?? "Cannot load the review list"}</Notice>
        ) : data.reviews.length ? (
          <div className="space-y-4">
            {data.reviews.map((review) => (
              <ReviewCard key={review.id} review={review} />
            ))}
          </div>
        ) : (
          <Empty title="Nothing to review" />
        )}
      </div>
    </>
  );
}
