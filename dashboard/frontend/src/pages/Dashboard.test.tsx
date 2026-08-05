import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Dashboard", () => {
  it("shows a message when there are no monitored locations yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
    );

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("No monitored locations yet.")).toBeInTheDocument();
    });
  });

  it("surfaces a fetch failure instead of hanging on 'Loading'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 500 })),
    );

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText(/GET \/locations failed: 500/)).toBeInTheDocument();
    });
  });
});
