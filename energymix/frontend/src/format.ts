export const eur = (v: number, digits = 3) => `${v < 0 ? "−" : ""}€${Math.abs(v).toFixed(digits).replace(".", ",")}`;

export const time = (iso: string, tz?: string) =>
  new Date(iso).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit", timeZone: tz });

export const dayTime = (iso: string, tz?: string) =>
  new Date(iso).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: tz });

export function relDay(iso: string, tz?: string): string {
  const d = new Date(iso);
  const fmt = (x: Date) => x.toLocaleDateString("nl-NL", { timeZone: tz });
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400_000);
  if (fmt(d) === fmt(today)) return time(iso, tz);
  if (fmt(d) === fmt(tomorrow)) return `morgen ${time(iso, tz)}`;
  return dayTime(iso, tz);
}

export function watt(w: number | null | undefined): string {
  if (w === null || w === undefined || Number.isNaN(w)) return "–";
  const a = Math.abs(w);
  return a >= 1000 ? `${(a / 1000).toFixed(1).replace(".", ",")} kW` : `${Math.round(a)} W`;
}

export function priceColor(p: number, cheap: number, fast: number): string {
  if (p < 0) return "var(--color-neg)";
  if (p < cheap) return "var(--color-cheap)";
  if (p <= fast) return "var(--color-mid)";
  if (p < fast * 2.2) return "#64748b";
  return "var(--color-high)";
}

export const COMPONENTS: Record<string, { label: string; icon: string }> = {
  pv: { label: "Zonnepanelen", icon: "sun" },
  ess: { label: "Thuisaccu", icon: "battery" },
  dvcc: { label: "Laadstroom", icon: "bolt" },
  setpoint: { label: "Net-setpoint", icon: "grid" },
  zappi: { label: "Zappi", icon: "car" },
  feed_in: { label: "Victron loads", icon: "plug" },
};

export function valueLabel(component: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  switch (component) {
    case "ess":
      return Number(v) === 9 ? "Laden van net" : Number(v) === 10 ? "Zelfverbruik" : String(v);
    case "dvcc":
      return `${v} A`;
    case "setpoint":
      return Number(v) < 0 ? `Terug ${watt(Number(v))}` : `${v} W`;
    case "feed_in":
      return Number(v) === 0 ? "Alle loads" : "Critical";
    case "pv":
      return v === "on" || v === true ? "Aan" : "Uit";
    default:
      return String(v);
  }
}

// Tibber-prijsniveaus per kwartier
export const LEVELS: Record<string, { label: string; color: string }> = {
  VERY_CHEAP: { label: "zeer goedkoop", color: "#10b981" },
  CHEAP: { label: "goedkoop", color: "#6ee7b7" },
  NORMAL: { label: "normaal", color: "#64748b" },
  EXPENSIVE: { label: "duur", color: "#fb923c" },
  VERY_EXPENSIVE: { label: "zeer duur", color: "#ef4444" },
};

export function slotColor(price: number, level: string | null | undefined, cheap: number, fast: number): string {
  if (price < 0) return "var(--color-neg)";
  const l = level ? LEVELS[level] : undefined;
  return l ? l.color : priceColor(price, cheap, fast);
}
