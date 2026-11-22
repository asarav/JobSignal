import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AutofillPanel } from "./AutofillPanel";

const mocks = vi.hoisted(() => ({
  openJobInBrowser: vi.fn(),
  inspectApplicationForm: vi.fn(),
  planAutofill: vi.fn(),
  autofillForm: vi.fn(),
  handoffBrowserSession: vi.fn(),
  closeBrowserSession: vi.fn(),
}));

vi.mock("../../services/workerClient", () => ({
  workerClient: mocks,
  WorkerError: class WorkerError extends Error {
    readonly code = "test.error";
    readonly retryable = false;
  },
}));

function primeHappyPath() {
  mocks.openJobInBrowser.mockResolvedValue({ session_id: "session-1", url: "https://x.test" });
  mocks.inspectApplicationForm.mockResolvedValue({
    session_id: "session-1",
    step_index: 0,
    step_count: 1,
    is_final_step: true,
    fields: [],
    controls: [],
  });
  mocks.planAutofill.mockResolvedValue({
    plan_id: "plan-1",
    session_id: "session-1",
    items: [
      {
        field_id: "f1",
        label: "First name",
        action: "fill",
        value: "Ada",
        reason: "profile",
      },
    ],
  });
  mocks.autofillForm.mockResolvedValue({
    plan_id: "plan-1",
    filled: [{ field_id: "f1", label: "First name", value: "Ada", reason: "profile" }],
    skipped: [],
    blocked: [],
    errors: [],
    submitted: false,
  });
  mocks.handoffBrowserSession.mockResolvedValue({
    session_id: "session-1",
    interceptor_active: false,
    submitted: false,
  });
}

async function fillOnce() {
  render(<AutofillPanel jobId="job-1" />);
  fireEvent.click(screen.getByRole("button", { name: /check application form/i }));
  const fillButton = await screen.findByRole("button", { name: /fill 1 field/i });
  fireEvent.click(fillButton);
  await waitFor(() =>
    expect(screen.getByText(/nothing was submitted/i)).toBeInTheDocument(),
  );
}

describe("AutofillPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    primeHappyPath();
  });

  it("does not offer any way to submit for the user", async () => {
    await fillOnce();
    for (const name of [/^submit$/i, /^apply$/i, /submit application/i]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(mocks.autofillForm).toHaveBeenCalledWith({
      session_id: "session-1",
      plan_id: "plan-1",
    });
  });

  it("hands the window over only when the user says so", async () => {
    await fillOnce();
    const handoff = screen.getByRole("button", { name: /i'll submit in the browser/i });
    expect(mocks.handoffBrowserSession).not.toHaveBeenCalled();

    fireEvent.click(handoff);

    await waitFor(() =>
      expect(screen.getByText(/submission guard is off for this window/i)).toBeInTheDocument(),
    );
    expect(mocks.handoffBrowserSession).toHaveBeenCalledWith({ session_id: "session-1" });
    expect(
      screen.queryByRole("button", { name: /i'll submit in the browser/i }),
    ).not.toBeInTheDocument();
  });

  it("turns the guard note off again after another fill", async () => {
    await fillOnce();
    fireEvent.click(screen.getByRole("button", { name: /i'll submit in the browser/i }));
    await screen.findByText(/submission guard is off/i);

    fireEvent.click(screen.getByRole("button", { name: /fill 1 field/i }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /i'll submit in the browser/i }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/submission guard is off/i)).not.toBeInTheDocument();
  });
});
