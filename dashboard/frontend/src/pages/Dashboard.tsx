import { useEffect, useState } from "react";

import { getLocations, getNodes } from "../api/client";
import type { LocationSummary, NodeSummary } from "../api/client";
import LocationCard from "../components/LocationCard";
import NodeDetailModal from "../components/NodeDetailModal";
import NodeInbox from "../components/NodeInbox";

// Polling, not a WebSocket -- adequate for the 10-15 min firmware duty
// cycle per docs/dashboard-plan.md; short enough that mock live-mode
// updates are visible within one interval. refreshTick below rides the
// same interval so each card's chart/reading data live-updates too, not
// just this top-level location list.
const POLL_INTERVAL_MS = 15_000;

export default function Dashboard() {
  const [locations, setLocations] = useState<LocationSummary[] | null>(null);
  const [nodes, setNodes] = useState<NodeSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [inspectingNodeId, setInspectingNodeId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    function refresh() {
      Promise.all([getLocations(), getNodes()])
        .then(([locationData, nodeData]) => {
          if (cancelled) return;
          setLocations(locationData);
          setNodes(nodeData);
          setError(null);
          setRefreshTick((tick) => tick + 1);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "failed to load locations");
        });
    }

    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const unassignedNodes = nodes.filter((n) => n.location_id === null);
  const nodesById = new Map(nodes.map((n) => [n.node_id, n]));

  return (
    <main className="dashboard">
      <h1>Fence Monitor</h1>
      {error && <div className="dashboard-error">{error}</div>}

      {locations !== null && (
        <NodeInbox
          nodes={unassignedNodes}
          locations={locations}
          onNodeClick={setInspectingNodeId}
          onAssigned={() => setRefreshTick((tick) => tick + 1)}
        />
      )}

      {locations === null ? (
        <p>Loading locations…</p>
      ) : locations.length === 0 ? (
        <p>No monitored locations yet.</p>
      ) : (
        <div className="location-grid">
          {locations.map((location) => (
            <LocationCard
              key={location.location_id}
              location={location}
              node={location.node_id ? (nodesById.get(location.node_id) ?? null) : null}
              nodes={nodes}
              refreshTick={refreshTick}
              onNodeClick={setInspectingNodeId}
              onAssignmentChanged={() => setRefreshTick((tick) => tick + 1)}
            />
          ))}
        </div>
      )}

      {inspectingNodeId && (
        <NodeDetailModal nodeId={inspectingNodeId} onClose={() => setInspectingNodeId(null)} />
      )}
    </main>
  );
}
