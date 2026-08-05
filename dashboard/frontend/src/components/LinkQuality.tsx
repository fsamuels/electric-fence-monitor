// Display heuristics only -- not alerting logic. See docs/dashboard-plan.md's
// "Link quality is a first-class signal, not debug data": rssi/wifi_ms/failed_pub
// feed the antenna-vs-LoRa decision and are kept visually separate from fence status.
type Tier = "good" | "fair" | "poor";

function rssiTier(rssi: number): Tier {
  if (rssi >= -70) return "good";
  if (rssi >= -85) return "fair";
  return "poor";
}

function wifiMsTier(wifiMs: number): Tier {
  if (wifiMs <= 3000) return "good";
  if (wifiMs <= 8000) return "fair";
  return "poor";
}

function failedPubTier(failedPub: number): Tier {
  if (failedPub === 0) return "good";
  if (failedPub <= 3) return "fair";
  return "poor";
}

function Pill({ label, value, tier }: { label: string; value: string; tier: Tier }) {
  return (
    <span className="link-quality-pill" data-tier={tier}>
      <span className="link-quality-label">{label}</span> {value}
    </span>
  );
}

export default function LinkQuality({
  rssi,
  wifiMs,
  failedPub,
}: {
  rssi: number | null;
  wifiMs: number | null;
  failedPub: number | null;
}) {
  if (rssi === null && wifiMs === null && failedPub === null) {
    return <div className="link-quality">no link data yet</div>;
  }

  return (
    <div className="link-quality">
      {rssi !== null && <Pill label="RSSI" value={`${rssi} dBm`} tier={rssiTier(rssi)} />}
      {wifiMs !== null && <Pill label="Wi-Fi" value={`${wifiMs} ms`} tier={wifiMsTier(wifiMs)} />}
      {failedPub !== null && (
        <Pill label="Failed pub" value={String(failedPub)} tier={failedPubTier(failedPub)} />
      )}
    </div>
  );
}
