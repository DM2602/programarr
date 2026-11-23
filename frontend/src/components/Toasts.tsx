import { CheckCircleIcon, ExclamationCircleIcon, InformationCircleIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { Spinner } from "./ui";

type Kind = "info" | "success" | "error";
type Options = { kind?: Kind; spinner?: boolean; timeout?: number };
type Toast = { id: number; text: string; kind: Kind; spinner: boolean };

export type ToastHandle = {
  update: (text: string, options?: Options) => void;
  close: () => void;
};

type ToastApi = { show: (text: string, options?: Options) => ToastHandle };

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const close = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setToasts((list) => list.filter((toast) => toast.id !== id));
  }, []);

  const schedule = useCallback(
    (id: number, timeout: number) => {
      window.clearTimeout(timers.current.get(id));
      if (timeout > 0) timers.current.set(id, window.setTimeout(() => close(id), timeout));
    },
    [close],
  );

  const show = useCallback(
    (text: string, { kind = "info", spinner = false, timeout = 4200 }: Options = {}): ToastHandle => {
      const id = nextId.current++;
      setToasts((list) => [...list.slice(-4), { id, text, kind, spinner }]);
      if (!spinner) schedule(id, timeout);
      return {
        update(message, options = {}) {
          const nextKind = options.kind ?? "info";
          const nextSpinner = options.spinner ?? false;
          setToasts((list) => list.map((toast) => (toast.id === id ? { ...toast, text: message, kind: nextKind, spinner: nextSpinner } : toast)));
          if (!nextSpinner) schedule(id, options.timeout ?? 4200);
        },
        close: () => close(id),
      };
    },
    [schedule, close],
  );

  const value = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-[70] flex w-[min(26rem,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.kind === "error" ? "alert" : "status"}
            data-kind={toast.kind}
            className="toast pointer-events-auto flex items-start gap-3 rounded-lg bg-gray-800 px-4 py-3 text-sm text-gray-100 shadow-xl ring-1 ring-gray-700"
          >
            <span className="mt-0.5 shrink-0">
              {toast.spinner ? (
                <Spinner className="h-5 w-5 text-indigo-400" />
              ) : toast.kind === "success" ? (
                <CheckCircleIcon className="h-5 w-5 text-green-400" />
              ) : toast.kind === "error" ? (
                <ExclamationCircleIcon className="h-5 w-5 text-red-400" />
              ) : (
                <InformationCircleIcon className="h-5 w-5 text-indigo-300" />
              )}
            </span>
            <span className="min-w-0 flex-1 break-words">{toast.text}</span>
            <button type="button" className="shrink-0 rounded text-gray-400 hover:text-white" onClick={() => close(toast.id)} aria-label="Close">
              <XMarkIcon className="h-5 w-5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast outside ToastProvider");
  return value;
}
