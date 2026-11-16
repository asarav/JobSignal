import { useEffect } from "react";
import { useWorkerStore } from "../stores/workerStore";

/** Performs the protocol v1 handshake and health round-trip once on mount. */
export function useWorkerBootstrap() {
  const status = useWorkerStore((state) => state.status);
  const refresh = useWorkerStore((state) => state.refresh);

  useEffect(() => {
    if (status === "idle") {
      void refresh();
    }
  }, [status, refresh]);

  return { status, refresh };
}
