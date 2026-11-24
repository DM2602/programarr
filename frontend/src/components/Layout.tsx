import {
  ArchiveBoxIcon,
  ArrowRightStartOnRectangleIcon,
  Bars3Icon,
  ClipboardDocumentListIcon,
  ClockIcon,
  Cog6ToothIcon,
  DocumentTextIcon,
  HomeIcon,
  InboxStackIcon,
  LinkIcon,
  MagnifyingGlassIcon,
  NoSymbolIcon,
  QueueListIcon,
  ShareIcon,
  Squares2X2Icon,
  TableCellsIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ComponentType, type FormEvent, type SVGProps } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";
import { jobProgress } from "../api/jobs";
import { useAuthState, useStatus } from "../api/queries";
import { Logo } from "./Logo";
import { Spinner } from "./ui";
import { useFocusTrap } from "./useFocusTrap";

type Icon = ComponentType<SVGProps<SVGSVGElement>>;
type NavItem = { to: string; label: string; icon: Icon; count?: number; end?: boolean };

function NavEntry({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        `group flex items-center gap-3 rounded-md px-3 py-2 text-base font-medium leading-6 text-white transition duration-150 ${
          isActive ? "bg-gradient-to-br from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500" : "hover:bg-gray-700"
        }`
      }
    >
      <item.icon className="h-6 w-6 shrink-0" aria-hidden="true" />
      <span className="flex-1 truncate">{item.label}</span>
      {!!item.count && (
        <span className="rounded-full bg-gray-900/70 px-2 text-xs font-semibold leading-5 text-gray-100 ring-1 ring-gray-600" aria-label={`${item.count} open`}>
          {item.count}
        </span>
      )}
    </NavLink>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { data: status } = useStatus();
  const counts = status?.counts;
  const main: NavItem[] = [{ to: "/", label: "Dashboard", icon: HomeIcon, end: true }];
  const posters: NavItem[] = [
    { to: "/library", label: "Library", icon: Squares2X2Icon },
    { to: "/review", label: "Review", icon: InboxStackIcon, count: counts?.review },
    { to: "/waiting", label: "Waiting", icon: ClockIcon, count: counts?.waiting },
    { to: "/match", label: "Match existing", icon: LinkIcon },
    { to: "/history", label: "History", icon: ArchiveBoxIcon },
  ];
  const lists: NavItem[] = [
    { to: "/lists", label: "Lists", icon: QueueListIcon, end: true },
    { to: "/lists/matches", label: "Match log", icon: TableCellsIcon, count: counts?.match_open },
    { to: "/lists/exclusions", label: "Exclusions", icon: NoSymbolIcon },
  ];
  const flows: NavItem[] = [
    { to: "/flows", label: "Flows", icon: ShareIcon, end: true },
    { to: "/flows/history", label: "Flow history", icon: ClipboardDocumentListIcon },
  ];
  const system: NavItem[] = [
    { to: "/logs", label: "Logs", icon: DocumentTextIcon },
    { to: "/settings", label: "Settings", icon: Cog6ToothIcon },
  ];
  return (
    <div className="flex h-full flex-col">
      <Link to="/" onClick={onNavigate} className="flex h-16 shrink-0 items-center gap-2.5 px-5" aria-label="Programarr dashboard">
        <Logo className="h-8 w-8" />
        <span className="text-xl font-bold tracking-tight text-white">Programarr</span>
      </Link>
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 pb-4" aria-label="Main">
        {main.map((item) => (
          <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
        ))}
        <p className="mt-5 mb-1 px-3 text-sm font-semibold text-gray-500">Posters</p>
        {posters.map((item) => (
          <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
        ))}
        <p className="mt-5 mb-1 px-3 text-sm font-semibold text-gray-500">Lists</p>
        {lists.map((item) => (
          <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
        ))}
        <p className="mt-5 mb-1 px-3 text-sm font-semibold text-gray-500">Flows</p>
        {flows.map((item) => (
          <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
        ))}
        <div className="mt-auto pt-6">
          {status && !status.automation && (
            <Link
              to="/settings/automation"
              onClick={onNavigate}
              className="mb-3 block rounded-md border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-100 hover:border-yellow-400/70"
            >
              Automation is off
            </Link>
          )}
          {system.map((item) => (
            <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
          ))}
          <p className="mt-3 px-3 text-xs text-gray-500">Programarr {status?.version}</p>
        </div>
      </nav>
    </div>
  );
}

function JobIndicator() {
  const { data: status } = useStatus();
  const jobs = status?.jobs ?? [];
  if (!jobs.length) return null;
  const first = jobs[0];
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-full bg-gray-800 px-3 py-1.5 text-sm text-gray-200 ring-1 ring-gray-700" role="status" title={jobProgress(first)}>
      <Spinner className="h-4 w-4 shrink-0 text-indigo-400" />
      <span className="hidden max-w-[16rem] truncate sm:inline">{jobProgress(first)}</span>
      {jobs.length > 1 && <span className="text-gray-400">+{jobs.length - 1}</span>}
    </div>
  );
}

function SearchBox() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const current = location.pathname === "/library" ? params.get("q") ?? "" : "";
  const [value, setValue] = useState(current);

  // Follow the address: a cleared filter or another page empties the box.
  useEffect(() => setValue(current), [current]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    // On the library, the search narrows the current filters; elsewhere it starts fresh.
    const next = new URLSearchParams(location.pathname === "/library" ? params : undefined);
    const q = value.trim();
    if (q) next.set("q", q);
    else next.delete("q");
    next.delete("page");
    const query = next.toString();
    navigate(query ? `/library?${query}` : "/library");
  };

  return (
    <form role="search" onSubmit={submit} className="relative w-full max-w-xl">
      <MagnifyingGlassIcon className="pointer-events-none absolute top-1/2 left-3 h-5 w-5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
      <input
        type="search"
        id="global-search"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Search titles"
        aria-label="Search titles"
        className="block w-full rounded-full border border-gray-600 bg-gray-900/80 py-2 pr-4 pl-10 text-sm text-white placeholder:text-gray-400 hover:border-gray-500 focus:border-gray-400 focus:bg-gray-900 focus:outline-none"
      />
    </form>
  );
}

function SignOut() {
  const { data: auth } = useAuthState();
  const queryClient = useQueryClient();
  if (!auth?.password_required) return null;
  const signOut = async () => {
    await unwrap(api.POST("/api/auth/logout")).catch(() => null);
    queryClient.clear();
    window.location.assign("/login");
  };
  return (
    <button type="button" onClick={signOut} className="rounded-full p-2 text-gray-300 hover:bg-gray-800 hover:text-white" title="Sign out">
      <ArrowRightStartOnRectangleIcon className="h-6 w-6" aria-hidden="true" />
      <span className="sr-only">Sign out</span>
    </button>
  );
}

function MobileSidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
      <div className="absolute inset-0 bg-gray-900/80" onClick={onClose} aria-hidden="true" />
      <div ref={ref} className="relative flex h-full w-72 max-w-[85vw] flex-col border-r border-gray-800 bg-gray-900 shadow-xl">
        <button type="button" onClick={onClose} className="absolute top-4 right-3 rounded-md p-1 text-gray-400 hover:text-white" aria-label="Close menu">
          <XMarkIcon className="h-6 w-6" />
        </button>
        <SidebarContent onNavigate={onClose} />
      </div>
    </div>
  );
}

export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-gray-800 bg-gray-900 lg:block">
        <SidebarContent />
      </aside>
      <MobileSidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-gray-800/60 bg-gray-900/80 px-4 backdrop-blur md:px-8">
          <button type="button" className="-ml-1 rounded-md p-1.5 text-gray-300 hover:text-white lg:hidden" onClick={() => setMenuOpen(true)} aria-label="Open menu">
            <Bars3Icon className="h-6 w-6" />
          </button>
          <SearchBox />
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <JobIndicator />
            <SignOut />
          </div>
        </header>
        <main id="main" className="px-4 pb-20 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
