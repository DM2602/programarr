import { siImdb } from "simple-icons";

/** A brand mark from simple-icons (CC0), in its brand colour. */
/** A brand mark in the text colour, for marks inside coloured badges. */
export function MonoBrandIcon({ icon, className = "h-3.5 w-3.5" }: { icon: { path: string }; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={`shrink-0 ${className}`} fill="currentColor" aria-hidden="true">
      <path d={icon.path} />
    </svg>
  );
}

export function BrandIcon({ icon, className = "h-4 w-4" }: { icon: { path: string; hex: string }; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={`shrink-0 ${className}`} fill={`#${icon.hex}`} aria-hidden="true">
      <path d={icon.path} />
    </svg>
  );
}

/**
 * IMDb as it looks: black letters on yellow. The simple-icons mark draws yellow letters in a frame;
 * this crops it to the letters and puts them on a yellow field.
 */
export function ImdbMark({ className = "h-4" }: { className?: string }) {
  return (
    <svg viewBox="1.7 7.08 20.75 9.84" className={`w-auto shrink-0 ${className}`} aria-hidden="true">
      <rect x="1.7" y="7.08" width="20.75" height="9.84" rx="1.3" fill={`#${siImdb.hex}`} />
      <path d={siImdb.path} fill="#000" />
    </svg>
  );
}
