import type { paths } from "./schema";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type HealthResponse =
  paths["/healthz"]["get"]["responses"][200]["content"]["application/json"];

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/healthz`);
  if (!response.ok) {
    throw new Error(`GET /healthz failed: ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}
