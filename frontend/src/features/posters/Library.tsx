import { CheckBadgeIcon, XMarkIcon } from "@heroicons/react/20/solid";
import { Link, useSearchParams } from "react-router";
import type { Schemas } from "../../api/client";
import { useLibrary } from "../../api/queries";
import { ButtonLink } from "../../components/Button";
import { Badge, Empty, Loading, Notice, PageHeader, Spinner, type Tone } from "../../components/ui";
import { Pager } from "../../components/Pager";
import { Segmented, SelectField } from "../../components/Controls";
import { useTitle } from "../../lib/useTitle";

const FLAGS: Record<string, { label: string; tone: Tone }> = {
  problem: { label: "Plex", tone: "red" },
  review: { label: "Review", tone: "yellow" },
  waiting: { label: "Waiting", tone: "indigo" },
  new: { label: "New", tone: "green" },
};

export function TitleCard({ card }: { card: Schemas["Card"] }) {
  const flag = card.flag ? FLAGS[card.flag] : null;
  const meta = [card.year, card.designer ? `${card.designer} (${card.source_label})` : card.complete ? "complete" : card.files ? "no set" : null]
    .filter(Boolean)
    .join(", ");
  return (
    <Link to={`/item/${card.key}`} className="title-card group block" data-title={card.title}>
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl bg-gray-800 shadow-poster ring-1 ring-gray-700 transition duration-300 group-hover:scale-[1.03] group-hover:ring-gray-500 group-focus-visible:ring-indigo-400">
        <img
          src={card.thumb}
          alt=""
          loading="lazy"
          width={200}
          height={300}
          className={`h-full w-full object-cover ${card.has_file ? "" : "opacity-60 grayscale-[35%]"}`}
        />
        <div className="absolute inset-x-0 top-0 flex items-start justify-between gap-1 p-2">
          <Badge tone={card.type === "movie" ? "blue" : "purple"}>{card.type === "movie" ? "Movie" : "Show"}</Badge>
          {flag && <Badge tone={flag.tone}>{flag.label}</Badge>}
        </div>
        {card.complete && (
          <span className="absolute right-2 bottom-2 rounded-full bg-gray-900/80 p-0.5 text-green-400" title="Complete: ended, every poster is a file">
            <CheckBadgeIcon className="h-6 w-6" aria-label="Complete" />
          </span>
        )}
      </div>
      <p className="mt-2 truncate text-sm font-semibold text-gray-100" title={card.title}>
        {card.title}
      </p>
      <p className="truncate text-xs text-gray-400">{meta || " "}</p>
    </Link>
  );
}

export function Library() {
  useTitle("Library");
  const [params, setParams] = useSearchParams();
  const query = {
    type: params.get("type") ?? "",
    status: params.get("status") ?? "all",
    q: params.get("q") ?? "",
    library_key: params.get("library") ?? "",
    sort: params.get("sort") ?? "title",
    page: Number(params.get("page") ?? "1") || 1,
  };
  const { data, isPending, isFetching, error } = useLibrary(query);

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      const isDefault = !value || (key === "status" && value === "all") || (key === "sort" && value === "title") || (key === "page" && value === "1");
      if (isDefault) next.delete(key);
      else next.set(key, value);
    }
    if (!("page" in changes)) next.delete("page");
    setParams(next);
  };

  const filterLabel = data?.filters.find((filter) => filter.key === query.status)?.label;
  const subtitle = data
    ? [
        `${data.total} ${data.total === 1 ? "title" : "titles"}`,
        query.status !== "all" && filterLabel ? `filter ${filterLabel}` : null,
      ]
        .filter(Boolean)
        .join(", ")
    : " ";

  return (
    <>
      <PageHeader title="Library" subtitle={subtitle} />
      <div className="mb-6 flex flex-wrap items-end gap-3">
        <Segmented
          label="Type"
          value={query.type}
          onChange={(type) => update({ type })}
          options={[
            { value: "", label: "All" },
            { value: "movie", label: "Movies" },
            { value: "show", label: "Shows" },
          ]}
        />
        <SelectField
          label="Filter"
          id="filter-status"
          value={query.status}
          onChange={(status) => update({ status })}
          options={(data?.filters ?? [{ key: "all", label: "All" }]).map((filter) => ({ value: filter.key, label: filter.label }))}
        />
        {data && data.libraries.length > 2 && (
          <SelectField
            label="Plex library"
            id="filter-library"
            value={query.library_key}
            onChange={(library) => update({ library })}
            options={[{ value: "", label: "All libraries" }, ...data.libraries.map((lib) => ({ value: lib.key, label: lib.title }))]}
          />
        )}
        <SelectField
          label="Sort"
          id="filter-sort"
          value={query.sort}
          onChange={(sort) => update({ sort })}
          options={[
            { value: "title", label: "Title" },
            { value: "added", label: "Recently added" },
          ]}
        />
        {query.q && (
          <button
            type="button"
            onClick={() => update({ q: "" })}
            className="inline-flex items-center gap-1 rounded-full bg-gray-700 py-1 pr-2 pl-3 text-sm text-gray-100 hover:bg-gray-600"
            aria-label={`Clear search ${query.q}`}
          >
            Search: {query.q}
            <XMarkIcon className="h-4 w-4" />
          </button>
        )}
        {isFetching && !isPending && <Spinner className="h-5 w-5 text-gray-400" />}
      </div>

      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load the library"}</Notice>
      ) : data.cards.length ? (
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-x-4 gap-y-6" data-testid="library-grid">
            {data.cards.map((card) => (
              <TitleCard key={card.key} card={card} />
            ))}
          </div>
          <Pager page={data.page} pages={data.pages} onPage={(page) => update({ page: String(page) })} />
        </>
      ) : data.empty_library ? (
        <Empty title="No titles yet" action={<ButtonLink to="/settings/plex">Plex settings</ButtonLink>}>
          Pick libraries under Settings › Plex, then sync.
        </Empty>
      ) : (
        <Empty title="No titles match" action={<ButtonLink to="/library">Clear filters</ButtonLink>}>
          Change the filter or the search.
        </Empty>
      )}
    </>
  );
}
