import { TrashIcon } from "@heroicons/react/24/outline";
import { useState, type FormEvent } from "react";
import { api, errorText, unwrap, type Schemas } from "../../api/client";
import { Button } from "../../components/Button";
import { plural } from "../../lib/format";
import { ResultText, SaveBar, SecretField, SettingsSection, TestButton, TextField, typed, useSettingsForm, type Result } from "./fields";

type View = Schemas["SettingsView"];
type PathMap = Schemas["PathMap"];

function Libraries({ view }: { view: View }) {
  const [libraries, setLibraries] = useState(view.libraries);
  const [enabled, setEnabled] = useState(() => new Set(view.libraries.filter((lib) => lib.enabled).map((lib) => lib.key)));
  const [result, setResult] = useState<Result | null>(null);

  const load = async () => {
    setResult({ busy: true, text: "Loading…" });
    try {
      const data = await unwrap(api.POST("/api/libraries/refresh"));
      setLibraries(data.libraries);
      setEnabled(new Set(data.libraries.filter((lib) => lib.enabled).map((lib) => lib.key)));
      setResult({ ok: true, text: `${plural(data.libraries.length, "library", "libraries")} loaded. Pick and save.` });
    } catch (error) {
      setResult({ ok: false, text: errorText(error) });
    }
  };
  const save = async () => {
    setResult({ busy: true, text: "Saving…" });
    try {
      await unwrap(api.POST("/api/libraries", { body: { enabled: [...enabled] } }));
      setResult({ ok: true, text: "Saved" });
    } catch (error) {
      setResult({ ok: false, text: errorText(error) });
    }
  };
  const toggle = (key: string, on: boolean) => {
    setEnabled((current) => {
      const next = new Set(current);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  return (
    <SettingsSection title="Libraries" description="Titles in a library at the first sync are left as they are.">
      <div className="py-4" id="libraries">
        {libraries.length ? (
          <div className="space-y-2">
            {libraries.map((lib) => (
              <label key={lib.key} className="flex cursor-pointer items-start gap-3 rounded-md px-2 py-1.5 hover:bg-gray-800">
                <input type="checkbox" name="lib" value={lib.key} className="mt-0.5 h-5 w-5 accent-indigo-500" checked={enabled.has(lib.key)} onChange={(event) => toggle(lib.key, event.target.checked)} />
                <span className="text-sm">
                  <span className="font-semibold text-gray-100">{lib.title}</span>
                  <span className="text-gray-400">
                    {", "}
                    {lib.type === "movie" ? "Movies" : "Shows"}
                    {lib.locations.length ? `, ${lib.locations.join(", ")}` : ""}
                  </span>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">No libraries loaded yet.</p>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button size="sm" onClick={load} data-action="libraries-refresh">
            Load from Plex
          </Button>
          <Button size="sm" onClick={save} disabled={!libraries.length} data-action="libraries-save">
            Save selection
          </Button>
          <ResultText result={result} name="libraries" />
        </div>
      </div>
    </SettingsSection>
  );
}

export function PlexSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  const [paths, setPaths] = useState<Result | null>(null);
  const maps = form.get("path_maps") as PathMap[];

  const setMap = (index: number, change: Partial<PathMap>) => form.set("path_maps", maps.map((map, i) => (i === index ? { ...map, ...change } : map)));

  const checkPaths = async () => {
    setPaths({ busy: true, text: "Checking…" });
    if (form.dirty && !(await form.save())) {
      setPaths(null);
      return;
    }
    try {
      const r = await unwrap(api.GET("/api/paths/check"));
      if (!r.checked) {
        setPaths({ text: "No titles synced yet. Sync first, then check." });
        return;
      }
      let text = `${r.ok} of ${r.checked} folders reachable and writable.`;
      const missing = r.examples.filter((example) => !example.readonly);
      if (missing.length) text += ` Not found: ${missing.map((example) => `${example.plex} → ${example.local}`).join("; ")}.`;
      if (r.readonly) text += ` Not writable: ${r.readonly}.`;
      setPaths({ ok: r.ok === r.checked, text });
    } catch (error) {
      setPaths({ ok: false, text: errorText(error) });
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void form.save();
  };

  return (
    <form onSubmit={submit} data-settings>
      <SettingsSection title="Plex server">
        <TextField form={form} name="plex_url" label="Address" hint="How this container reaches Plex, with port" placeholder="http://192.168.1.10:32400" type="url" />
        <SecretField form={form} name="plex_token" label="Token" hint="Plex Web: a title › Get Info › View XML, X-Plex-Token in the address" />
        <TestButton what="plex" label="Test connection" body={() => ({ value: typed(form, "plex_token"), extra: (form.get("plex_url") as string) || null })} />
      </SettingsSection>
      <Libraries view={view} />
      <SettingsSection title="Path mapping" description="Only needed when Plex and Programarr see the media folders under different paths.">
        <div className="space-y-2 py-4">
          {maps.map((map, index) => (
            <div key={index} className="path-map flex flex-col gap-2 sm:flex-row">
              <input className="field-input" aria-label="Path in Plex" placeholder="Path in Plex, e.g. /data/media" value={map.plex} onChange={(event) => setMap(index, { plex: event.target.value })} />
              <input className="field-input" aria-label="Path in Programarr" placeholder="Path in Programarr, e.g. /media" value={map.local} onChange={(event) => setMap(index, { local: event.target.value })} />
              <Button size="sm" variant="ghost" aria-label="Remove mapping" onClick={() => form.set("path_maps", maps.filter((_, i) => i !== index))}>
                <TrashIcon className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-3 pt-2">
            <Button size="sm" onClick={() => form.set("path_maps", [...maps, { plex: "", local: "" }])}>
              Add mapping
            </Button>
            <Button size="sm" onClick={checkPaths} data-action="paths-check">
              Check paths
            </Button>
            <ResultText result={paths} name="paths" />
          </div>
          <p className="pt-2 text-xs text-gray-500">In Plex, turn on "Use local assets" for each library (Advanced).</p>
        </div>
      </SettingsSection>
      <SaveBar form={form} />
    </form>
  );
}
