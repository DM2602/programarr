import { ArrowPathIcon, PlusIcon } from "@heroicons/react/24/outline";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { siRadarr, siSonarr } from "simple-icons";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useExclusions } from "../../api/queries";
import { BrandIcon, MonoBrandIcon } from "../../components/BrandIcon";
import { Button } from "../../components/Button";
import { Segmented } from "../../components/Controls";
import { useDialogs } from "../../components/Dialogs";
import { Pager } from "../../components/Pager";
import { Badge, Card, Empty, Loading, Notice, PageHeader } from "../../components/ui";
import { dateOnly, timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { KindIcon, POSTER_FALLBACK, Ratings } from "./common";
import { TitleSearch } from "./FixDialog";

type Row = Schemas["ExclusionView"];
type Service = NonNullable<Row["service"]>;

const SERVICE_LABELS: Record<Service, string> = { radarr: "Radarr", sonarr: "Sonarr" };
const PER_PAGE = 50;

const rowKey = (row: Row) => (row.id !== null ? String(row.id) : `${row.service}-${row.external_id}`);

function syncLine(services: Service[], syncedAt: number | null): string {
  if (!services.length) return "Excluded from all lists.";
  const names = services.map((service) => SERVICE_LABELS[service]).join(" and ");
  return syncedAt ? `Excluded from all lists. Synced with ${names} ${timeAgo(syncedAt)}.` : `Excluded from all lists. Not synced with ${names} yet.`;
}

export function Exclusions() {
  useTitle("Exclusions");
  const { data, isPending, error } = useExclusions();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const run = useRunJob();
  const dialogs = useDialogs();

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.rows ?? []).filter((row) => (!kind || row.kind === kind) && (!needle || row.title.toLowerCase().includes(needle)));
  }, [data, kind, query]);
  const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  const shown = rows.slice((Math.min(page, pages) - 1) * PER_PAGE, Math.min(page, pages) * PER_PAGE);

  const remove = (row: Row) => {
    if (row.id !== null) {
      const id = row.id;
      return run(() => unwrap(api.DELETE("/api/exclusions/{exclusion_id}", { params: { path: { exclusion_id: id } } })), "Removing…");
    }
    if (!row.service || row.external_id === null) return;
    const path = { service: row.service, external_id: row.external_id };
    return run(() => unwrap(api.DELETE("/api/exclusions/arr/{service}/{external_id}", { params: { path } })), "Removing…");
  };
  const sync = () => run(() => unwrap(api.POST("/api/exclusions/sync")), "Syncing…");
  const services = data?.services ?? [];

  return (
    <>
      <PageHeader
        title="Exclusions"
        subtitle={syncLine(services, data?.synced_at ?? null)}
        actions={
          <>
            {services.length > 0 && (
              <Button icon={<ArrowPathIcon className="h-4 w-4" />} onClick={() => void sync()} data-action="sync-exclusions">
                Sync
              </Button>
            )}
            <Button variant="primary" icon={<PlusIcon className="h-4 w-4" />} onClick={() => setAdding(true)} data-action="add-exclusion">
              Add title
            </Button>
          </>
        }
      />
      {isPending ? (
        <Loading />
      ) : error || !data ? (
        <Notice tone="red">{error?.message ?? "Cannot load"}</Notice>
      ) : (
        <>
          {data.errors.length > 0 && (
            <div className="mb-4" data-sync-errors>
              <Notice tone="red">
                {data.errors.map((message) => (
                  <p key={message}>{message}</p>
                ))}
              </Notice>
            </div>
          )}
          {data.rows.length ? (
            <>
              <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <Segmented
                  label="Type"
                  value={kind}
                  onChange={(value) => {
                    setKind(value);
                    setPage(1);
                  }}
                  options={[
                    { value: "", label: "All" },
                    { value: "movie", label: "Movies" },
                    { value: "show", label: "Shows" },
                  ]}
                />
                <input
                  className="field-input md:w-64"
                  type="search"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Title"
                  aria-label="Filter by title"
                />
              </div>
              {shown.length ? (
                <Card className="divide-y divide-gray-700">
                  {shown.map((row) => (
                    <ExclusionRow key={rowKey(row)} row={row} onRemove={() => void remove(row)} />
                  ))}
                </Card>
              ) : (
                <Empty title="No matches" />
              )}
              <Pager page={Math.min(page, pages)} pages={pages} onPage={setPage} />
            </>
          ) : (
            <Empty title="No exclusions" />
          )}
        </>
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

function ExclusionRow({ row, onRemove }: { row: Row; onRemove: () => void }) {
  const service = row.service ? SERVICE_LABELS[row.service] : null;
  const fromService = row.id === null && row.service ? row.service : null;
  const serviceIcon = row.service === "sonarr" ? siSonarr : siRadarr;
  return (
    <div
      className="exclusion flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center"
      data-exclusion={rowKey(row)}
      data-service={row.service ?? undefined}
      data-pending={row.pending || undefined}
    >
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <img src={row.media?.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-11 shrink-0 rounded object-cover ring-1 ring-gray-700" />
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-x-2">
            <KindIcon kind={row.kind} />
            <span className="font-semibold text-gray-100">{row.title}</span>
            <span className="text-sm text-gray-400">{row.year ?? "?"}</span>
            {row.plex_key && (
              <Link to={`/item/${row.plex_key}`} className="text-xs font-semibold text-green-300 hover:underline">
                In Plex
              </Link>
            )}
          </p>
          <p className="flex items-center gap-1.5 text-sm text-gray-400">
            {fromService && (
              <span title={`From ${SERVICE_LABELS[fromService]}`} className="inline-flex">
                <BrandIcon icon={serviceIcon} />
                <span className="sr-only">From {SERVICE_LABELS[fromService]}</span>
              </span>
            )}
            <span>
              {!fromService && row.reason ? `${row.reason}, ` : ""}
              {dateOnly(row.created_at)}
            </span>
          </p>
          {row.media && <Ratings media={row.media} />}
        </div>
      </div>
      <div className="flex items-center gap-3 self-end sm:self-auto">
        {service &&
          (row.pending ? (
            <Badge tone="yellow" title={`Written to ${service} at the next sync`} icon={<MonoBrandIcon icon={serviceIcon} />}>
              {service} pending
            </Badge>
          ) : (
            <Badge tone="gray" title={`On the ${service} exclusion list`} icon={<BrandIcon icon={serviceIcon} className="h-3.5 w-3.5" />}>
              {service}
            </Badge>
          ))}
        <Button size="sm" variant="ghost" onClick={onRemove}>
          Remove
        </Button>
      </div>
    </div>
  );
}
