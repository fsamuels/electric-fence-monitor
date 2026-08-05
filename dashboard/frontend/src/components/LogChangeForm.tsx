import { useState } from "react";

import { createFenceEvent } from "../api/client";

// Kinds from docs/dashboard-plan.md's fence_events section -- the operator-
// annotated timeline that keeps a deliberate physical change from reading
// as a developing fault forever afterward.
const KINDS = [
  "wire_added",
  "wire_removed",
  "charger_changed",
  "charger_serviced",
  "vegetation_cleared",
  "grounding_modified",
  "board_swapped",
  "recalibrated",
  "other",
] as const;

export default function LogChangeForm({
  locationId,
  onLogged,
  onCancel,
}: {
  locationId: string;
  onLogged: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<(typeof KINDS)[number]>(KINDS[0]);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createFenceEvent({ location_id: locationId, kind, note: note || null });
      setNote("");
      onLogged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to log change");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="log-change-form" onSubmit={handleSubmit}>
      <select value={kind} onChange={(e) => setKind(e.target.value as (typeof KINDS)[number])}>
        {KINDS.map((k) => (
          <option key={k} value={k}>
            {k.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <textarea
        placeholder="note (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      {error && <div className="dialog-error">{error}</div>}
      <div className="log-change-actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={submitting}>
          {submitting ? "Logging…" : "Log change"}
        </button>
      </div>
    </form>
  );
}
