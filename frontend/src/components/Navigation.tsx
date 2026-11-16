import { NavLink } from "react-router-dom";
import { Copy } from "../copy/copy";
import { StatusPill } from "./StatusPill";
import { navigationItems } from "../routes/routes";
import { useWorkerStore } from "../stores/workerStore";

export function Navigation() {
  const status = useWorkerStore((state) => state.status);

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold tracking-tight text-slate-900">
            {Copy.productName}
          </span>
          <span className="hidden text-xs text-slate-500 sm:inline">
            {Copy.productNameSubtitle}
          </span>
          <StatusPill status={status} />
        </div>
        <nav aria-label="Primary" className="flex flex-wrap gap-1">
          {navigationItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                  isActive
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
