import { create } from "zustand";
import type { SystemHealthResponse, SystemHelloResponse } from "../types/generated";
import { WorkerError, workerClient } from "../services/workerClient";

export type ConnectionStatus = "idle" | "connecting" | "ready" | "error";

interface WorkerState {
  status: ConnectionStatus;
  hello: SystemHelloResponse | null;
  health: SystemHealthResponse | null;
  error: string | null;
  isPreview: boolean;
  refresh: () => Promise<void>;
}

export const useWorkerStore = create<WorkerState>((set) => ({
  status: "idle",
  hello: null,
  health: null,
  error: null,
  isPreview: workerClient.isPreview,
  refresh: async () => {
    set({ status: "connecting", error: null });
    try {
      const hello = await workerClient.hello({ protocol_version: 1, ui_version: "0.0.0" });
      const health = await workerClient.health();
      set({ status: "ready", hello, health, error: null });
    } catch (error) {
      const message =
        error instanceof WorkerError ? `${error.code}: ${error.message}` : String(error);
      set({ status: "error", error: message });
    }
  },
}));
