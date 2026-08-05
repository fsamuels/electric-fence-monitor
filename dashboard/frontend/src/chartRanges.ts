import type { Bucket } from "./api/client";

/**
 * Range/bucket pairing is set by the real 600s firmware duty cycle, not the
 * accelerated mock cadence -- see docs/dashboard-plan.md "Chart ranges are
 * set by the real duty cycle, not the mock cadence." 1h is omitted because
 * it's a six-point line at real spacing.
 */
export interface ChartRange {
  id: "24h" | "7d" | "30d" | "season";
  label: string;
  hours: number;
  bucket: Bucket;
}

export const CHART_RANGES: ChartRange[] = [
  { id: "24h", label: "24h", hours: 24, bucket: "raw" },
  { id: "7d", label: "7d", hours: 24 * 7, bucket: "hour" },
  { id: "30d", label: "30d", hours: 24 * 30, bucket: "hour" },
  { id: "season", label: "Season", hours: 24 * 90, bucket: "day" },
];

export function sinceFor(range: ChartRange, now: Date = new Date()): string {
  return new Date(now.getTime() - range.hours * 60 * 60 * 1000).toISOString();
}
