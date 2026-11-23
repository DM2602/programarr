import { useId } from "react";

/** App mark: a programme guide in three rows, indigo to purple like the active navigation. */
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  const id = useId();
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6366f1" />
          <stop offset="1" stopColor="#9333ea" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${id})`} />
      <rect x="8" y="8" width="16" height="4" rx="2" fill="#fff" />
      <rect x="8" y="14" width="11" height="4" rx="2" fill="#fff" fillOpacity=".85" />
      <rect x="8" y="20" width="6.5" height="4" rx="2" fill="#fff" fillOpacity=".7" />
    </svg>
  );
}
