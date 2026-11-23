import type { ReactNode } from "react";

export type Tone = "indigo" | "green" | "yellow" | "red" | "gray" | "dark" | "purple" | "blue";

const TONES: Record<Tone, string> = {
  indigo: "border-indigo-500 bg-indigo-500/80 text-indigo-50",
  green: "border-green-500 bg-green-500/80 text-green-50",
  yellow: "border-yellow-500 bg-yellow-500/80 text-yellow-50",
  red: "border-red-500 bg-red-600/80 text-red-50",
  gray: "border-gray-600 bg-gray-700 text-gray-300",
  dark: "border-gray-700 bg-gray-900/90 text-gray-300",
  purple: "border-purple-500 bg-purple-600/80 text-purple-50",
  blue: "border-blue-500 bg-blue-600/80 text-blue-50",
};

export function Badge({ tone = "indigo", children, title, icon }: { tone?: Tone; children: ReactNode; title?: string; icon?: ReactNode }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 text-xs font-semibold leading-5 ${TONES[tone]}`}
    >
      {icon}
      {children}
    </span>
  );
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={`animate-spin text-current ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21.5 12a9.5 9.5 0 0 0-9.5-9.5" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Loading({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-sm text-gray-400" role="status">
      <Spinner />
      {text}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mt-6 mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-bold leading-8 text-gray-100 sm:text-3xl sm:leading-10">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-gray-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, aside, children, id }: { title: ReactNode; aside?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="mt-10" aria-labelledby={id}>
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 id={id} className="text-xl font-bold leading-7 text-gray-300">
          {title}
        </h2>
        {aside && <div className="text-sm">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-gray-700 px-6 py-12 text-center">
      <p className="font-semibold text-gray-200">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-md text-sm text-gray-400">{children}</p>}
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

export function Notice({ tone = "yellow", children }: { tone?: "yellow" | "red" | "indigo"; children: ReactNode }) {
  const styles = {
    yellow: "border-yellow-500/50 bg-yellow-500/10 text-yellow-100",
    red: "border-red-500/50 bg-red-500/10 text-red-100",
    indigo: "border-indigo-500/50 bg-indigo-500/10 text-indigo-100",
  }[tone];
  return <div className={`rounded-md border px-4 py-3 text-sm ${styles}`}>{children}</div>;
}

export function ProgressBar({ done, total }: { done: number; total: number }) {
  const percent = total ? Math.min(100, Math.round((100 * done) / total)) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-700" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-indigo-500 transition-[width] duration-500" style={{ width: `${percent}%` }} />
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-gray-700 bg-gray-800 ${className}`}>{children}</div>;
}

/** Poster image in a 2:3 frame. "fallback" dims what Plex shows when no file is in the folder. */
export function Poster({ src, alt = "", fallback = false, className = "", children }: {
  src: string;
  alt?: string;
  fallback?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`relative aspect-[2/3] overflow-hidden rounded-lg bg-gray-800 shadow-poster ring-1 ring-gray-700 ${className}`}>
      <img src={src} alt={alt} loading="lazy" className={`h-full w-full object-cover ${fallback ? "opacity-60 grayscale-[35%]" : ""}`} />
      {children}
    </div>
  );
}
