import { XMarkIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "./useFocusTrap";

export type DrawerState = { entered: boolean; open: () => void; close: () => void };

/** Slide-in state: close() plays the way out, then calls onClose. */
export function useDrawer(onClose: () => void): DrawerState {
  const [entered, setEntered] = useState(false);
  const closing = useRef(onClose);
  useEffect(() => {
    closing.current = onClose;
  });
  const open = useCallback(() => setEntered(true), []);
  const close = useCallback(() => {
    setEntered(false);
    window.setTimeout(() => closing.current(), 220);
  }, []);
  return { entered, open, close };
}

/** A panel from the right edge, over the page: heading, tabs or other header content, body, footer. */
export function Drawer({ drawer, heading, subtitle, header, footer, children, className = "" }: {
  drawer: DrawerState;
  heading: ReactNode;
  subtitle?: ReactNode;
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const { entered, open, close } = drawer;
  useFocusTrap(panelRef, true, close);

  useEffect(() => {
    const frame = requestAnimationFrame(open);
    panelRef.current?.querySelector<HTMLElement>("[data-close]")?.focus();
    return () => cancelAnimationFrame(frame);
  }, [open]);

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className={`absolute inset-0 bg-gray-900/70 transition-opacity duration-200 ${entered ? "opacity-100" : "opacity-0"}`} onClick={close} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col bg-gray-800 shadow-2xl ring-1 ring-gray-700 transition-transform duration-200 ease-out ${
          entered ? "translate-x-0" : "translate-x-full"
        } ${className}`}
        data-open={entered || undefined}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div className="min-w-0">
            <h2 id={titleId} className="truncate text-lg font-bold text-gray-100">
              {heading}
            </h2>
            {subtitle && <p className="text-sm text-gray-400">{subtitle}</p>}
          </div>
          <button type="button" data-close onClick={close} className="rounded-md p-1 text-gray-400 hover:bg-gray-700 hover:text-white" aria-label="Close">
            <XMarkIcon className="h-6 w-6" />
          </button>
        </div>
        {header && <div className="px-5">{header}</div>}
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex items-center gap-3 border-t border-gray-700 bg-gray-800 px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
