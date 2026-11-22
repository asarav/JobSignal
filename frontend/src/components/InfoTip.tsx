import { useId, useState, type ReactNode } from "react";

/**
 * Small "?" affordance that shows an explanation on hover, focus or tap.
 *
 * The bubble is plain positioned content rather than a native `title`, so it
 * is readable with a keyboard and on touch. `aria-describedby` ties the text
 * to the trigger while it is visible.
 */
export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <span
      className="relative inline-flex items-center align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={`About ${label}`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
        className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white text-[10px] font-semibold leading-none text-slate-500 hover:border-slate-400 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
      >
        ?
      </button>
      {open ? (
        <span
          role="tooltip"
          id={id}
          className="absolute left-0 top-full z-30 mt-1 w-64 rounded-md border border-slate-200 bg-white p-2 text-left text-xs font-normal leading-5 text-slate-600 shadow-lg"
        >
          {children}
        </span>
      ) : null}
    </span>
  );
}
