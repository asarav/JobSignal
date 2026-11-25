import type { ReactNode } from "react";

export function SectionCard({
  title,
  description,
  actions,
  children,
  ariaLabel,
  className = "",
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  ariaLabel?: string;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-slate-200 bg-white p-4 shadow-sm ${className}`.trim()}
      aria-label={ariaLabel ?? title}
    >
      {title || actions ? (
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            {title ? (
              <h2 className="text-base font-semibold text-slate-900">{title}</h2>
            ) : null}
            {description ? (
              <p className="mt-1 max-w-3xl text-xs text-slate-500">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={title || actions ? "mt-3" : ""}>{children}</div>
    </section>
  );
}
