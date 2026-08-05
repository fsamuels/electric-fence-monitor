import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard";

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
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
      expect(screen.getByText(/GET \/(locations|nodes) failed: 500/)).toBeInTheDocument();
    });
  });

  it("renders one card per location and lists unassigned nodes in the inbox", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL) => {
        const url = input.toString();
        if (url.includes("/locations")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve([
                {
                  location_id: "north-gate",
                  label: "North Gate",
                  status: "ok",
                  node_id: "node-a",
                  current_kv: 7.1,
                  provisional: false,
                  updated_at: null,
                },
                {
                  location_id: "creek-crossing",
                  label: "Creek Crossing",
                  status: "unmonitored",
                  node_id: null,
                  current_kv: null,
                  provisional: false,
                  updated_at: null,
                },
              ]),
          });
        }
        if (url.includes("/nodes")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve([
                {
                  node_id: "node-a",
                  fw_version: "0.1.0",
                  report_interval_s: 600,
                  sample_interval_s: 600,
                  first_seen: "2026-01-01T00:00:00Z",
                  location_id: "north-gate",
                  last_payload_ts: null,
                  last_received_at: null,
                  rssi: -60,
                  wifi_ms: 900,
                  failed_pub: 0,
                },
                {
                  node_id: "node-b",
                  fw_version: "0.1.0",
                  report_interval_s: 600,
                  sample_interval_s: 600,
                  first_seen: "2026-01-01T00:00:00Z",
                  location_id: null,
                  last_payload_ts: null,
                  last_received_at: null,
                  rssi: -80,
                  wifi_ms: 4000,
                  failed_pub: 2,
                },
              ]),
          });
        }
        // /readings, /fence-events
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("North Gate")).toBeInTheDocument();
      expect(screen.getByText("Creek Crossing")).toBeInTheDocument();
    });
    expect(screen.getByText("Unassigned nodes")).toBeInTheDocument();
    expect(screen.getByText("node-b")).toBeInTheDocument();
  });
});
