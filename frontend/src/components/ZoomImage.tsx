import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const WIDTH = 320;
const MARGIN = 8;
const GAP = 12;

type Place = { left: number; top: number; width: number };

function canHover(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;
}

// Beside the image, right if there is room, else left; never outside the window.
function place(rect: DOMRect): Place {
  const width = Math.min(WIDTH, (window.innerHeight - 2 * MARGIN) / 1.5);
  const height = width * 1.5;
  let left = rect.right + GAP;
  if (left + width > window.innerWidth - MARGIN) left = rect.left - GAP - width;
  if (left < MARGIN) left = Math.min(Math.max(MARGIN, rect.left), window.innerWidth - width - MARGIN);
  const top = Math.min(Math.max(rect.top + rect.height / 2 - height / 2, MARGIN), window.innerHeight - height - MARGIN);
  return { left, top, width };
}

/** A poster thumb that shows enlarged while the pointer rests on it. `large` replaces the thumb once loaded. */
export function ZoomImage({ src, large, className, alt = "" }: { src: string; large?: string | null; className?: string; alt?: string }) {
  const ref = useRef<HTMLImageElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const [at, setAt] = useState<Place | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);

  const show = () => {
    if (!canHover()) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (ref.current) setAt(place(ref.current.getBoundingClientRect()));
    }, 250);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    setAt(null);
  };

  useEffect(() => {
    if (!at) return;
    const close = () => setAt(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);

  useEffect(() => {
    if (!at || !large || loaded === large) return;
    const image = new Image();
    image.onload = () => setLoaded(large);
    image.src = large;
    return () => {
      image.onload = null;
    };
  }, [at, large, loaded]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <>
      <img ref={ref} src={src} alt={alt} loading="lazy" className={className} onMouseEnter={show} onMouseLeave={hide} />
      {at &&
        createPortal(
          <div
            className="zoom-preview pointer-events-none fixed z-[65] aspect-[2/3] overflow-hidden rounded-lg bg-gray-900 shadow-2xl ring-1 ring-gray-500"
            style={{ left: at.left, top: at.top, width: at.width }}
            aria-hidden="true"
          >
            <img src={large && loaded === large ? large : src} alt="" className="h-full w-full object-cover" />
          </div>,
          document.body,
        )}
    </>
  );
}
