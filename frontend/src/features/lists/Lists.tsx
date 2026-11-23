import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useListSources, useRuns } from "../../api/queries";
import { Button } from "../../components/Button";
import { Segmented } from "../../components/Controls";
import { Badge, Card, Empty, Loading, Notice, PageHeader, Section, Spinner } from "../../components/ui";
import { timeAgo } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { SOURCE_LABELS, type RunBrief } from "./common";

type Source = Schemas["ListSource"];
type Field = Schemas["SourceField"];
type Values = Record<string, unknown>;

const LAST_SOURCE = "programarr.lists.source";

function defaults(source: Source): Values {
  return Object.fromEntries(source.fields.map((field) => [field.key, field.default ?? (field.type === "multi" ? [] : "")]));
}

function visible(field: Field, values: Values): boolean {
  if (!field.when) return true;
  return Object.entries(field.when).every(([key, allowed]) => allowed.includes(String(values[key])));
}

function settingsLink(message: string): string {
  return message.includes("Settings › Sources") ? "/settings/sources" : "/settings/lists";
}

function FieldInput({ field, value, onChange }: { field: Field; value: unknown; onChange: (value: unknown) => void }) {
  const id = useId();
  const label = (
    <label htmlFor={id} className="mb-1 block text-xs font-semibold text-gray-400">
      {field.label}
    </label>
  );
  if (field.type === "multi") {
    const chosen = Array.isArray(value) ? (value as string[]) : [];
    return (
      <fieldset className="sm:col-span-2">
        <legend className="mb-1 block text-xs font-semibold text-gray-400">{field.label}</legend>
        <div className="flex flex-wrap gap-2">
          {field.options.map((option) => {
            const on = chosen.includes(option.value);
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={on}
                data-option={option.value}
                onClick={() => onChange(on ? chosen.filter((v) => v !== option.value) : [...chosen, option.value])}
                className={`rounded-full border px-3 py-1 text-sm transition ${
                  on ? "border-indigo-500 bg-indigo-600/80 text-white" : "border-gray-600 bg-gray-800 text-gray-300 hover:border-gray-400 hover:text-white"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </fieldset>
    );
  }
  if (field.type === "select") {
    return (
      <div>
        {label}
        <select id={id} name={field.key} className="field-input" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (field.type === "number") {
    return (
      <div>
        {label}
        <input
          id={id}
          name={field.key}
          type="number"
          className="field-input max-w-32"
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
        />
      </div>
    );
  }
  const listId = field.suggestions.length ? `${id}-suggestions` : undefined;
  return (
    <div className={field.placeholder && field.placeholder.startsWith("http") ? "sm:col-span-2" : ""}>
      {label}
      <input
        id={id}
        name={field.key}
        className="field-input"
        value={String(value ?? "")}
        placeholder={field.placeholder ?? undefined}
        list={listId}
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
      />
      {listId && (
        <datalist id={listId}>
          {field.suggestions.map((suggestion) => (
            <option key={suggestion} value={suggestion} />
          ))}
        </datalist>
      )}
    </div>
  );
}

function PreviewForm({ sources }: { sources: Source[] }) {
  const navigate = useNavigate();
  const run = useRunJob();
  const [sourceKey, setSourceKey] = useState(() => {
    const stored = window.localStorage.getItem(LAST_SOURCE);
    return sources.some((source) => source.key === stored) ? stored! : sources[0].key;
  });
  const source = sources.find((entry) => entry.key === sourceKey) ?? sources[0];
  const [values, setValues] = useState<Values>(() => defaults(source));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setValues(defaults(source));
    try {
      window.localStorage.setItem(LAST_SOURCE, source.key);
    } catch {
      // private mode: remembering the source is optional
    }
  }, [source]);

  const fields = useMemo(() => source.fields.filter((field) => visible(field, values)), [source, values]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    const job = await run(() => unwrap(api.POST("/api/lists/preview", { body: { source: source.key, params: values } })), "Preview…");
    setBusy(false);
    const runId = job?.result?.run;
    if (runId) navigate(`/lists/runs/${runId}`);
  };

  return (
    <Card className="p-5">
      <form onSubmit={submit} data-preview-form>
        <Segmented label="Source" value={source.key} onChange={setSourceKey} options={sources.map((entry) => ({ value: entry.key, label: entry.label }))} />
        {source.missing ? (
          <div className="mt-5">
            <Notice>
              <Link to={settingsLink(source.missing)} className="underline underline-offset-2">
                {source.missing}
              </Link>
            </Notice>
          </div>
        ) : (
          <>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              {fields.map((field) => (
                <FieldInput key={`${source.key}-${field.key}`} field={field} value={values[field.key]} onChange={(value) => setValues((current) => ({ ...current, [field.key]: value }))} />
              ))}
            </div>
            <div className="mt-5 flex items-center gap-3">
              <Button type="submit" variant="primary" disabled={busy} data-action="preview">
                {busy && <Spinner className="h-4 w-4" />}
                Preview
              </Button>
            </div>
          </>
        )}
      </form>
    </Card>
  );
}

export function RunCounts({ run }: { run: RunBrief }) {
  if (run.status === "running") return <Badge tone="indigo">Running</Badge>;
  if (run.status === "error") return <Badge tone="red">Failed</Badge>;
  const open = run.uncertain + run.failed;
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
      <span>
        <span className="font-semibold text-gray-200">{run.matched}</span> of {run.total} matched
      </span>
      {open > 0 && <Badge tone="yellow">{open} to check</Badge>}
      {run.excluded > 0 && <Badge tone="gray">{run.excluded} excluded</Badge>}
      {run.status === "partial" && <Badge tone="gray">Notes</Badge>}
    </span>
  );
}

function RecentRuns({ runs }: { runs: RunBrief[] }) {
  if (!runs.length) return <Empty title="No previews yet" />;
  return (
    <Card className="divide-y divide-gray-700">
      {runs.map((run) => (
        <Link key={run.id} to={`/lists/runs/${run.id}`} className="run-row flex flex-col gap-2 px-4 py-3 hover:bg-gray-700/30 sm:flex-row sm:items-center" data-run={run.id}>
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-gray-100">{run.label}</p>
            <p className="truncate text-xs text-gray-400">
              {SOURCE_LABELS[run.source] ?? run.source}, {timeAgo(run.started_at)}
              {run.status === "error" && run.error ? <span className="text-red-300">: {run.error}</span> : null}
            </p>
          </div>
          <RunCounts run={run} />
        </Link>
      ))}
    </Card>
  );
}

export function Lists() {
  useTitle("Lists");
  const sources = useListSources();
  const runs = useRuns();
  return (
    <>
      <PageHeader title="Lists" subtitle="Previews only. Nothing is written to Plex." />
      {sources.isPending ? (
        <Loading />
      ) : sources.error || !sources.data ? (
        <Notice tone="red">{sources.error?.message ?? "Cannot load the sources"}</Notice>
      ) : (
        <div className="max-w-4xl">
          <PreviewForm sources={sources.data.sources} />
        </div>
      )}
      <Section title="Recent previews" id="recent-runs">
        {runs.isPending ? <Loading /> : runs.error || !runs.data ? <Notice tone="red">{runs.error?.message ?? "Cannot load"}</Notice> : <RecentRuns runs={runs.data.runs} />}
      </Section>
    </>
  );
}
