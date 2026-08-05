import { describe, expect, it } from "vitest";

import { CHART_RANGES, sinceFor } from "./chartRanges";

describe("chartRanges", () => {
  it("omits 1h -- six points at real 600s cadence is not a useful chart", () => {
    expect(CHART_RANGES.some((r) => r.id === ("1h" as never))).toBe(false);
  });

  it("buckets by the real duty cycle: raw at 24h, hourly through 30d, daily for the season", () => {
    const byId = Object.fromEntries(CHART_RANGES.map((r) => [r.id, r.bucket]));
    expect(byId["24h"]).toBe("raw");
    expect(byId["7d"]).toBe("hour");
    expect(byId["30d"]).toBe("hour");
    expect(byId.season).toBe("day");
  });

  it("sinceFor subtracts the range's hours from the given instant", () => {
    const now = new Date("2026-08-05T12:00:00Z");
    const range = CHART_RANGES.find((r) => r.id === "24h")!;
    expect(sinceFor(range, now)).toBe("2026-08-04T12:00:00.000Z");
  });
});
