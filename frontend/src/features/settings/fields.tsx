import { CheckCircleIcon, ExclamationCircleIcon } from "@heroicons/react/20/solid";
import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api, errorText, unwrap, type Schemas } from "../../api/client";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/ui";

export type Values = Schemas["SettingsValues"];
export type Key = keyof Values;
/** Changed fields only. Secrets: a string sets a new value, false clears the stored one. */
export type Draft = Partial<Record<Key, unknown>>;

export function useSettingsForm(values: Values) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>({});
  const [result, setResult] = useState<Result | null>(null);
  const [saving, setSaving] = useState(false);

  function get<K extends Key>(key: K): Values[K] {
    return (key in draft ? draft[key] : values[key]) as Values[K];
  }
  const set = (key: Key, value: unknown) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setResult(null);
  };
  const save = async (): Promise<boolean> => {
    setSaving(true);
    setResult({ busy: true, text: "Saving…" });
    try {
      await unwrap(api.POST("/api/settings", { body: draft as Record<string, unknown> }));
      setDraft({});
      setResult({ ok: true, text: "Saved" });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      return true;
    } catch (error) {
      setResult({ ok: false, text: errorText(error) });
      return false;
    } finally {
      setSaving(false);
    }
  };
  return { draft, original: values, get, set, save, saving, result, dirty: Object.keys(draft).length > 0 };
}

export type Form = ReturnType<typeof useSettingsForm>;
export type Result = { ok?: boolean; busy?: boolean; text: string };

export function ResultText({ result, name }: { result: Result | null; name: string }) {
  if (!result) return <span data-result={name} />;
  const tone = result.busy ? "text-gray-400" : result.ok ? "text-green-300" : result.ok === false ? "text-red-300" : "text-gray-300";
  return (
    <span data-result={name} data-ok={result.busy ? undefined : String(!!result.ok)} className={`inline-flex items-center gap-1.5 text-sm ${tone}`} role="status">
      {result.busy ? <Spinner className="h-4 w-4" /> : result.ok ? <CheckCircleIcon className="h-4 w-4" /> : result.ok === false ? <ExclamationCircleIcon className="h-4 w-4" /> : null}
      {result.text}
    </span>
  );
}

export function SettingsSection({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <fieldset className="mt-10 first:mt-6">
      <legend className="text-xl font-bold text-gray-100">{title}</legend>
      {description && <p className="mt-1 max-w-3xl text-sm text-gray-400">{description}</p>}
      <div className="mt-2 divide-y divide-gray-700/60">{children}</div>
    </fieldset>
  );
}

export function Row({ label, hint, htmlFor, children }: { label: string; hint?: ReactNode; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="py-5 sm:grid sm:grid-cols-3 sm:items-start sm:gap-4">
      <div className="mb-2 sm:mb-0 sm:pt-2">
        {htmlFor ? (
          <label htmlFor={htmlFor} className="block text-sm font-bold text-gray-300">
            {label}
          </label>
        ) : (
          <span className="block text-sm font-bold text-gray-300">{label}</span>
        )}
        {hint && <span className="mt-0.5 block text-xs font-medium text-gray-500">{hint}</span>}
      </div>
      <div className="max-w-xl text-sm text-white sm:col-span-2">{children}</div>
    </div>
  );
}

export function TextField({ form, name, label, hint, placeholder, type = "text" }: { form: Form; name: Key; label: string; hint?: ReactNode; placeholder?: string; type?: string }) {
  const id = `f-${name}`;
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <input id={id} name={name} type={type} className="field-input" value={String(form.get(name) ?? "")} placeholder={placeholder} onChange={(event) => form.set(name, event.target.value)} autoComplete="off" />
    </Row>
  );
}

export function NumberField({ form, name, label, hint, min, max, step }: { form: Form; name: Key; label: string; hint?: ReactNode; min?: number; max?: number; step?: number }) {
  const id = `f-${name}`;
  const value = form.get(name);
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <input
        id={id}
        name={name}
        type="number"
        className="field-input max-w-40"
        value={value === null || value === undefined ? "" : String(value)}
        min={min}
        max={max}
        step={step}
        onChange={(event) => form.set(name, event.target.value === "" ? null : Number(event.target.value))}
      />
    </Row>
  );
}

export function SelectRow({ form, name, label, hint, options }: { form: Form; name: Key; label: string; hint?: ReactNode; options: { value: string; label: string }[] }) {
  const id = `f-${name}`;
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <select id={id} name={name} className="field-input" value={String(form.get(name))} onChange={(event) => form.set(name, event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Row>
  );
}

export function CheckRow({ form, name, label, hint }: { form: Form; name: Key; label: string; hint?: ReactNode }) {
  const id = `f-${name}`;
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <input
        id={id}
        name={name}
        type="checkbox"
        className="mt-2 h-5 w-5 cursor-pointer rounded border-gray-500 bg-gray-700 accent-indigo-500"
        checked={!!form.get(name)}
        onChange={(event) => form.set(name, event.target.checked)}
      />
    </Row>
  );
}

export function SecretField({ form, name, label, hint, autoComplete = "off" }: { form: Form; name: Key; label: string; hint?: ReactNode; autoComplete?: string }) {
  const id = `f-${name}`;
  const stored = form.original[name] === true;
  const pending = form.draft[name];
  const clearing = pending === false;
  const placeholder = clearing ? "Removed when you save" : stored ? "Saved. Leave empty to keep it." : "";
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <div className="flex gap-2">
        <input
          id={id}
          name={name}
          type="password"
          className="field-input"
          autoComplete={autoComplete}
          value={typeof pending === "string" ? pending : ""}
          placeholder={placeholder}
          onChange={(event) => form.set(name, event.target.value)}
        />
        {stored && !clearing && (
          <Button size="sm" variant="ghost" onClick={() => form.set(name, false)}>
            Clear
          </Button>
        )}
      </div>
    </Row>
  );
}

/** Secret value for a connection test: what was typed, else the stored one (sent as null). */
export function typed(form: Form, name: Key): string | null {
  const pending = form.draft[name];
  return typeof pending === "string" && pending ? pending : null;
}

export function TestButton({ what, label, body }: { what: string; label: string; body: () => { value?: string | null; extra?: string | null } }) {
  const [result, setResult] = useState<Result | null>(null);
  const test = async () => {
    setResult({ busy: true, text: "Testing…" });
    try {
      const outcome = await unwrap(api.POST("/api/test/{what}", { params: { path: { what } }, body: body() }));
      setResult({ ok: outcome.ok, text: outcome.message });
    } catch (error) {
      setResult({ ok: false, text: errorText(error) });
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-3 py-4">
      <Button size="sm" onClick={test} data-test={what}>
        {label}
      </Button>
      <ResultText result={result} name={what} />
    </div>
  );
}

export function SaveBar({ form }: { form: Form }) {
  return (
    <div className="mt-8 flex flex-wrap items-center justify-end gap-3 border-t border-gray-700 pt-5">
      <ResultText result={form.result} name="save" />
      <Button type="submit" variant="primary" disabled={form.saving} data-action="save">
        Save Changes
      </Button>
    </div>
  );
}
