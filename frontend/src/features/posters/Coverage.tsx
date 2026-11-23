import { TvIcon } from "@heroicons/react/20/solid";
import { seasonLabel, seasonShort } from "../../lib/format";

/** Which posters a set has for a show: the show poster, then one mark per season in Plex. */
export function Coverage({ seasons, covers, missing, hasMain }: { seasons: number[]; covers: number[]; missing: number[]; hasMain: boolean }) {
  const gaps = [...(hasMain ? [] : ["show poster"]), ...missing.map((n) => seasonLabel(n))];
  const label = gaps.length ? `Missing: ${gaps.join(", ")}` : "Every poster";
  const shown = seasons.filter((n) => covers.includes(n) || missing.includes(n));
  const mark = (has: boolean) =>
    `inline-flex h-6 min-w-6 items-center justify-center rounded px-1 text-xs font-semibold ${
      has ? "bg-green-600/80 text-white" : "border border-dashed border-gray-500 text-gray-400 line-through"
    }`;
  return (
    <span className="coverage inline-flex flex-wrap items-center gap-1" role="img" aria-label={label} title={label}>
      <span className={mark(hasMain)} data-has={hasMain}>
        <TvIcon className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
      {shown.map((n) => {
        const has = covers.includes(n);
        return (
          <span key={n} className={mark(has)} data-season={n} data-has={has}>
            {seasonShort(n)}
          </span>
        );
      })}
    </span>
  );
}
