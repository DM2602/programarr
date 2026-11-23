import { createContext, useCallback, useContext, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";
import { useFocusTrap } from "./useFocusTrap";

type ConfirmOptions = { title: string; body?: ReactNode; confirm?: string; danger?: boolean };
type PromptOptions = { title: string; label: string; initial?: string; placeholder?: string; hint?: ReactNode; confirm?: string; type?: string };

type DialogApi = {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
};

type Open =
  | { kind: "confirm"; options: ConfirmOptions; resolve: (value: boolean) => void }
  | { kind: "prompt"; options: PromptOptions; resolve: (value: string | null) => void };

const DialogContext = createContext<DialogApi | null>(null);

export function Modal({ title, children, footer, onClose, onSubmit }: {
  title: string;
  children?: ReactNode;
  footer: ReactNode;
  onClose: () => void;
  onSubmit?: (event: FormEvent) => void;
}) {
  const ref = useRef<HTMLFormElement>(null);
  const titleId = useId();
  useFocusTrap(ref, true, onClose);
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-end justify-center p-0 sm:items-center sm:p-4">
      <div className="absolute inset-0 bg-gray-900/80 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <form
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit?.(event);
        }}
        className="relative w-full overflow-hidden bg-gray-800 text-left shadow-xl ring-1 ring-gray-700 sm:max-w-lg sm:rounded-lg"
      >
        <div className="px-5 pt-5 pb-4">
          <h2 id={titleId} className="text-lg font-bold text-gray-100">
            {title}
          </h2>
          {children && <div className="mt-2 text-sm text-gray-300">{children}</div>}
        </div>
        <div className="flex flex-row-reverse flex-wrap gap-2 border-t border-gray-700 bg-gray-800 px-5 py-3">{footer}</div>
      </form>
    </div>,
    document.body,
  );
}

function PromptBody({ options, inputId, value, setValue }: { options: PromptOptions; inputId: string; value: string; setValue: (v: string) => void }) {
  return (
    <div className="mt-3">
      <label htmlFor={inputId} className="mb-1 block text-sm font-semibold text-gray-300">
        {options.label}
      </label>
      <input
        id={inputId}
        className="field-input"
        type={options.type ?? "text"}
        value={value}
        placeholder={options.placeholder}
        onChange={(event) => setValue(event.target.value)}
        autoFocus
      />
      {options.hint && <p className="mt-1 text-xs text-gray-400">{options.hint}</p>}
    </div>
  );
}

export function DialogProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<Open | null>(null);
  const [value, setValue] = useState("");
  const inputId = useId();

  const confirm = useCallback(
    (options: ConfirmOptions) => new Promise<boolean>((resolve) => setOpen({ kind: "confirm", options, resolve })),
    [],
  );
  const prompt = useCallback((options: PromptOptions) => {
    setValue(options.initial ?? "");
    return new Promise<string | null>((resolve) => setOpen({ kind: "prompt", options, resolve }));
  }, []);

  const finish = (result: boolean) => {
    if (!open) return;
    if (open.kind === "confirm") open.resolve(result);
    else open.resolve(result ? value.trim() : null);
    setOpen(null);
  };

  const api = useMemo(() => ({ confirm, prompt }), [confirm, prompt]);

  return (
    <DialogContext.Provider value={api}>
      {children}
      {open && (
        <Modal
          title={open.options.title}
          onClose={() => finish(false)}
          onSubmit={() => finish(true)}
          footer={
            <>
              <Button type="submit" variant={open.kind === "confirm" && open.options.danger ? "danger" : "primary"} autoFocus={open.kind === "confirm"}>
                {open.options.confirm ?? "OK"}
              </Button>
              <Button onClick={() => finish(false)}>Cancel</Button>
            </>
          }
        >
          {open.kind === "confirm" ? open.options.body : <PromptBody options={open.options} inputId={inputId} value={value} setValue={setValue} />}
        </Modal>
      )}
    </DialogContext.Provider>
  );
}

export function useDialogs(): DialogApi {
  const value = useContext(DialogContext);
  if (!value) throw new Error("useDialogs outside DialogProvider");
  return value;
}
