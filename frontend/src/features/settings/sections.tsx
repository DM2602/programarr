import { ArrowDownIcon, ArrowUpIcon, ClipboardDocumentIcon, TrashIcon } from "@heroicons/react/24/outline";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, errorText, unwrap, type Schemas } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useBackup } from "../../api/queries";
import { Button } from "../../components/Button";
import { useToast } from "../../components/Toasts";
import { fileSize, plural } from "../../lib/format";
import { CheckRow, NumberField, Row, SaveBar, SecretField, SelectRow, SettingsSection, TestButton, TextField, typed, useSettingsForm, type Form } from "./fields";

type View = Schemas["SettingsView"];
type Designer = Schemas["Designer"];

function SettingsForm({ form, children }: { form: Form; children: React.ReactNode }) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void form.save();
  };
  return (
    <form onSubmit={submit} data-settings>
      {children}
      <SaveBar form={form} />
    </form>
  );
}

// -- Files -------------------------------------------------------------------------------
const SEASON_NAMES: Record<string, string> = {
  root: "season01-poster.jpg in the show folder",
  root_plain: "Season01.jpg in the show folder",
  folder: "Season01.jpg in the season folder",
};

export function FilesSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  const now = view.naming_detected;
  const movieNow = now.movie ? ` (now: ${now.movie === "file" ? "name of the video file" : `${now.movie}.jpg`})` : "";
  const showNow = now.show ? ` (now: ${now.show}.jpg)` : "";
  const seasonNow = now.season ? ` (now: ${SEASON_NAMES[now.season] ?? now.season})` : "";
  return (
    <SettingsForm form={form}>
      <SettingsSection title="File names" description="A fixed name always applies; other poster files for the same spot move to the archive. As existing keeps the names already in use.">
        <SelectRow
          form={form}
          name="naming_movie"
          label="Movies"
          hint="Several movies in one folder always use the video file's name"
          options={[
            { value: "auto", label: `As existing${movieNow}` },
            { value: "poster", label: "poster.jpg" },
            { value: "file", label: "Like the video file (Movie (2021).jpg)" },
          ]}
        />
        <SelectRow
          form={form}
          name="naming_show"
          label="Shows"
          options={[
            { value: "auto", label: `As existing${showNow}` },
            { value: "poster", label: "poster.jpg" },
            { value: "show", label: "show.jpg" },
            { value: "folder", label: "folder.jpg" },
          ]}
        />
        <SelectRow
          form={form}
          name="naming_season"
          label="Seasons"
          hint="Without season folders, in the show folder. Specials: season-specials-poster.jpg"
          options={[
            { value: "auto", label: `As existing${seasonNow}` },
            { value: "root", label: SEASON_NAMES.root },
            { value: "root_plain", label: SEASON_NAMES.root_plain },
            { value: "folder", label: SEASON_NAMES.folder },
          ]}
        />
        <CheckRow form={form} name="keep_png" label="Keep PNG as PNG" hint="Otherwise JPEG. WebP always becomes JPEG." />
      </SettingsSection>
      <SettingsSection title="Other files and Plex">
        <SelectRow
          form={form}
          name="conflicts"
          label="Other poster files for the same spot"
          hint="Plex may pick the wrong one otherwise. Restore them under History."
          options={[
            { value: "archive", label: "Move to the archive" },
            { value: "keep", label: "Leave them" },
          ]}
        />
        <CheckRow form={form} name="unlock_fields" label="Unlock posters in Plex" hint="A locked poster ignores the file in the folder." />
        <CheckRow form={form} name="verify_plex" label="Check that Plex shows the file" hint="After writing. Otherwise it shows up under Review." />
        <SelectRow
          form={form}
          name="plex_poster_files"
          label="Keep the Plex poster as file"
          hint="For titles without a file"
          options={[
            { value: "off", label: "Off" },
            { value: "movies", label: "Movies" },
            { value: "all", label: "Movies and shows" },
          ]}
        />
        <NumberField form={form} name="archive_keep" label="Older versions per poster in the archive" hint="0 keeps all" min={0} max={100} />
      </SettingsSection>
      <BackupRows form={form} />
    </SettingsForm>
  );
}

function BackupRows({ form }: { form: Form }) {
  const { data } = useBackup();
  const run = useRunJob();
  const hint = data ? `${plural(data.files, "file", "files")}, ${fileSize(data.bytes)} in ${data.folder}` : undefined;
  return (
    <SettingsSection title="Backup">
      <CheckRow form={form} name="backup_posters" label="Copy every poster to the backup" hint={hint} />
      <div className="flex flex-wrap items-center gap-3 py-4">
        <Button size="sm" onClick={() => run(() => unwrap(api.POST("/api/backup")), "Backing up posters…")} data-action="backup">
          Back up now
        </Button>
        <Button size="sm" onClick={() => run(() => unwrap(api.POST("/api/backup/restore")), "Restoring missing posters…")} data-action="restore-backup">
          Restore missing posters
        </Button>
      </div>
    </SettingsSection>
  );
}

// -- Sources -----------------------------------------------------------------------------
export function SourcesSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  return (
    <SettingsForm form={form}>
      <SettingsSection title="MediUX">
        <SecretField form={form} name="mediux_token" label="Personal API token" hint="From your MediUX account settings" />
        <SelectRow
          form={form}
          name="mediux_quality"
          label="Image quality"
          options={[
            { value: "original", label: "Designer's original" },
            { value: "optimized", label: "Optimized (smaller JPEGs)" },
          ]}
        />
        <TestButton what="mediux" label="Check token" body={() => ({ value: typed(form, "mediux_token") })} />
      </SettingsSection>
      <SettingsSection title="ThePosterDB" description="Downloads need an account.">
        <TextField form={form} name="tpdb_email" label="Email or username" />
        <SecretField form={form} name="tpdb_password" label="Password" autoComplete="new-password" />
        <NumberField form={form} name="tpdb_delay" label="Pause between page loads" hint="Seconds" min={0} max={10} step={0.25} />
        <NumberField form={form} name="tpdb_pages" label="Pages per title" hint="24 posters per page" min={1} max={10} />
        <TextField form={form} name="flaresolverr_url" label="FlareSolverr" hint="Optional, only for Cloudflare checks" placeholder="http://flaresolverr:8191" type="url" />
        <TestButton what="tpdb" label="Test sign-in" body={() => ({})} />
        <TestButton what="flaresolverr" label="Test FlareSolverr" body={() => ({ value: (form.get("flaresolverr_url") as string) || null })} />
      </SettingsSection>
      <SettingsSection title="More sources" description="Shown in the picker under Online. TMDB or TVmaze also tell whether a show has ended.">
        <SecretField form={form} name="tmdb_key" label="TMDB API key" hint="v3 key or v4 read token" />
        <TextField form={form} name="tmdb_languages" label="TMDB languages" hint="In order of preference, null means textless" />
        <TestButton what="tmdb" label="Test TMDB" body={() => ({ value: typed(form, "tmdb_key") })} />
        <SecretField form={form} name="fanart_key" label="fanart.tv API key" hint="Project key" />
        <SecretField form={form} name="fanart_client_key" label="fanart.tv personal key" hint="Optional, new images sooner" />
        <TestButton what="fanart" label="Test fanart.tv" body={() => ({ value: typed(form, "fanart_key") })} />
        <CheckRow form={form} name="tvmaze_enabled" label="Use TVmaze" hint="No key needed" />
        <CheckRow form={form} name="allow_private_urls" label="Allow links into your network" hint="Image links to addresses in your home network" />
      </SettingsSection>
    </SettingsForm>
  );
}

// -- Lists ---------------------------------------------------------------------------------
export function ListsSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  return (
    <SettingsForm form={form}>
      <SettingsSection title="MDBList" description="Ratings for all list titles, and MDBList lists.">
        <SecretField form={form} name="mdblist_key" label="API key" hint="mdblist.com › Preferences" />
        <TestButton what="mdblist" label="Check key" body={() => ({ value: typed(form, "mdblist_key") })} />
      </SettingsSection>
      <SettingsSection title="Trakt">
        <SecretField form={form} name="trakt_client_id" label="Client ID" hint="trakt.tv › Settings › Your API Apps" />
        <TestButton what="trakt" label="Check client ID" body={() => ({ value: typed(form, "trakt_client_id") })} />
      </SettingsSection>
      <SettingsSection title="FlixPatrol" description={<>Cloudflare checks go through FlareSolverr (<Link to="/settings/sources" className="underline underline-offset-2">Sources</Link>).</>}>
        <NumberField form={form} name="flixpatrol_delay" label="Pause between page loads" hint="Seconds" min={0} max={10} step={0.25} />
      </SettingsSection>
      <SettingsSection title="Matching">
        <NumberField form={form} name="ratings_days" label="Refresh ratings after" hint="Days" min={1} max={90} />
        <NumberField form={form} name="runs_keep_days" label="Keep previews and flow runs" hint="Days. The last run of each list and flow stays." min={1} max={365} />
      </SettingsSection>
    </SettingsForm>
  );
}

// -- Services --------------------------------------------------------------------------------
function ArrSettings({ form, service, label, port }: { form: ReturnType<typeof useSettingsForm>; service: "radarr" | "sonarr"; label: string; port: number }) {
  return (
    <SettingsSection title={label}>
      <TextField form={form} name={`${service}_url`} label="Address" hint="How this container reaches it, with port" placeholder={`http://192.168.1.10:${port}`} type="url" />
      <SecretField form={form} name={`${service}_key`} label="API key" hint={`${label} › Settings › General`} />
      <TextField form={form} name={`${service}_external_url`} label="Address for links" hint="Optional, if the browser uses another address" placeholder={`https://${service}.example.com`} type="url" />
      <TestButton what={service} label="Test connection" body={() => ({ value: typed(form, `${service}_key`), extra: (form.get(`${service}_url`) as string) || null })} />
    </SettingsSection>
  );
}

export function ServicesSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  return (
    <SettingsForm form={form}>
      <ArrSettings form={form} service="radarr" label="Radarr" port={7878} />
      <ArrSettings form={form} service="sonarr" label="Sonarr" port={8989} />
    </SettingsForm>
  );
}

// -- Automation ------------------------------------------------------------------------------
function Designers({ form, view }: { form: Form; view: View }) {
  const toast = useToast();
  const [follows, setFollows] = useState(view.follows);
  const designers = form.get("designers") as Designer[];
  const update = (list: Designer[]) => form.set("designers", list);
  const move = (index: number, by: number) => {
    const target = index + by;
    if (target < 0 || target >= designers.length) return;
    const list = [...designers];
    [list[index], list[target]] = [list[target], list[index]];
    update(list);
  };
  const reload = async () => {
    try {
      setFollows(await unwrap(api.POST("/api/follows/refresh")));
    } catch (error) {
      toast.show(errorText(error), { kind: "error" });
    }
  };
  return (
    <SettingsSection title="Preferred designers" description="Higher in the list wins.">
      <div className="space-y-2 py-4" data-repeat="designers">
        {designers.map((designer, index) => (
          <div key={index} className="designer flex flex-wrap gap-2 sm:flex-nowrap">
            <select
              className="field-input sm:max-w-44"
              aria-label="Source"
              value={designer.source}
              onChange={(event) => update(designers.map((d, i) => (i === index ? { ...d, source: event.target.value as Designer["source"] } : d)))}
            >
              <option value="any">Both sources</option>
              <option value="mediux">MediUX</option>
              <option value="tpdb">ThePosterDB</option>
            </select>
            <input
              className="field-input"
              aria-label="Username"
              placeholder="Username"
              data-k="name"
              value={designer.name}
              onChange={(event) => update(designers.map((d, i) => (i === index ? { ...d, name: event.target.value } : d)))}
            />
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" aria-label="Move up" disabled={index === 0} onClick={() => move(index, -1)}>
                <ArrowUpIcon className="h-4 w-4" />
              </Button>
              <Button size="sm" variant="ghost" aria-label="Move down" disabled={index === designers.length - 1} onClick={() => move(index, 1)}>
                <ArrowDownIcon className="h-4 w-4" />
              </Button>
              <Button size="sm" variant="ghost" aria-label="Remove" onClick={() => update(designers.filter((_, i) => i !== index))}>
                <TrashIcon className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ))}
        <Button size="sm" onClick={() => update([...designers, { source: "any", name: "" }])} data-action="add-designer">
          Add designer
        </Button>
      </div>
      <CheckRow form={form} name="use_mediux_follows" label="Designers I follow on MediUX count too" hint="After the list above. Hidden designers never." />
      <div className="flex flex-wrap items-center gap-3 py-4 text-sm text-gray-400">
        <span>
          {follows.follow.length ? `You follow ${follows.follow.join(", ")}.` : "You follow nobody on MediUX."}
          {follows.hide.length ? ` Hidden: ${follows.hide.join(", ")}.` : ""}
        </span>
        <Button size="sm" variant="ghost" onClick={reload}>
          Reload from MediUX
        </Button>
      </div>
    </SettingsSection>
  );
}

export function AutomationSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  const order = (form.get("source_order") as string[]).join(",");
  return (
    <SettingsForm form={form}>
      <SettingsSection title="Automation">
        <CheckRow
          form={form}
          name="automation"
          label="Write posters on its own"
          hint="New titles, new seasons and set updates. When off, Programarr writes only when you click."
        />
      </SettingsSection>
      <SettingsSection title="New titles">
        <CheckRow form={form} name="auto_new" label="Apply sets for new titles" hint="Preferred designers only, the rest goes to Review" />
        <CheckRow form={form} name="auto_require_complete" label="Shows only with complete sets" hint="Show poster and every season in Plex" />
        <CheckRow form={form} name="auto_count_specials" label="Count specials too" />
        <CheckRow form={form} name="auto_collection_consistency" label="Movies of a collection use the same set" hint="A new movie follows the set of the others" />
        <Row label="On a tie, prefer" htmlFor="f-source_order">
          <select id="f-source_order" className="field-input" value={order} onChange={(event) => form.set("source_order", event.target.value.split(","))}>
            <option value="mediux,tpdb">MediUX, then ThePosterDB</option>
            <option value="tpdb,mediux">ThePosterDB, then MediUX</option>
          </select>
        </Row>
      </SettingsSection>
      <Designers form={form} view={view} />
      <SettingsSection title="Schedule">
        <NumberField form={form} name="poll_minutes" label="Sync with Plex every" hint="Minutes. Webhooks sync right away." min={5} max={1440} />
        <NumberField form={form} name="set_check_hours" label="Check linked sets every" hint="Hours" min={1} max={720} />
        <NumberField form={form} name="wait_max_days" label="Wait for missing seasons" hint="Days, 0 waits forever" min={0} max={3650} />
        <SelectRow
          form={form}
          name="set_changes"
          label="When a designer changes an image in a linked set"
          options={[
            { value: "notify", label: "Tell me, I decide under Review" },
            { value: "apply", label: "Take it right away" },
          ]}
        />
        <CheckRow form={form} name="detect_movies" label="Include movies in Match existing" />
      </SettingsSection>
    </SettingsForm>
  );
}

// -- Notifications ----------------------------------------------------------------------------
export function NotificationsSection({ view }: { view: View }) {
  const form = useSettingsForm(view.values);
  const events = form.get("discord_events") as Record<string, boolean>;
  return (
    <SettingsForm form={form}>
      <SettingsSection title="Discord">
        <SecretField form={form} name="discord_webhook" label="Webhook URL" hint="Discord: Edit Channel › Integrations › Webhooks" />
        <TestButton what="discord" label="Send test message" body={() => ({ value: typed(form, "discord_webhook") })} />
        <Row label="Messages">
          <div className="space-y-2">
            {view.discord_events.map((option) => (
              <label key={option.key} className="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  className="h-5 w-5 accent-indigo-500"
                  checked={!!events[option.key]}
                  onChange={(event) => form.set("discord_events", { ...events, [option.key]: event.target.checked })}
                />
                <span className="text-gray-200">{option.label}</span>
              </label>
            ))}
          </div>
        </Row>
      </SettingsSection>
      <SettingsSection title="Links in messages">
        <TextField form={form} name="base_url" label="Programarr address" hint="For links in Discord messages and webhook URLs" placeholder="https://programarr.example.com" type="url" />
      </SettingsSection>
    </SettingsForm>
  );
}

// -- Webhooks -----------------------------------------------------------------------------------
function CopyLine({ value, label }: { value: string; label: string }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.show("Copied", { kind: "success", timeout: 2000 });
    } catch {
      toast.show("Copy failed, select the text instead", { kind: "error" });
    }
  };
  return (
    <div className="flex gap-2">
      <input readOnly className="field-input font-mono text-xs" value={value} aria-label={label} onFocus={(event) => event.target.select()} />
      <Button size="sm" onClick={copy} icon={<ClipboardDocumentIcon className="h-4 w-4" />}>
        Copy
      </Button>
    </div>
  );
}

export function WebhooksSection({ view }: { view: View }) {
  return (
    <>
      <SettingsSection title="Webhooks" description="Sonarr, Radarr or Plex start a sync 90 seconds after something new arrives.">
        <Row label="Sonarr" hint="Settings › Connect › Webhook, events On Import and On Series Add">
          <CopyLine value={view.hooks.sonarr} label="Webhook for Sonarr" />
        </Row>
        <Row label="Radarr" hint="Settings › Connect › Webhook, event On Import">
          <CopyLine value={view.hooks.radarr} label="Webhook for Radarr" />
        </Row>
        <Row label="Plex" hint="Settings › Webhooks, needs Plex Pass">
          <CopyLine value={view.hooks.plex} label="Webhook for Plex" />
        </Row>
        <p className="py-4 text-xs text-gray-500">Behind a proxy with sign-in, such as Pangolin SSO, allow /api/hooks/ without it. The token protects the webhooks.</p>
      </SettingsSection>
      <SettingsSection title="API">
        <p className="py-4 text-sm text-gray-400">
          Documentation at{" "}
          <a href="/api/docs" className="text-indigo-300 hover:text-indigo-200">
            /api/docs
          </a>
        </p>
      </SettingsSection>
    </>
  );
}

// -- About ----------------------------------------------------------------------------------------
export function AboutSection({ view }: { view: View }) {
  const run = useRunJob();
  return (
    <>
      <SettingsSection title="Data">
        <Row label="Data folder" hint="Back up this folder. Posters themselves live in the media folders.">
          <p className="pt-2 font-mono text-xs break-all text-gray-200">{view.data_dir}</p>
        </Row>
        <Row label="Version">
          <p className="pt-2 text-gray-200">Programarr {view.version}</p>
        </Row>
      </SettingsSection>
      <SettingsSection title="Maintenance" description="After changing files in the folders by hand.">
        <div className="flex flex-wrap gap-2 py-4">
          <Button onClick={() => run(() => unwrap(api.POST("/api/rescan")), "Rescanning folders…")}>Rescan all folders</Button>
          <Button onClick={() => run(() => unwrap(api.POST("/api/sync", { params: { query: { full: true } } })), "Full sync with Plex…")}>Full sync with Plex</Button>
        </div>
      </SettingsSection>
      <p className="mt-8 text-xs text-gray-500">
        Licensed under GPL-3.0. <Link to="/logs" className="hover:text-gray-300">Logs</Link>
      </p>
    </>
  );
}
