import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ToastProvider } from "./components/Toast";
import { Copy } from "./copy/copy";
import { recordVisitOnExit } from "./features/workflow/lastVisit";
import { useWorkerBootstrap } from "./hooks/useWorkerBootstrap";
import { AppRoutes } from "./routes/routes";
import { useWorkerStore } from "./stores/workerStore";

export default function App() {
  useWorkerBootstrap();
  useEffect(() => recordVisitOnExit(), []);
  const isPreview = useWorkerStore((state) => state.isPreview);
  const error = useWorkerStore((state) => state.error);

  return (
    <BrowserRouter>
      <ToastProvider>
        <AppShell>
          {isPreview ? (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-center text-xs text-amber-900">
              {Copy.previewBanner}
            </div>
          ) : null}
          {error ? (
            <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-center text-xs text-red-800">
              {error}
            </div>
          ) : null}
          <AppRoutes />
          <footer className="py-6 text-center text-xs text-slate-500">{Copy.footer}</footer>
        </AppShell>
      </ToastProvider>
    </BrowserRouter>
  );
}
