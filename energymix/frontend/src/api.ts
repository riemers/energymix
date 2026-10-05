import type { Decision, Status } from "./types";

// Relatieve paden: werkt zowel achter HA Ingress als rechtstreeks
export async function getStatus(): Promise<Status> {
  const r = await fetch("api/status");
  if (!r.ok) throw new Error(`status ${r.status}`);
  return r.json();
}

export async function getDecisions(limit = 50): Promise<Decision[]> {
  const r = await fetch(`api/decisions?limit=${limit}`);
  if (!r.ok) throw new Error(`status ${r.status}`);
  return r.json();
}

export async function replan(): Promise<Status> {
  const r = await fetch("api/replan", { method: "POST" });
  if (!r.ok) throw new Error(`status ${r.status}`);
  return r.json();
}
