import type { Decision, EntityOption, Live, Review, Settings, Stats, Status } from "./types";

// Relatieve paden: werkt zowel achter HA Ingress als rechtstreeks
//
// Achter HA Ingress verloopt de ingress-sessie als het tabblad een tijd op de
// achtergrond stond. Bij terugkomen geeft de addon dan even 401 tot de HA-frontend
// de sessie heeft vernieuwd (dat duurt een paar seconden). Daarom proberen we het bij
// een 401 stil opnieuw, in plaats van meteen een foutmelding te tonen.
const RETRY_401_MS = [1000, 2000, 3000, 4000, 5000, 5000];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function request(path: string, init?: RequestInit): Promise<Response> {
  for (let i = 0; ; i++) {
    const r = await fetch(path, { credentials: "same-origin", ...init });
    if (r.status !== 401 || i >= RETRY_401_MS.length) return r;
    await sleep(RETRY_401_MS[i]);
  }
}

async function get<T>(path: string): Promise<T> {
  const r = await request(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await request(path, {
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
export const getReview = (day?: string) => get<Review>(`api/review${day ? `?day=${day}` : ""}`);
