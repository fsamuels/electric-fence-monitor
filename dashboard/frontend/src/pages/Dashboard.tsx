import { useEffect, useState } from "react";

import { getLocations } from "../api/client";
import type { LocationSummary } from "../api/client";
import LocationCard from "../components/LocationCard";

// Polling, not a WebSocket -- adequate for the 10-15 min firmware duty
// cycle per docs/dashboard-plan.md; short enough that mock live-mode
// updates are visible within one interval.
const POLL_INTERVAL_MS = 15_000;

export default function Dashboard() {
  const [locations, setLocations] = useState<LocationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    function refresh() {
      getLocations()
        .then((data) => {
          if (cancelled) return;
          setLocations(data);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "failed to load locations");
        });
    }

    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <main className="dashboard">
      <h1>Fence Monitor</h1>
      {error && <div className="dashboard-error">{error}</div>}
      {locations === null ? (
        <p>Loading locations…</p>
      ) : locations.length === 0 ? (
        <p>No monitored locations yet.</p>
      ) : (
        <div className="location-grid">
          {locations.map((location) => (
            <LocationCard key={location.location_id} location={location} />
          ))}
        </div>
      )}
    </main>
  );
}
