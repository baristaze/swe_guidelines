// Pure: what the live-channel dot shows for each connection status.
import type { ConnectionStatus } from "../store/connection";

export interface Indicator {
  tone: "live" | "pending";
  label: string;
}

export function indicatorFor(status: ConnectionStatus): Indicator {
  return { tone: status === "open" ? "live" : "pending", label: `live updates: ${status}` };
}
