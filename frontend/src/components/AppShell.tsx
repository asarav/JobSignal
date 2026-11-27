import { useEffect, useState, type ReactNode } from "react";
import { Navigation } from "./Navigation";
import { DiscoveryActivity } from "../features/discover/DiscoveryActivity";

/** Persistent sidebar on wide screens; a disclosure menu on narrow ones. */
export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="min-h-screen bg-canvas text-slate-900 lg:flex">
      <aside className="hidden w-60 shrink-0 bg-ink-900 lg:block">
        <div className="sticky top-0 flex h-screen flex-col">
          <Navigation />
          <div className="mt-auto">
            <DiscoveryActivity tone="dark" />
          </div>
        </div>
      </aside>

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-white/10 bg-ink-900 px-4 py-3 text-white lg:hidden">
        <span className="flex items-center gap-2 text-base font-bold tracking-tight">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-500 text-[10px] font-black text-ink-900">
            JS
          </span>
          Job Signal
        </span>
        <button
          type="button"
          aria-expanded={menuOpen}
          aria-controls="mobile-nav"
          onClick={() => setMenuOpen((open) => !open)}
          className="rounded-md border border-white/20 px-3 py-1.5 text-sm font-medium text-white"
        >
          {menuOpen ? "Close menu" : "Menu"}
        </button>
      </header>
      <div className="bg-ink-900 lg:hidden">
        <DiscoveryActivity tone="dark" />
      </div>
      {menuOpen ? (
        <div
          id="mobile-nav"
          className="border-b border-white/10 bg-ink-900 lg:hidden"
        >
          <Navigation onNavigate={() => setMenuOpen(false)} />
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
