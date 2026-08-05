import { useEffect, useState } from "react";

import { getNode } from "../api/client";
import type { NodeDetail } from "../api/client";

function formatWindow(validFrom: string, validTo: string | null): string {
  const from = new Date(validFrom).toLocaleString();
  const to = validTo ? new Date(validTo).toLocaleString() : "present";
  return `${from} → ${to}`;
}

export default function NodeDetailModal({
  nodeId,
  onClose,
}: {
  nodeId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getNode(nodeId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "failed to load node");
      });
    return () => {
      cancelled = true;
    };
  }, [nodeId]);

  return (
    <div className="dialog-overlay" role="dialog" aria-modal="true">
      <div className="dialog node-detail-dialog">
        <h3>Node {nodeId}</h3>
        {error && <div className="dialog-error">{error}</div>}
        {!error && !detail && <p>Loading…</p>}
        {detail && (
          <>
            <dl className="node-detail-facts">
              <dt>Firmware</dt>
              <dd>{detail.fw_version ?? "unknown"}</dd>
              <dt>Report interval</dt>
              <dd>{detail.report_interval_s ? `${detail.report_interval_s}s` : "unknown"}</dd>
              <dt>First seen</dt>
              <dd>{new Date(detail.first_seen).toLocaleString()}</dd>
            </dl>

            <h4>Assignment history</h4>
            {detail.assignments.length === 0 ? (
              <p>No assignments yet.</p>
            ) : (
              <ul className="node-detail-timeline">
                {detail.assignments.map((a, i) => (
                  <li key={i}>
                    <strong>{a.location_id}</strong>{" "}
                    <span>{formatWindow(a.valid_from, a.valid_to)}</span>
                  </li>
                ))}
              </ul>
            )}

            <h4>Calibration history</h4>
            {detail.calibrations.length === 0 ? (
              <p>No calibration recorded -- readings show provisional kV.</p>
            ) : (
              <ul className="node-detail-timeline">
                {detail.calibrations.map((c, i) => (
                  <li key={i}>
                    <strong>{c.location_id}</strong>{" "}
                    <span>{formatWindow(c.valid_from, c.valid_to)}</span>
                    <div className="node-detail-calibration-fit">
                      kv_per_mv={c.kv_per_mv}, kv_offset={c.kv_offset}
                      {c.method ? `, method=${c.method}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
