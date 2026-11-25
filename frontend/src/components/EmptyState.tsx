import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

const TONES = {
  brand: "border-brand-200 bg-brand-50 text-brand-700",
  amber: "border-amber-200 bg-amber-50 text-amber-800",
  rose: "border-rose-200 bg-rose-50 text-rose-700",
  emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
  slate: "border-line bg-slate-50 text-slate-600",
} as const;

export function EmptyState({
  heading,
  body,
  action,
  icon,
  tone = "brand",
}: {
  heading: string;
  body: string;
  action?: ReactNode;
  icon?: IconName;
  tone?: keyof typeof TONES;
}) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
      {icon ? (
        <span
          className={`mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full border ${TONES[tone]}`}
        >
          <Icon name={icon} className="h-5 w-5" />
        </span>
      ) : null}
      <p className="text-sm font-semibold text-slate-700">{heading}</p>
      <p className="mx-auto mt-1 max-w-xl text-sm text-slate-500">{body}</p>
      {action ? <div className="mt-3 flex justify-center">{action}</div> : null}
    </div>
  );
}
