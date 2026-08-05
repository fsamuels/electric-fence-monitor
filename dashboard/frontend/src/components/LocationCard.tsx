import { useEffect, useState } from "react";

import { getFenceEvents, getLocationReadings, unassignNode } from "../api/client";
import type { FenceEvent, LocationSummary, NodeSummary, ReadingPoint } from "../api/client";
import { CHART_RANGES, sinceFor } from "../chartRanges";
import type { ChartRange } from "../chartRanges";
import AssignmentDialog from "./AssignmentDialog";
import LinkQuality from "./LinkQuality";
import LogChangeForm from "./LogChangeForm";
import StatusBadge from "./StatusBadge";
import VoltageChart from "./VoltageChart";

export default function LocationCard({
  location,
  node,
  nodes,
  refreshTick,
  onNodeClick,
  onAssignmentChanged,
}: {
  location: LocationSummary;
  node: NodeSummary | null;
  nodes: NodeSummary[];
  refreshTick: number;
  onNodeClick: (nodeId: string) => void;
  onAssignmentChanged: () => void;
}) {
  const [range, setRange] = useState<ChartRange>(CHART_RANGES[0]);
  const [readings, setReadings] = useState<ReadingPoint[]>([]);
  const [events, setEvents] = useState<FenceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reassigning, setReassigning] = useState(false);
  const [loggingChange, setLoggingChange] = useState(false);
  const [eventsVersion, setEventsVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const since = sinceFor(range);
    Promise.all([
      getLocationReadings(location.location_id, since, range.bucket),
      getFenceEvents(location.location_id, since),
    ])
      .then(([r, e]) => {
        if (cancelled) return;
        setReadings(r);
        setEvents(e);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "failed to load readings");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [location.location_id, range, refreshTick, eventsVersion]);

  async function handleUnassign() {
    if (!location.node_id) return;
    await unassignNode(location.node_id);
    onAssignmentChanged();
  }

  return (
    <article className="location-card" data-status={location.status}>
      <header className="location-card-header">
        <div>
          <h2>{location.label}</h2>
          <div className="location-node-id">
            {location.node_id ? (
              <>
                <button
                  type="button"
                  className="location-node-id-link"
                  onClick={() => onNodeClick(location.node_id!)}
                >
                  node {location.node_id}
                </button>
                <button type="button" className="location-node-action" onClick={handleUnassign}>
                  Unassign
                </button>
              </>
            ) : (
              <>
                no node assigned{" "}
                <button
                  type="button"
                  className="location-node-action"
                  onClick={() => setReassigning(true)}
                >
                  Assign node
                </button>
              </>
            )}
            {location.node_id && (
              <button
                type="button"
                className="location-node-action"
                onClick={() => setReassigning(true)}
              >
                Change node
              </button>
            )}
          </div>
        </div>
        <StatusBadge status={location.status} />
      </header>

      <div className="location-current-kv">
        {location.current_kv !== null ? (
          <>
            <span className="kv-value">{location.current_kv.toFixed(2)}</span>
            <span className="kv-unit">kV</span>
            {location.provisional && <span className="kv-provisional">provisional</span>}
          </>
        ) : (
          <span className="kv-unavailable">no reading yet</span>
        )}
      </div>

      {node && (
        <LinkQuality rssi={node.rssi} wifiMs={node.wifi_ms} failedPub={node.failed_pub} />
      )}

      <div className="range-selector">
        {CHART_RANGES.map((r) => (
          <button
            key={r.id}
            type="button"
            className={r.id === range.id ? "range-button active" : "range-button"}
            onClick={() => setRange(r)}
          >
            {r.label}
          </button>
        ))}
      </div>

      {error ? (
        <div className="chart-error">{error}</div>
      ) : loading ? (
        <div className="chart-loading">Loading…</div>
      ) : (
        <VoltageChart readings={readings} events={events} />
      )}

      <div className="location-card-footer">
        {loggingChange ? (
          <LogChangeForm
            locationId={location.location_id}
            onCancel={() => setLoggingChange(false)}
            onLogged={() => {
              setLoggingChange(false);
              setEventsVersion((v) => v + 1);
            }}
          />
        ) : (
          <button type="button" onClick={() => setLoggingChange(true)}>
            Log a change
          </button>
        )}
      </div>

      {reassigning && (
        <AssignmentDialog
          mode="reassign-location"
          locationId={location.location_id}
          nodes={nodes}
          onClose={() => setReassigning(false)}
          onAssigned={onAssignmentChanged}
        />
      )}
    </article>
  );
}
