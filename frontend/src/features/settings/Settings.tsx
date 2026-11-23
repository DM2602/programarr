import { Navigate, useParams } from "react-router";
import { useSettings } from "../../api/queries";
import { Tabs } from "../../components/Controls";
import { Loading, Notice, PageHeader } from "../../components/ui";
import { useTitle } from "../../lib/useTitle";
import { PlexSection } from "./PlexSection";
import { AboutSection, AutomationSection, FilesSection, ListsSection, NotificationsSection, SourcesSection, WebhooksSection } from "./sections";

const SECTIONS = [
  { key: "plex", label: "Plex", component: PlexSection },
  { key: "files", label: "Files", component: FilesSection },
  { key: "sources", label: "Sources", component: SourcesSection },
  { key: "lists", label: "Lists", component: ListsSection },
  { key: "automation", label: "Automation", component: AutomationSection },
  { key: "notifications", label: "Notifications", component: NotificationsSection },
  { key: "webhooks", label: "Webhooks", component: WebhooksSection },
  { key: "about", label: "About", component: AboutSection },
] as const;

export function Settings() {
  const { section = "plex" } = useParams();
  const current = SECTIONS.find((entry) => entry.key === section);
  useTitle(current ? `${current.label} settings` : "Settings");
  const { data, isPending, error } = useSettings();
  if (!current) return <Navigate to="/settings/plex" replace />;
  const Section = current.component;
  return (
    <>
      <PageHeader title="Settings" />
      <Tabs label="Settings" items={SECTIONS.map((entry) => ({ key: entry.key, label: entry.label, to: `/settings/${entry.key}`, active: entry.key === section }))} />
      <div className="max-w-5xl">
        {isPending ? <Loading /> : error || !data ? <Notice tone="red">{error?.message ?? "Cannot load settings"}</Notice> : <Section key={section} view={data} />}
      </div>
    </>
  );
}
