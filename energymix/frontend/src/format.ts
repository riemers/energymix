export const eur = (v: number, digits = 3) =>
  `${v < 0 ? "−" : ""}€${Math.abs(v).toFixed(digits)}`;

export const time = (iso: string, tz?: string) =>
  new Date(iso).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit", timeZone: tz });

export const dayTime = (iso: string, tz?: string) =>
  new Date(iso).toLocaleString("nl-NL", {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: tz,
  });

export function priceColor(p: number, cheap: number, fast: number): string {
  if (p < 0) return "var(--color-neg)";
  if (p < cheap) return "var(--color-cheap)";
  if (p <= fast) return "var(--color-mid)";
  if (p < fast * 2) return "#94a3b8";
  return "var(--color-high)";
}

export const COMPONENTS: Record<string, { label: string; icon: string }> = {
  pv: { label: "Zonnepanelen", icon: "☀️" },
  ess: { label: "Thuisaccu", icon: "🔋" },
  dvcc: { label: "Laadstroom", icon: "⚡" },
  zappi: { label: "Zappi", icon: "🚗" },
  feed_in: { label: "Victron loads", icon: "🔌" },
};

export function valueLabel(component: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  switch (component) {
    case "ess":
      return Number(v) === 9 ? "Laden" : Number(v) === 10 ? "Zelfverbruik" : String(v);
    case "dvcc":
      return `${v} A`;
    case "feed_in":
      return Number(v) === 0 ? "Alle loads" : "Critical";
    case "pv":
      return v === "on" || v === true ? "Aan" : "Uit";
    default:
      return String(v);
  }
}
