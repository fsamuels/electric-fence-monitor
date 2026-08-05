import type { components, paths } from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type HealthResponse =
  paths["/healthz"]["get"]["responses"][200]["content"]["application/json"];

export type LocationSummary = components["schemas"]["LocationSummary"];
export type ReadingPoint = components["schemas"]["ReadingPoint"];
export type FenceEvent = components["schemas"]["FenceEvent"];
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
