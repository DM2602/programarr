import { XMarkIcon } from "@heroicons/react/20/solid";
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { useArrOptions, useMdblistLists } from "../../api/queries";
import type { NodeField } from "./common";

type Values = Record<string, unknown>;
type Option = { value: string; label: string };

const regions = safeNames("region");
const languages = safeNames("language");

function safeNames(type: "region" | "language"): Intl.DisplayNames | null {
  try {
    return new Intl.DisplayNames(["en"], { type });
  } catch {
    return null;
  }
}

function codeName(kind: string | null | undefined, code: string): string {
  const names = kind === "countries" ? regions : kind === "languages" ? languages : null;
  if (!names) return code;
  try {
    const name = names.of(code);
    return name && name !== code ? name : code;
  } catch {
    return code;
  }
}

export function fieldDefaults(fields: NodeField[]): Values {
  return Object.fromEntries(fields.map((field) => [field.key, field.default ?? (field.type === "multi" || field.type === "tokens" ? [] : field.type === "bool" ? false : "")]));
}

export function visible(field: NodeField, values: Values): boolean {
  if (!field.when) return true;
  return Object.entries(field.when).every(([key, allowed]) => allowed.includes(String(values[key] ?? "")));
}

/** Options or suggestions a field loads from Radarr, Sonarr or MDBList. */
function useDynamic(dynamic: string | null | undefined): { options: Option[]; loading: boolean; error: string | null } {
  const [service, what] = (dynamic ?? "").split(".");
  const arr = service === "radarr" || service === "sonarr" ? service : null;
  const options = useArrOptions(arr ?? "radarr", !!arr);
  const lists = useMdblistLists(dynamic === "mdblist.lists");
  return useMemo(() => {
    if (arr) {
      const data = options.data;
      const error = options.error?.message ?? data?.error ?? (data && !data.configured ? "Not set up" : null);
      let list: Option[] = [];
      if (data && what === "profiles") list = data.profiles.map((row) => ({ value: String(row.id), label: row.name }));
      if (data && what === "root_folders") list = data.root_folders.map((row) => ({ value: row.path, label: row.path }));
      if (data && what === "tags") list = data.tags.map((row) => ({ value: row.label, label: row.label }));
      return { options: list, loading: options.isPending, error };
    }
    if (dynamic === "mdblist.lists") {
      const list = (lists.data?.lists ?? []).map((row) => ({ value: `${row.user}/${row.slug}`, label: `${row.name} (${row.user}/${row.slug})` }));
      return { options: list, loading: lists.isPending && lists.fetchStatus !== "idle", error: null };
    }
    return { options: [], loading: false, error: null };
  }, [arr, what, dynamic, options.data, options.error, options.isPending, lists.data, lists.isPending, lists.fetchStatus]);
}

function Label({ htmlFor, children }: { htmlFor?: string; children: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block text-xs font-semibold text-gray-400">
      {children}
    </label>
  );
}

function Toggle({ field, value, onChange }: { field: NodeField; value: unknown; onChange: (value: unknown) => void }) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex cursor-pointer items-center justify-between gap-3 py-1 text-sm text-gray-200">
      {field.label}
      <input id={id} type="checkbox" className="peer sr-only" checked={!!value} onChange={(event) => onChange(event.target.checked)} data-field={field.key} />
      <span
        aria-hidden="true"
        className="relative h-5 w-9 shrink-0 rounded-full bg-gray-600 transition peer-checked:bg-indigo-600 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-indigo-400 after:absolute after:top-0.5 after:left-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-4"
      />
    </label>
  );
}

function Tokens({ field, value, onChange }: { field: NodeField; value: unknown; onChange: (value: unknown) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const dynamic = useDynamic(field.dynamic);
  const items = Array.isArray(value) ? (value as string[]) : [];
  const suggestions: Option[] = field.dynamic && field.dynamic.includes(".")
    ? dynamic.options
    : field.suggestions.map((code) => ({ value: code, label: codeName(field.dynamic, code) }));
  const add = (raw: string) => {
    const entry = raw.trim();
    if (!entry || items.includes(entry)) return setText("");
    onChange([...items, entry]);
    setText("");
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      add(text);
    } else if (event.key === "Backspace" && !text && items.length) {
      onChange(items.slice(0, -1));
    }
  };
  return (
    <div>
      <Label htmlFor={id}>{field.label}</Label>
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-gray-600 bg-gray-700 px-2 py-1.5 focus-within:border-indigo-500 focus-within:ring-1 focus-within:ring-indigo-500">
        {items.map((item) => (
          <span key={item} className="token inline-flex items-center gap-1 rounded bg-gray-600 py-0.5 pr-1 pl-2 text-xs text-gray-100" data-token={item}>
            {codeName(field.dynamic, item) !== item ? `${codeName(field.dynamic, item)} (${item})` : item}
            <button type="button" className="rounded text-gray-300 hover:text-white" onClick={() => onChange(items.filter((other) => other !== item))} aria-label={`Remove ${item}`}>
              <XMarkIcon className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={text}
          list={`${id}-list`}
          placeholder={items.length ? "" : field.placeholder ?? ""}
          autoComplete="off"
          data-field={field.key}
          className="min-w-24 flex-1 bg-transparent py-0.5 text-sm text-white placeholder:text-gray-400 focus:outline-none"
          onChange={(event) => {
            const next = event.target.value;
            // A pick from the list arrives without "insertText": take it right away.
            const kind = (event.nativeEvent as InputEvent).inputType;
            if ((!kind || kind === "insertReplacementText") && suggestions.some((option) => option.value === next)) add(next);
            else setText(next);
          }}
          onKeyDown={onKey}
          onBlur={() => add(text)}
        />
        <datalist id={`${id}-list`}>
          {suggestions
            .filter((option) => !items.includes(option.value))
            .map((option) => (
              <option key={option.value} value={option.value}>
                {option.label !== option.value ? option.label : undefined}
              </option>
            ))}
        </datalist>
      </div>
      {dynamic.error && field.dynamic?.includes(".") && <p className="mt-1 text-xs text-yellow-200">{dynamic.error}</p>}
    </div>
  );
}

function DynamicSelect({ field, value, onChange }: { field: NodeField; value: unknown; onChange: (value: unknown) => void }) {
  const id = useId();
  const { options, loading, error } = useDynamic(field.dynamic);
  const current = value === null || value === undefined ? "" : String(value);
  // A new node takes the first profile or folder, like Radarr's add dialog.
  useEffect(() => {
    if (!current && options.length) onChange(field.dynamic?.endsWith("profiles") ? Number(options[0].value) : options[0].value);
  }, [current, options, onChange, field.dynamic]);
  const known = options.some((option) => option.value === current);
  return (
    <div>
      <Label htmlFor={id}>{field.label}</Label>
      <select
        id={id}
        className="field-input"
        value={current}
        data-field={field.key}
        disabled={loading && !options.length}
        onChange={(event) => onChange(field.dynamic?.endsWith("profiles") ? Number(event.target.value) : event.target.value)}
      >
        {!known && <option value={current}>{current ? `${current} (not found)` : loading ? "Loading…" : "Choose"}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error && <p className="mt-1 text-xs text-yellow-200">{error}</p>}
    </div>
  );
}

export function ConfigField({ field, value, onChange }: { field: NodeField; value: unknown; onChange: (value: unknown) => void }) {
  const id = useId();
  if (field.type === "bool") return <Toggle field={field} value={value} onChange={onChange} />;
  if (field.type === "tokens") return <Tokens field={field} value={value} onChange={onChange} />;
  if (field.type === "select" && field.dynamic) return <DynamicSelect field={field} value={value} onChange={onChange} />;
  if (field.type === "multi") {
    const chosen = Array.isArray(value) ? (value as string[]) : [];
    return (
      <fieldset>
        <legend className="mb-1 block text-xs font-semibold text-gray-400">{field.label}</legend>
        <div className="flex flex-wrap gap-1.5">
          {field.options.map((option) => {
            const on = chosen.includes(option.value);
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={on}
                data-option={option.value}
                onClick={() => onChange(on ? chosen.filter((v) => v !== option.value) : [...chosen, option.value])}
                className={`rounded-full border px-2.5 py-0.5 text-xs transition ${
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
        <Label htmlFor={id}>{field.label}</Label>
        <select id={id} className="field-input" value={String(value ?? "")} data-field={field.key} onChange={(event) => onChange(event.target.value)}>
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (field.type === "number" || field.type === "decimal") {
    return (
      <div>
        <Label htmlFor={id}>{field.label}</Label>
        <input
          id={id}
          type="number"
          className="field-input max-w-36"
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          step={field.step ?? (field.type === "decimal" ? 0.1 : 1)}
          value={value === null || value === undefined ? "" : String(value)}
          data-field={field.key}
          onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
        />
      </div>
    );
  }
  const listId = field.suggestions.length ? `${id}-suggestions` : undefined;
  return (
    <div>
      <Label htmlFor={id}>{field.label}</Label>
      <input
        id={id}
        className="field-input"
        value={String(value ?? "")}
        placeholder={field.placeholder ?? undefined}
        list={listId}
        autoComplete="off"
        data-field={field.key}
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
