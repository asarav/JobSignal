import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";

export interface ToastOptions {
  tone?: "success" | "info" | "warning" | "danger";
  action?: { label: string; to: string };
}

interface ToastRow extends ToastOptions {
  id: number;
  message: string;
  leaving?: boolean;
}

interface ToastContextValue {
  push: (message: string, options?: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TONE = {
  success: "border-emerald-200 bg-emerald-50 text-emerald-900",
  info: "border-brand-200 bg-brand-50 text-brand-800",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
} as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [rows, setRows] = useState<ToastRow[]>([]);
  const seq = useRef(0);
  const timers = useRef<number[]>([]);

  const remove = useCallback((id: number) => {
    setRows((current) => current.filter((row) => row.id !== id));
  }, []);

  const dismiss = useCallback(
    (id: number) => {
      setRows((current) =>
        current.map((row) => (row.id === id ? { ...row, leaving: true } : row)),
      );
      const timer = window.setTimeout(() => remove(id), 160);
      timers.current.push(timer);
    },
    [remove],
  );

  const push = useCallback(
    (message: string, options: ToastOptions = {}) => {
      seq.current += 1;
      const id = seq.current;
      setRows((current) => [...current.slice(-2), { id, message, ...options }]);
      const timer = window.setTimeout(() => dismiss(id), 4200);
      timers.current.push(timer);
    },
    [dismiss],
  );

  useEffect(
    () => () => {
      for (const timer of timers.current) window.clearTimeout(timer);
    },
    [],
  );

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
        aria-live="polite"
        aria-atomic="false"
      >
        {rows.map((row) => (
          <div
            key={row.id}
            className={`${row.leaving ? "animate-toast-out" : "animate-toast-in"} pointer-events-auto flex items-start gap-3 rounded-lg border p-3 shadow-card ${TONE[row.tone ?? "success"]}`}
            role="status"
          >
            <span aria-hidden="true" className="mt-0.5 text-base font-bold">
              ✓
            </span>
            <div className="min-w-0 flex-1 text-sm">
              <p>{row.message}</p>
              {row.action ? (
                <Link
                  to={row.action.to}
                  onClick={() => dismiss(row.id)}
                  className="mt-1 inline-block text-xs font-semibold underline"
                >
                  {row.action.label}
                </Link>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => dismiss(row.id)}
              className="text-xs opacity-60 hover:opacity-100"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Safe outside the provider, so isolated screen tests still render. */
export function useToast(): ToastContextValue {
  return useContext(ToastContext) ?? { push: () => undefined };
}
