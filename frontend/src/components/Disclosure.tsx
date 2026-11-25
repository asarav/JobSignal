import { useId, useState, type ReactNode } from "react";

/** Summary first, details only when asked for. Content is not mounted closed. */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  hint,
}: {
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
  hint?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span>
          <span className="text-sm font-medium text-slate-900">{summary}</span>
          {hint ? <span className="mt-0.5 block text-xs text-slate-500">{hint}</span> : null}
        </span>
        <span aria-hidden="true" className="text-slate-400">
          {open ? "−" : "+"}
        </span>
      </button>
      {open ? (
        <div id={id} className="border-t border-slate-100 px-4 py-3">
          {children}
        </div>
      ) : null}
    </div>
  );
}
