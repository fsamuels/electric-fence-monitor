import { describe, expect, it, vi } from "vitest";

vi.stubGlobal(
  "fetch",
  vi.fn(() =>
    Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          api: true,
          broker: false,
          db: false,
          ingest: { connected: false, last_heartbeat: null, healthy: false },
          healthy: false,
        }),
    }),
  ),
);

describe("api client", () => {
  it("fetches health from the configured API base URL", async () => {
    const { getHealth } = await import("./api/client");
    const health = await getHealth();
    expect(health.api).toBe(true);
  });
});
