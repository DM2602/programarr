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
