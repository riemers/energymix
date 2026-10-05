import type { Decision, EntityOption, Live, Settings, Stats, Status } from "./types";

// Relatieve paden: werkt zowel achter HA Ingress als rechtstreeks
async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const getStatus = () => get<Status>("api/status");
export const getLive = () => get<Live>("api/live");
export const getDecisions = (limit = 60) => get<Decision[]>(`api/decisions?limit=${limit}`);
export const getStats = () => get<Stats>("api/stats");
export const getSettings = () => get<Settings>("api/settings");
export const saveSettings = (changes: Record<string, unknown>) => post<Settings>("api/settings", changes);
export const setHelper = (key: string, value: unknown) => post<Status>("api/helper", { key, value });
export const replan = () => post<Status>("api/replan");
export const searchEntities = (q: string) => get<EntityOption[]>(`api/entities?q=${encodeURIComponent(q)}`);
