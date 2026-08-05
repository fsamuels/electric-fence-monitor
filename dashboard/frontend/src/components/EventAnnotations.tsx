import { ReferenceLine } from "recharts";

export interface Annotation {
  id: number;
  x: number;
  label: string;
}

/**
 * Returns ReferenceLine elements, one per fence_events row -- the difference
 * between a step change reading as "something broke here" versus "we
 * extended the fence here" (docs/dashboard-plan.md). Rendered as an array of
 * elements rather than a wrapping component so Recharts recognizes each
 * child's type as ReferenceLine.
 */
export default function eventAnnotations(annotations: Annotation[]) {
  return annotations.map((annotation) => (
    <ReferenceLine
      key={annotation.id}
      yAxisId="kv"
      x={annotation.x}
      stroke="#8a8fa3"
      strokeDasharray="4 4"
      label={{
        value: annotation.label,
        position: "insideTopLeft",
        fill: "#8a8fa3",
        fontSize: 11,
      }}
    />
  ));
}
