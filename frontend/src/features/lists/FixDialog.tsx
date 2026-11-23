import { useState } from "react";
import { api, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useMediaSearch } from "../../api/queries";
import { Button } from "../../components/Button";
import { Segmented } from "../../components/Controls";
import { Modal } from "../../components/Dialogs";
import { Spinner } from "../../components/ui";
import { KIND_LABELS, POSTER_FALLBACK, type Candidate } from "./common";

type Target = { title: string; year?: number | null; kind?: string | null; candidates: Candidate[] };

/** Search TMDB and pick the title. Used to fix a match and to exclude a title. */
export function TitleSearch({ target, confirm, onPick, onClose, heading }: {
  target: Target;
  confirm: string;
  heading: string;
  onPick: (candidate: Candidate) => Promise<unknown>;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(target.title);
  const [kind, setKind] = useState(target.kind ?? "movie");
  const [submitted, setSubmitted] = useState<{ q: string; kind: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const search = useMediaSearch({ q: submitted?.q ?? "", kind: submitted?.kind ?? kind }, !!submitted);
  const results = submitted ? search.data?.results ?? [] : target.candidates;

  const pick = async (candidate: Candidate) => {
    setBusy(candidate.tmdb_id);
    try {
      await onPick(candidate);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      title={heading}
      onClose={onClose}
      onSubmit={() => setSubmitted({ q: query.trim(), kind })}
      footer={<Button onClick={onClose}>Cancel</Button>}
    >
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex gap-2">
          <input
            className="field-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Title or TMDB ID"
            aria-label="Title or TMDB ID"
            autoFocus
          />
          <Button type="submit" variant="primary">
            Search
          </Button>
        </div>
        <div>
          <Segmented
            label="Type"
            value={kind}
            onChange={(value) => {
              setKind(value);
              if (submitted) setSubmitted({ ...submitted, kind: value });
            }}
            options={[
              { value: "movie", label: "Movie" },
              { value: "show", label: "Show" },
            ]}
          />
        </div>
        {target.year && <p className="text-xs text-gray-400">In the list: {target.title}, {target.year}</p>}
        <div className="-mx-5 max-h-96 overflow-y-auto border-t border-gray-700" data-results>
          {submitted && search.isFetching ? (
            <div className="flex items-center gap-2 px-5 py-6 text-sm text-gray-400">
              <Spinner className="h-4 w-4" /> Searching…
            </div>
          ) : search.error && submitted ? (
            <p className="px-5 py-6 text-sm text-red-300">{search.error.message}</p>
          ) : results.length ? (
            results.map((candidate) => (
              <div key={`${candidate.kind}-${candidate.tmdb_id}`} className="candidate flex items-center gap-3 px-5 py-2.5 hover:bg-gray-700/40">
                <img src={candidate.poster ?? POSTER_FALLBACK} alt="" loading="lazy" className="aspect-[2/3] w-10 shrink-0 rounded object-cover ring-1 ring-gray-700" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-gray-100">
                    {candidate.title} <span className="font-normal text-gray-400">{candidate.year ?? "?"}</span>
                  </p>
                  <p className="truncate text-xs text-gray-500">
                    {KIND_LABELS[candidate.kind]}
                    {candidate.original_title && candidate.original_title !== candidate.title ? `, ${candidate.original_title}` : ""}
                    {`, TMDB ${candidate.tmdb_id}`}
                  </p>
                </div>
                <Button size="sm" onClick={() => pick(candidate)} disabled={busy !== null} data-tmdb={candidate.tmdb_id}>
                  {busy === candidate.tmdb_id ? <Spinner className="h-4 w-4" /> : confirm}
                </Button>
              </div>
            ))
          ) : (
            <p className="px-5 py-6 text-sm text-gray-400">{submitted ? "Nothing found." : "Search TMDB."}</p>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Fix the match of a list entry: this raw title is that TMDB title, now and in future runs. */
export function FixDialog({ entry, onClose }: {
  entry: { id: number; title: string; year?: number | null; kind?: string | null; candidates: Candidate[] };
  onClose: () => void;
}) {
  const run = useRunJob();
  return (
    <TitleSearch
      target={entry}
      heading={`Match ${entry.title}`}
      confirm="Use"
      onClose={onClose}
      onPick={async (candidate) => {
        await run(
          () => unwrap(api.POST("/api/lists/entries/{entry_id}/fix", { params: { path: { entry_id: entry.id } }, body: { kind: candidate.kind, tmdb_id: candidate.tmdb_id } })),
          "Saving match…",
          { onDone: () => onClose() },
        );
      }}
    />
  );
}
