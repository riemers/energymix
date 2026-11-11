import { eur, relDay } from "./format";
import type { ChargeUse } from "./types";

// Waar de stroom van een laadblok heen gaat
export const USE_COLOR = { own: "#34d399", export: "#fbbf24", end: "#94a3b8" };
const kwh = (v: number) => `${v.toFixed(1).replace(".", ",")} kWh`;

export function UseBar({ use, height = 6 }: { use: ChargeUse; height?: number }) {
  const total = use.own_kwh + use.export_kwh + use.end_kwh;
  if (total < 0.1) return null;
  const seg = (v: number, c: string, hatch = false) =>
    v > 0.05 && (
      <span
        className="h-full"
        style={{
          width: `${(v / total) * 100}%`,
          background: hatch ? `repeating-linear-gradient(135deg, ${c} 0 3px, transparent 3px 6px)` : c,
        }}
      />
    );
  return (
    <span className="flex w-full overflow-hidden rounded-full bg-white/5" style={{ height }}>
      {seg(use.own_kwh, USE_COLOR.own)}
      {seg(use.export_kwh, USE_COLOR.export)}
      {seg(use.end_kwh, USE_COLOR.end, true)}
    </span>
  );
}

/** Uitleg per laadblok: balk + regels "waarvoor" in gewone woorden */
export function UseDetails({ use, tz }: { use: ChargeUse; tz: string }) {
  const rows: [string, string, string][] = [];
  if (use.own_kwh >= 0.1)
    rows.push([USE_COLOR.own, `${kwh(use.own_kwh)} eigen verbruik`,
      use.own_at ? `later duurder: vooral ${relDay(use.own_at, tz)}${use.own_avg_price !== null ? `, gem. ${eur(use.own_avg_price, 3)}` : ""}` : "later duurder"]);
  if (use.export_kwh >= 0.1)
    rows.push([USE_COLOR.export, `${kwh(use.export_kwh)} terugleveren`,
      use.export_at ? `vooral ${relDay(use.export_at, tz)}${use.export_avg_price !== null ? ` à ${eur(use.export_avg_price, 3)}` : ""}` : ""]);
  if (use.end_kwh >= 0.1)
    rows.push([USE_COLOR.end, `${kwh(use.end_kwh)} voor later`,
      `blijft in de accu na de bekende prijzen, gerekend à ${eur(use.end_value, 3)} (${use.end_why.replace(/€(\d+)\.(\d+)/g, "€$1,$2")})`]);
  return (
    <div className="space-y-1">
      <UseBar use={use} />
      {rows.map(([c, a, b]) => (
        <div key={a} className="flex gap-1.5 text-[11px] leading-snug">
          <span className="mt-1 h-2 w-2 shrink-0 rounded-sm" style={{ background: c }} />
          <span>
            <b className="font-medium text-slate-200">{a}</b>
            {b && <span className="text-slate-400"> · {b}</span>}
          </span>
        </div>
      ))}
      <div className="text-[11px] text-slate-500">
        kost {eur(use.cost_eur, 2)} · levert naar verwachting {use.gain_eur >= 0 ? "+" : ""}{eur(use.gain_eur, 2)} op
      </div>
    </div>
  );
}
