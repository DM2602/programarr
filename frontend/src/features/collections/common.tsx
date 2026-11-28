import { siRadarr, siSonarr } from "simple-icons";
import type { Schemas } from "../../api/client";
import { BrandIcon } from "../../components/BrandIcon";
import { Badge } from "../../components/ui";

export type Collection = Schemas["CollectionBrief"];
export type CollectionDetail = Schemas["CollectionDetail"];

export const SYNC_LABELS: Record<Collection["sync_mode"], string> = { mirror: "Mirror", append: "Append" };
export const ORDER_LABELS: Record<Collection["item_order"], string> = { list: "List order", release: "Release date", alpha: "Title" };
export const SERVICE_LABELS = { radarr: "Radarr", sonarr: "Sonarr" } as const;

/** Where the collection stands: kept by a live flow, a dry run, or no flow any more. */
export function CollectionState({ collection }: { collection: Collection }) {
  if (!collection.in_flow) {
    return (
      <Badge tone="yellow" title="Output removed from its flow, still in Plex">
        No flow
      </Badge>
    );
  }
  if (!collection.live) {
    return (
      <Badge tone="gray" title="Dry run: Plex stays as it is">
        Dry run
      </Badge>
    );
  }
  if (!collection.plex_key) return <Badge tone="gray">Not in Plex yet</Badge>;
  return null;
}

export function ServiceBadge({ service }: { service: "radarr" | "sonarr" | null | undefined }) {
  if (!service) return null;
  return (
    <Badge tone="gray" icon={<BrandIcon icon={service === "radarr" ? siRadarr : siSonarr} className="h-3.5 w-3.5" />}>
      {SERVICE_LABELS[service]}
    </Badge>
  );
}
