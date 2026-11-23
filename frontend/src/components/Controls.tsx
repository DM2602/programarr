import { useId, type ReactNode } from "react";
import { Link } from "react-router";

type Option = { value: string; label: ReactNode };

/** A row of buttons for a few exclusive choices. */
export function Segmented({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Option[] }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md shadow-sm ring-1 ring-gray-600">
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`px-3 py-2 text-sm font-medium transition ${index === 0 ? "rounded-l-md" : ""} ${
              index === options.length - 1 ? "rounded-r-md" : "border-r border-gray-600"
            } ${active ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-300 hover:bg-gray-700 hover:text-white"}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function SelectField({ label, id, value, onChange, options }: { label: string; id?: string; value: string; onChange: (value: string) => void; options: Option[] }) {
  const fallback = useId();
  const fieldId = id ?? fallback;
  return (
    <div className="flex flex-col">
      <label htmlFor={fieldId} className="mb-1 text-xs font-semibold text-gray-400">
        {label}
      </label>
      <select
        id={fieldId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-gray-600 bg-gray-800 py-2 pr-8 pl-3 text-sm text-gray-100 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Underlined tabs: links when "to" is given, buttons otherwise. */
export function Tabs({ items, label }: { label: string; items: { key: string; label: ReactNode; to?: string; active?: boolean; onClick?: () => void; count?: number }[] }) {
  return (
    <div className="scroll-x -mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
      <nav className="flex min-w-max gap-6 border-b border-gray-700" aria-label={label}>
        {items.map((item) => {
          const classes = (active: boolean) =>
            `-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-1 py-3 text-sm font-medium transition ${
              active ? "border-indigo-500 text-indigo-300" : "border-transparent text-gray-400 hover:border-gray-400 hover:text-gray-200"
            }`;
          const count = item.count ? <span className="rounded-full bg-gray-700 px-2 text-xs leading-5 text-gray-200">{item.count}</span> : null;
          return item.to ? (
            <Link key={item.key} to={item.to} className={classes(!!item.active)} aria-current={item.active ? "page" : undefined}>
              {item.label}
              {count}
            </Link>
          ) : (
            <button key={item.key} type="button" role="tab" aria-selected={!!item.active} className={classes(!!item.active)} onClick={item.onClick} data-tab={item.key}>
              {item.label}
              {count}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
