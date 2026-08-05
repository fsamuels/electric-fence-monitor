import { useEffect, useState } from "react";

import { getHealth, type HealthResponse } from "./api/client";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | "unreachable" | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth("unreachable"));
  }, []);

  return (
    <main>
      <h1>Fence Monitor</h1>
      <p>D0 scaffolding placeholder -- the location grid arrives in Phase D4.</p>
      <p>API health: {health === null ? "checking..." : JSON.stringify(health)}</p>
    </main>
  );
}
