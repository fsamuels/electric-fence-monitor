import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["ok", "OK"],
    ["low", "LOW"],
    ["down", "DOWN"],
    ["silent", "SILENT"],
    ["unmonitored", "UNMONITORED"],
  ] as const)("renders %s as %s", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
