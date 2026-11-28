import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { onUnauthorized, type Schemas } from "./api/client";
import { useAuthState } from "./api/queries";
import { Button } from "./components/Button";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { Login } from "./features/auth/Login";
import { CollectionPage } from "./features/collections/CollectionPage";
import { Collections } from "./features/collections/Collections";
import { Queue } from "./features/collections/Queue";
import { Dashboard } from "./features/dashboard/Dashboard";
import { FlowHistory } from "./features/flows/FlowHistory";
import { FlowRun } from "./features/flows/FlowRun";
import { Flows } from "./features/flows/Flows";
import { Exclusions } from "./features/lists/Exclusions";
import { Lists } from "./features/lists/Lists";
import { MatchLog } from "./features/lists/MatchLog";
import { RunPage } from "./features/lists/RunPage";
import { History } from "./features/posters/History";
import { ItemPage } from "./features/posters/ItemPage";
import { Library } from "./features/posters/Library";
import { Match } from "./features/posters/Match";
import { Review } from "./features/posters/Review";
import { Pending } from "./features/posters/Pending";
import { Settings } from "./features/settings/Settings";
import { Logs } from "./features/system/Logs";
import { NotFound } from "./features/system/NotFound";

// The editor brings React Flow along; it loads when a flow is opened.
const FlowEditor = lazy(() => import("./features/flows/FlowEditor").then((module) => ({ default: module.FlowEditor })));

function RequireAuth({ children }: { children: ReactNode }) {
  const { data, isPending, isError, refetch } = useAuthState();
  const location = useLocation();
  if (isPending) return <Loading text="Connecting…" />;
  if (isError || !data) {
    return (
      <div className="mx-auto mt-24 max-w-md px-4 text-center">
        <p className="text-lg font-semibold text-gray-100">Programarr is not reachable</p>
        <p className="mt-1 text-sm text-gray-400">Check that the container is running.</p>
        <Button className="mt-4" onClick={() => refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!data.signed_in) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;
  }
  return <>{children}</>;
}

export function App() {
  const queryClient = useQueryClient();

  useEffect(() => {
    onUnauthorized(() => {
      queryClient.setQueryData<Schemas["AuthState"]>(["auth"], (state) => (state ? { ...state, signed_in: false } : state));
    });
    return () => onUnauthorized(null);
  }, [queryClient]);

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="library" element={<Library />} />
        <Route path="item/:key" element={<ItemPage />} />
        <Route path="review" element={<Review />} />
        <Route path="pending" element={<Pending />} />
        <Route path="waiting" element={<Navigate to="/pending" replace />} />
        <Route path="match" element={<Match />} />
        <Route path="history" element={<History />} />
        <Route path="lists" element={<Lists />} />
        <Route path="lists/runs/:id" element={<RunPage />} />
        <Route path="lists/matches" element={<MatchLog />} />
        <Route path="lists/exclusions" element={<Exclusions />} />
        <Route path="flows" element={<Flows />} />
        <Route path="flows/history" element={<FlowHistory />} />
        <Route path="flows/runs/:id" element={<FlowRun />} />
        <Route
          path="flows/:id"
          element={
            <Suspense fallback={<Loading />}>
              <FlowEditor />
            </Suspense>
          }
        />
        <Route path="collections" element={<Collections />} />
        <Route path="collections/queue" element={<Queue />} />
        <Route path="collections/:id" element={<CollectionPage />} />
        <Route path="logs" element={<Logs />} />
        <Route path="settings" element={<Navigate to="/settings/plex" replace />} />
        <Route path="settings/:section" element={<Settings />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
