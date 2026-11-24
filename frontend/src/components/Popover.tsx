import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const WIDTH = 352;
const GAP = 8;

/** A button that opens a small panel next to it. Closes on Escape, outside clicks and scrolling away. */
export function Popover({ button, label, buttonClass, title, children, data }: {
  button: ReactNode;
  label: string;
  buttonClass: string;
  title?: string;
  children: (close: () => void) => ReactNode;
  data?: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<{ top: number; left: number; above: boolean } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = useCallback(() => {
    setOpen(false);
    trigger.current?.focus();
  }, []);

  const measure = useCallback(() => {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const height = panel.current?.offsetHeight ?? 320;
    const above = rect.bottom + GAP + height > window.innerHeight && rect.top - GAP - height > 0;
    const left = Math.min(Math.max(GAP, rect.right - WIDTH), window.innerWidth - WIDTH - GAP);
    setPlace({ top: above ? rect.top - GAP - height : rect.bottom + GAP, left: Math.max(GAP, left), above });
  }, []);

  useLayoutEffect(() => {
    if (open) measure();
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !trigger.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    panel.current?.querySelector<HTMLElement>("button, a")?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, close, measure]);

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={buttonClass}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? id : undefined}
        title={title}
        onClick={() => setOpen((value) => !value)}
        {...data}
      >
        {button}
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            id={id}
            role="dialog"
            aria-label={label}
            className="fixed z-40 rounded-lg border border-gray-600 bg-gray-800 p-4 text-sm shadow-2xl ring-1 ring-black/40"
            style={{ top: place?.top ?? -9999, left: place?.left ?? -9999, width: `min(${WIDTH}px, calc(100vw - ${GAP * 2}px))` }}
          >
            {children(close)}
          </div>,
          document.body,
        )}
    </>
  );
}
