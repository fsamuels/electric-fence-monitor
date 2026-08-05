import { useState } from "react";

import { assignNode } from "../api/client";
import type { LocationSummary, NodeSummary } from "../api/client";

type Props =
  | {
      mode: "assign-node";
      nodeId: string;
      locations: LocationSummary[];
      onClose: () => void;
      onAssigned: () => void;
    }
  | {
      mode: "reassign-location";
      locationId: string;
      nodes: NodeSummary[];
      onClose: () => void;
      onAssigned: () => void;
    };

export default function AssignmentDialog(props: Props) {
  const options =
    props.mode === "assign-node"
      ? props.locations.map((l) => ({ value: l.location_id, label: l.label }))
      : props.nodes.map((n) => ({
          value: n.node_id,
          label: n.location_id ? `${n.node_id} (currently at ${n.location_id})` : n.node_id,
        }));

  const [selected, setSelected] = useState(options[0]?.value ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      const [nodeId, locationId] =
        props.mode === "assign-node" ? [props.nodeId, selected] : [selected, props.locationId];
      await assignNode(nodeId, { location_id: locationId });
      props.onAssigned();
      props.onClose();
    } catch (err) {
      setError(
        err instanceof Error && err.message.includes("409")
          ? "That assignment conflicts with an existing window."
          : err instanceof Error
            ? err.message
            : "failed to assign",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const title =
    props.mode === "assign-node"
      ? `Assign ${props.nodeId} to a location`
      : `Assign a node to ${props.locationId}`;

  return (
    <div className="dialog-overlay" role="dialog" aria-modal="true">
      <div className="dialog">
        <h3>{title}</h3>
        <form onSubmit={handleSubmit}>
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={options.length === 0}
          >
            {options.length === 0 && <option value="">nothing available</option>}
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          {error && <div className="dialog-error">{error}</div>}
          <div className="dialog-actions">
            <button type="button" onClick={props.onClose}>
              Cancel
            </button>
            <button type="submit" disabled={submitting || options.length === 0}>
              {submitting ? "Assigning…" : "Assign"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
