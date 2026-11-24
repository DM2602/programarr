/** A brand mark from simple-icons (CC0), in its brand colour. */
export function BrandIcon({ icon, className = "h-4 w-4" }: { icon: { path: string; hex: string }; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={`shrink-0 ${className}`} fill={`#${icon.hex}`} aria-hidden="true">
      <path d={icon.path} />
    </svg>
  );
}
