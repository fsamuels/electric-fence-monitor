import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LocationSummary, NodeSummary } from "../api/client";
import LocationCard from "./LocationCard";

const location: LocationSummary = {
  location_id: "north-gate",
  label: "North Gate",
  status: "ok",
  node_id: "node-a",
  current_kv: 7.1,
  provisional: false,
  updated_at: null,
};

const node: NodeSummary = {
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
};

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("LocationCard", () => {
  it("renders link quality when a node is assigned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
    );

    render(
      <LocationCard
        location={location}
        node={node}
        nodes={[node]}
        refreshTick={0}
        onNodeClick={() => {}}
        onAssignmentChanged={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/-60 dBm/)).toBeInTheDocument();
    });
  });

  it("opens the log-change form and hides it again on cancel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
    );

    render(
      <LocationCard
        location={location}
        node={node}
        nodes={[node]}
        refreshTick={0}
        onNodeClick={() => {}}
        onAssignmentChanged={() => {}}
      />,
    );

    await waitFor(() => expect(screen.getByText("Log a change")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Log a change"));
    expect(screen.getByText("Log change")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.queryByText("Log change")).not.toBeInTheDocument();
  });
});
