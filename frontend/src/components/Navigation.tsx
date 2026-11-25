import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { Copy } from "../copy/copy";
import {
  navigationGroups,
  utilityNavigation,
  type NavigationItem,
} from "../routes/routes";
import { workerClient } from "../services/workerClient";
import { useWorkerStore } from "../stores/workerStore";
import { StatusPill } from "./StatusPill";

function linkClass(isActive: boolean, small = false): string {
  return `flex items-center justify-between gap-2 rounded-md ${
    small ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm"
  } font-medium transition ${
    isActive
      ? "bg-brand-600 text-white shadow-sm"
      : "text-slate-300 hover:bg-white/5 hover:text-white"
  }`;
}

function NavItemLink({
  item,
  badge,
  onNavigate,
  small,
}: {
  item: NavigationItem;
  badge?: number;
  onNavigate?: () => void;
  small?: boolean;
}) {
  return (
    <NavLink
      to={item.to}
      title={item.description}
      end={item.to === "/"}
      onClick={onNavigate}
      className={({ isActive }) => linkClass(isActive, small)}
    >
      <span>{item.label}</span>
      {typeof badge === "number" && badge > 0 ? (
        <span className="tabular rounded-full bg-amber-400 px-1.5 py-0.5 text-[10px] font-bold text-ink-900">
          {badge > 99 ? "99+" : badge}
        </span>
      ) : null}
    </NavLink>
  );
}

/** Grouped sidebar navigation in workflow order. */
export function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation();
  const status = useWorkerStore((state) => state.status);
  const isPreview = useWorkerStore((state) => state.isPreview);
  const [insightsOpen, setInsightsOpen] = useState(true);
  const [hasResume, setHasResume] = useState<boolean | null>(null);
  const [readyToApply, setReadyToApply] = useState(0);
  const [followUps, setFollowUps] = useState(0);

  const loadCounts = useCallback(async () => {
    try {
      const [resume, saved, applications] = await Promise.all([
        workerClient.listResumeVersions(),
        workerClient.listSavedJobs({ view: "ready", limit: 1 }),
        workerClient.listApplications({ limit: 1 }),
      ]);
      setHasResume((resume.versions ?? []).length > 0);
      setReadyToApply(saved.total ?? saved.jobs.length);
      setFollowUps(applications.summary?.follow_up ?? 0);
    } catch {
      setHasResume(null);
    }
  }, []);

  useEffect(() => {
    void loadCounts();
  }, [loadCounts, location.pathname]);

  useEffect(() => {
    const onCountsChanged = () => void loadCounts();
    window.addEventListener("jobsignal:counts-changed", onCountsChanged);
    return () => window.removeEventListener("jobsignal:counts-changed", onCountsChanged);
  }, [loadCounts]);

  const badges: Record<string, number> = {
    "/saved": readyToApply,
    "/applications": followUps,
  };

  return (
    <div className="flex h-full flex-col bg-ink-900 text-slate-200">
      <div className="px-4 py-5">
        <Link to="/" onClick={onNavigate} className="block">
          <span className="flex items-center gap-2 text-lg font-bold tracking-tight text-white">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-500 text-sm font-black text-ink-900">
              JS
            </span>
            {Copy.productName}
          </span>
          <span className="mt-1 block pl-9 text-xs text-slate-400">
            {Copy.productNameSubtitle}
          </span>
        </Link>
        <div className="mt-3">
          <StatusPill status={status} preview={isPreview} />
        </div>
      </div>

      <nav aria-label="Primary" className="flex-1 space-y-5 overflow-y-auto px-3 pb-4">
        {navigationGroups.map((group) => (
          <div key={group.label}>
            {group.collapsible ? (
              <button
                type="button"
                aria-expanded={insightsOpen}
                onClick={() => setInsightsOpen((open) => !open)}
                className="flex w-full items-center justify-between px-2 py-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500 transition hover:text-slate-300"
              >
                {group.label}
                <span aria-hidden="true">{insightsOpen ? "−" : "+"}</span>
              </button>
            ) : (
              <p className="px-2 py-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500">
                {group.label}
              </p>
            )}
            {group.collapsible && !insightsOpen ? null : (
              <ul className="mt-1 space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.to}>
                    <NavItemLink
                      item={item}
                      badge={badges[item.to]}
                      onNavigate={onNavigate}
                      small={Boolean(group.collapsible)}
                    />
                    {item.to === "/resume" ? (
                      <span
                        className={`ml-3 block text-[11px] ${
                          hasResume ? "text-emerald-400" : "text-amber-300"
                        }`}
                      >
                        {hasResume === null
                          ? "Resume not checked"
                          : hasResume
                            ? "Ready"
                            : "Setup needed"}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </nav>

      <div className="border-t border-white/10 px-3 py-3">
        <ul className="space-y-0.5">
          {utilityNavigation.map((item) => (
            <li key={item.to}>
              <NavItemLink item={item} onNavigate={onNavigate} />
            </li>
          ))}
        </ul>
        <p className="mt-2 px-3 text-[11px] text-slate-500">
          Local-first. Never submits.
        </p>
      </div>
    </div>
  );
}
