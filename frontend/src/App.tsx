import { BrowserRouter } from "react-router-dom";
import { Navigation } from "./components/Navigation";
import { Copy } from "./copy/copy";
import { useWorkerBootstrap } from "./hooks/useWorkerBootstrap";
import { AppRoutes } from "./routes/routes";
import { useWorkerStore } from "./stores/workerStore";

export default function App() {
  useWorkerBootstrap();
  const isPreview = useWorkerStore((state) => state.isPreview);
  const error = useWorkerStore((state) => state.error);

  return (
    <BrowserRouter>
      <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
        <Navigation />
        {isPreview ? (
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-xs text-amber-900">
            {Copy.previewBanner}
          </div>
        ) : null}
        {error ? (
          <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-center text-xs text-red-800">
            {error}
          </div>
        ) : null}
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
          <AppRoutes />
        </main>
        <footer className="border-t border-slate-200 bg-white py-3 text-center text-xs text-slate-500">
          {Copy.footer}
        </footer>
      </div>
    </BrowserRouter>
  );
}
