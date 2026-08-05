import { useState } from "react";

import type { LocationSummary, NodeSummary } from "../api/client";
import AssignmentDialog from "./AssignmentDialog";
import LinkQuality from "./LinkQuality";

// Unassigned-node provisioning inbox -- see docs/dashboard-plan.md's
// "Unknown-node policy": a freshly-flashed or unbound node is reporting
// fine, it just isn't attributed to a fence point yet. Not a fault.
export default function NodeInbox({
  nodes,
  locations,
  onNodeClick,
  onAssigned,
}: {
  nodes: NodeSummary[];
  locations: LocationSummary[];
  onNodeClick: (nodeId: string) => void;
  onAssigned: () => void;
}) {
  const [assigning, setAssigning] = useState<string | null>(null);

  if (nodes.length === 0) return null;

  return (
    <section className="node-inbox">
      <h2>Unassigned nodes</h2>
      <ul className="node-inbox-list">
        {nodes.map((node) => (
          <li key={node.node_id} className="node-inbox-row">
            <button
              type="button"
              className="node-inbox-id"
              onClick={() => onNodeClick(node.node_id)}
            >
              {node.node_id}
            </button>
            <span className="node-inbox-seen">
              {node.last_received_at
                ? `last seen ${new Date(node.last_received_at).toLocaleString()}`
                : "never reported"}
            </span>
            <LinkQuality rssi={node.rssi} wifiMs={node.wifi_ms} failedPub={node.failed_pub} />
            <button type="button" onClick={() => setAssigning(node.node_id)}>
              Assign
            </button>
          </li>
        ))}
      </ul>
      {assigning && (
        <AssignmentDialog
          mode="assign-node"
          nodeId={assigning}
          locations={locations}
          onClose={() => setAssigning(null)}
          onAssigned={onAssigned}
        />
      )}
    </section>
  );
}
