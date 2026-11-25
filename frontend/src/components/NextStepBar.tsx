import { Link } from "react-router-dom";
import { buttonClass } from "./Button";

export function NextStepBar({
  text,
  cta,
  to,
}: {
  text: string;
  cta: string;
  to: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-300 bg-slate-100 px-4 py-3">
      <p className="text-sm text-slate-700">{text}</p>
      <Link to={to} className={buttonClass("primary")}>
        {cta} →
      </Link>
    </div>
  );
}
