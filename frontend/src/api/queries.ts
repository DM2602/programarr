import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export type LibraryParams = {
  type: string;
  status: string;
  q: string;
  library_key: string;
  sort: string;
  page: number;
};

export function useAuthState() {
  return useQuery({
    queryKey: ["auth"],
    queryFn: () => unwrap(api.GET("/api/auth/state")),
    staleTime: Infinity,
    retry: 1,
  });
}

/** Counts for the sidebar and running jobs; polls faster while something runs. */
export function useStatus(enabled = true) {
  return useQuery({
    queryKey: ["status"],
    queryFn: () => unwrap(api.GET("/api/status")),
    enabled,
    refetchInterval: (query) => (query.state.data?.jobs.length ? 2500 : 20000),
  });
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => unwrap(api.GET("/api/overview")),
    refetchInterval: (query) => (query.state.data?.jobs.some((job) => job.status === "running" || job.status === "queued") ? 3000 : false),
  });
}

export function useLibrary(params: LibraryParams) {
  return useQuery({
    queryKey: ["library", params],
    queryFn: () => unwrap(api.GET("/api/library", { params: { query: params } })),
    placeholderData: keepPreviousData,
  });
}

export function useItem(key: string) {
  return useQuery({
    queryKey: ["item", key],
    queryFn: () => unwrap(api.GET("/api/items/{key}", { params: { path: { key } } })),
  });
}

export function useItemArr(key: string) {
  return useQuery({
    queryKey: ["item-arr", key],
    queryFn: () => unwrap(api.GET("/api/items/{key}/arr", { params: { path: { key } } })),
    staleTime: 60_000,
  });
}

export function useItemMissing(key: string, enabled: boolean) {
  return useQuery({
    queryKey: ["item-missing", key],
    queryFn: () => unwrap(api.GET("/api/items/{key}/missing", { params: { path: { key } } })),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useSets(key: string, enabled: boolean) {
  return useQuery({
    queryKey: ["sets", key],
    queryFn: () => unwrap(api.GET("/api/items/{key}/sets", { params: { path: { key } } })),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function usePlexImages(key: string, slot: string, enabled: boolean) {
  return useQuery({
    queryKey: ["plex-images", key, slot],
    queryFn: () => unwrap(api.GET("/api/items/{key}/plex", { params: { path: { key }, query: { slot } } })),
    enabled,
    staleTime: 60_000,
  });
}

export function useOnlineImages(key: string, slot: string, enabled: boolean) {
  return useQuery({
    queryKey: ["online-images", key, slot],
    queryFn: () => unwrap(api.GET("/api/items/{key}/online", { params: { path: { key }, query: { slot } } })),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useReviews(kind: string) {
  return useQuery({
    queryKey: ["reviews", kind],
    queryFn: () => unwrap(api.GET("/api/reviews", { params: { query: { kind } } })),
  });
}

export function useWaiting() {
  return useQuery({ queryKey: ["waiting"], queryFn: () => unwrap(api.GET("/api/waiting")) });
}

export function useBackup() {
  return useQuery({ queryKey: ["backup"], queryFn: () => unwrap(api.GET("/api/backup")) });
}

export function useDetectStats() {
  return useQuery({
    queryKey: ["detect-stats"],
    queryFn: () => unwrap(api.GET("/api/detect/stats")),
    refetchInterval: (query) => {
      const status = query.state.data?.job?.status;
      return status === "running" || status === "queued" ? 2000 : false;
    },
  });
}

export function useHistory(page: number) {
  return useQuery({
    queryKey: ["history", page],
    queryFn: () => unwrap(api.GET("/api/history", { params: { query: { page } } })),
    placeholderData: keepPreviousData,
  });
}

export function useLog(level: string) {
  return useQuery({
    queryKey: ["log", level],
    queryFn: () => unwrap(api.GET("/api/log", { params: { query: { level } } })),
  });
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => unwrap(api.GET("/api/settings")), staleTime: Infinity });
}

// -- lists -------------------------------------------------------------------------------
export function useListSources() {
  return useQuery({ queryKey: ["list-sources"], queryFn: () => unwrap(api.GET("/api/lists/sources")) });
}

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/lists/runs", { params: { query: { limit: 60, previews: true } } })),
    refetchInterval: (query) => (query.state.data?.runs.some((run) => run.status === "running") ? 2500 : false),
  });
}

export function useRun(id: number) {
  return useQuery({
    queryKey: ["run", id],
    queryFn: () => unwrap(api.GET("/api/lists/runs/{run_id}", { params: { path: { run_id: id } } })),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : false),
  });
}

export function useProblems() {
  return useQuery({ queryKey: ["problems"], queryFn: () => unwrap(api.GET("/api/lists/problems")) });
}

export function useMatches(params: { status: string; q: string; page: number }) {
  return useQuery({
    queryKey: ["matches", params],
    queryFn: () => unwrap(api.GET("/api/lists/matches", { params: { query: params } })),
    placeholderData: keepPreviousData,
  });
}

export function useOverrides() {
  return useQuery({ queryKey: ["overrides"], queryFn: () => unwrap(api.GET("/api/lists/overrides")) });
}

export function useExclusions() {
  return useQuery({ queryKey: ["exclusions"], queryFn: () => unwrap(api.GET("/api/exclusions")) });
}

export function useMediaSearch(params: { q: string; kind: string; year?: number }, enabled: boolean) {
  return useQuery({
    queryKey: ["media-search", params],
    queryFn: () => unwrap(api.GET("/api/media/search", { params: { query: params } })),
    enabled: enabled && params.q.trim().length > 0,
    staleTime: 5 * 60_000,
  });
}

export function useMdblistLists(enabled: boolean) {
  return useQuery({
    queryKey: ["mdblist-mine"],
    queryFn: () => unwrap(api.GET("/api/lists/mdblist/mine")),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

// -- flows -------------------------------------------------------------------------------
const running = (status: string | null | undefined) => status === "running";

export function useFlows() {
  return useQuery({
    queryKey: ["flows"],
    queryFn: () => unwrap(api.GET("/api/flows")),
    refetchInterval: (query) => (query.state.data?.flows.some((flow) => running(flow.last_run?.status)) ? 2500 : false),
  });
}

export function useFlow(id: number) {
  return useQuery({
    queryKey: ["flow", id],
    queryFn: () => unwrap(api.GET("/api/flows/{flow_id}", { params: { path: { flow_id: id } } })),
    refetchInterval: (query) => (running(query.state.data?.last_run?.status) ? 2500 : false),
  });
}

export function useNodeTypes() {
  return useQuery({ queryKey: ["flow-nodes"], queryFn: () => unwrap(api.GET("/api/flows/nodes")), staleTime: 60_000 });
}

export function useFlowRuns(flow: number, page: number) {
  return useQuery({
    queryKey: ["flow-runs", flow, page],
    queryFn: () => unwrap(api.GET("/api/flows/runs", { params: { query: { flow, page } } })),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.runs.some((run) => running(run.status)) ? 2500 : false),
  });
}

export function useFlowRun(id: number, enabled = true) {
  return useQuery({
    queryKey: ["flow-run", id],
    queryFn: () => unwrap(api.GET("/api/flows/runs/{run_id}", { params: { path: { run_id: id } } })),
    enabled: enabled && id > 0,
    refetchInterval: (query) => (running(query.state.data?.status) ? 2000 : false),
  });
}

export function useFlowRunTitles(id: number, params: { show: string; q: string; page: number }, enabled = true) {
  return useQuery({
    queryKey: ["flow-run-titles", id, params],
    queryFn: () => unwrap(api.GET("/api/flows/runs/{run_id}/titles", { params: { path: { run_id: id }, query: params } })),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useArrOptions(service: "radarr" | "sonarr", enabled: boolean) {
  return useQuery({
    queryKey: ["arr-options", service],
    queryFn: () => unwrap(api.GET("/api/arr/{service}/options", { params: { path: { service } } })),
    enabled,
    staleTime: 60_000,
  });
}

export function useScheduleCheck(value: string, enabled: boolean) {
  return useQuery({
    queryKey: ["flow-schedule", value],
    queryFn: () => unwrap(api.GET("/api/flows/schedule", { params: { query: { value } } })),
    enabled,
    staleTime: 30_000,
  });
}
