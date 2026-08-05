import type { components } from "../api/schema";

type Status = components["schemas"]["Status"];

const LABEL: Record<Status, string> = {
  ok: "OK",
  low: "LOW",
  down: "DOWN",
  silent: "SILENT",
  unmonitored: "UNMONITORED",
};

const COLOR: Record<Status, { bg: string; fg: string }> = {
  ok: { bg: "#1b7a3d", fg: "#eafff0" },
  low: { bg: "#b8860b", fg: "#fff8e5" },
  down: { bg: "#a3242c", fg: "#fff0f0" },
  silent: { bg: "#5a5f66", fg: "#f2f2f2" },
  unmonitored: { bg: "#3a3f47", fg: "#c9ccd1" },
};

export default function StatusBadge({ status }: { status: Status }) {
  const { bg, fg } = COLOR[status];
  return (
    <span
      className="status-badge"
      style={{ backgroundColor: bg, color: fg }}
      data-status={status}
    >
      {LABEL[status]}
    </span>
  );
}
