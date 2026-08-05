import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { FenceEvent, ReadingPoint } from "../api/client";
import eventAnnotations from "./EventAnnotations";

interface ChartPoint {
  ts: number;
  kv_avg: number | null;
  kv_min: number | null;
  kv_max: number | null;
  adc_mv_avg: number;
  provisional: boolean;
}

function formatTime(ts: number): string {
  const date = new Date(ts);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: ChartPoint }[];
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div>{formatTime(point.ts)}</div>
      {point.kv_avg !== null ? (
        <div>
          kV: {point.kv_avg.toFixed(2)}
          {point.kv_min !== null && point.kv_max !== null
            ? ` (${point.kv_min.toFixed(2)}–${point.kv_max.toFixed(2)})`
            : ""}
        </div>
      ) : (
        <div>kV: unavailable (no calibration)</div>
      )}
      <div>adc_mv: {point.adc_mv_avg.toFixed(0)}</div>
      {point.provisional && <div className="tooltip-provisional">provisional</div>}
    </div>
  );
}

export default function VoltageChart({
  readings,
  events,
}: {
  readings: ReadingPoint[];
  events: FenceEvent[];
}) {
  const data: ChartPoint[] = readings.map((r) => ({
    ts: new Date(r.ts).getTime(),
    kv_avg: r.kv_avg,
    kv_min: r.kv_min,
    kv_max: r.kv_max,
    adc_mv_avg: r.adc_mv_avg,
    provisional: r.provisional,
  }));

  if (data.length === 0) {
    return <div className="chart-empty">No readings in this range yet.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2e35" />
        <XAxis
          dataKey="ts"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={formatTime}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          yAxisId="kv"
          label={{ value: "kV", angle: -90, position: "insideLeft" }}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          yAxisId="adc"
          orientation="right"
          label={{ value: "adc_mv", angle: 90, position: "insideRight" }}
          tick={{ fontSize: 11 }}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend />
        <Line
          yAxisId="kv"
          type="monotone"
          dataKey="kv_avg"
          name="kV"
          stroke="#4fd1c5"
          dot={{ r: 2 }}
          connectNulls={false}
          isAnimationActive={false}
        />
        <Line
          yAxisId="adc"
          type="monotone"
          dataKey="adc_mv_avg"
          name="adc_mv (raw)"
          stroke="#8a8fa3"
          strokeDasharray="4 2"
          dot={false}
          isAnimationActive={false}
        />
        {eventAnnotations(
          events.map((e) => ({ id: e.id, x: new Date(e.ts).getTime(), label: e.kind })),
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
