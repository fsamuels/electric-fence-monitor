import type { components, paths } from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type HealthResponse =
  paths["/healthz"]["get"]["responses"][200]["content"]["application/json"];

export type LocationSummary = components["schemas"]["LocationSummary"];
export type ReadingPoint = components["schemas"]["ReadingPoint"];
export type FenceEvent = components["schemas"]["FenceEvent"];
export type FenceEventCreate = components["schemas"]["FenceEventCreate"];
export type NodeSummary = components["schemas"]["NodeSummary"];
export type NodeDetail = components["schemas"]["NodeDetail"];
export type Assignment = components["schemas"]["Assignment"];
export type AssignmentCreate = components["schemas"]["AssignmentCreate"];
export type Bucket = "raw" | "hour" | "day";

async function getJson<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const url = new URL(`${API_BASE_URL}${path}`);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined) url.searchParams.set(key, value);
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`POST ${path} failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function del(path: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`DELETE ${path} failed: ${response.status}`);
  }
}

export async function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/healthz");
}

export async function getLocations(): Promise<LocationSummary[]> {
  return getJson<LocationSummary[]>("/locations");
}

export async function getLocationReadings(
  locationId: string,
  since: string,
  bucket: Bucket,
): Promise<ReadingPoint[]> {
  return getJson<ReadingPoint[]>(`/locations/${encodeURIComponent(locationId)}/readings`, {
    since,
    bucket,
  });
}

export async function getFenceEvents(locationId: string, since?: string): Promise<FenceEvent[]> {
  return getJson<FenceEvent[]>("/fence-events", { location_id: locationId, since });
}

export async function createFenceEvent(body: FenceEventCreate): Promise<FenceEvent> {
  return postJson<FenceEvent>("/fence-events", body);
}

export async function getNodes(): Promise<NodeSummary[]> {
  return getJson<NodeSummary[]>("/nodes");
}

export async function getNode(nodeId: string): Promise<NodeDetail> {
  return getJson<NodeDetail>(`/nodes/${encodeURIComponent(nodeId)}`);
}

export async function assignNode(nodeId: string, body: AssignmentCreate): Promise<Assignment> {
  return postJson<Assignment>(`/nodes/${encodeURIComponent(nodeId)}/assignment`, body);
}

export async function unassignNode(nodeId: string): Promise<void> {
  return del(`/nodes/${encodeURIComponent(nodeId)}/assignment`);
}
