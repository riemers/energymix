import { eur, relDay, time } from "./format";
import { BATT_COLOR, BATT_LABEL, CAR_COLOR } from "./Timeline";
import type { Plan } from "./types";

const KIND: Record<string, string> = {
  goedkoopst: "goedkoopste uren",
  vannacht: "vannacht vol",
  eco: "zon/accu",
  handmatig: "handmatig",
};

function fmtMin(m: number): string {
  const r = Math.round(m);
  return r < 60 ? `${r} min` : `${Math.floor(r / 60)}u${String(r % 60).padStart(2, "0")}`;
}

function span(start: string, end: string, tz: string) {
  return `${relDay(start, tz)} – ${time(end, tz)}`;
}

export default function Sessions({ plan, tz }: { plan: Plan; tz: string }) {
  const car = plan.car;
  return (
    <div className="mt-4 grid gap-4 border-t border-white/5 pt-4 md:grid-cols-2">
      <div className="min-w-0">
        <div className="mb-2 flex items-baseline gap-2 text-[13px]">
          <span className="font-medium text-slate-300">Auto</span>
          {car.name && car.range_km !== null && (
            <span className="text-slate-500">
              {car.name} · nu {Math.round(car.range_km)} km{car.max_range_km ? ` van ${car.max_range_km}` : ""}
              {car.need_minutes > 1 ? ` · nog ${Math.round(car.need_km)} km = ${fmtMin(car.need_minutes)} laden` : ""}
            </span>
          )}
        </div>
        {plan.car_sessions.length ? (
          <ul className="space-y-1.5">
            {plan.car_sessions.map((c) => (
              <li key={c.start} className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2">
                <span className="h-8 w-1 shrink-0 rounded-full" style={{ background: CAR_COLOR[c.mode] }} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                    <span className="font-medium text-slate-100">{span(c.start, c.end, tz)}</span>
                    <span className="rounded px-1.5 text-[11px] font-semibold text-slate-950" style={{ background: CAR_COLOR[c.mode] }}>{c.mode}</span>
                  </div>
                  <div className="truncate text-[11px] text-slate-500">
                    {c.kinds.map((k) => KIND[k] ?? k).join(" + ")} · {c.kwh} kWh
                    {c.avg_price !== null && ` · gem. ${eur(c.avg_price, 3)}`}
                  </div>
                </div>
                {c.range_end_km !== null && (
                  <div className="shrink-0 text-right text-[12px] tabular-nums">
                    <span className="text-slate-500">{c.range_start_km} →</span> <b className="text-pink-300">{c.range_end_km} km</b>
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12px] text-slate-500">
            {car.name ? "Geen laadmoment nodig: de auto is vol." : "Geen auto aan de lader."}
          </p>
        )}
      </div>

      <div className="min-w-0">
        <div className="mb-2 text-[13px] font-medium text-slate-300">Accu</div>
        {plan.battery_sessions.length ? (
          <ul className="space-y-1.5">
            {plan.battery_sessions.slice(0, 6).map((b) => (
              <li key={b.start} className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2">
                <span className="h-8 w-1 shrink-0 rounded-full" style={{ background: BATT_COLOR[b.kind] }} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                    <span className="font-medium text-slate-100">{span(b.start, b.end, tz)}</span>
                    <span className="rounded px-1.5 text-[11px] font-semibold text-slate-950" style={{ background: BATT_COLOR[b.kind] }}>{BATT_LABEL[b.kind]}</span>
                  </div>
                  <div className="truncate text-[11px] text-slate-500" title={b.reason}>{b.reason}</div>
                </div>
                <div className="shrink-0 text-right text-[12px] tabular-nums">
                  {b.kind !== "hold" && <span className="text-slate-400">{b.kwh} kWh</span>}
                  {b.soc_end !== null && <div className="text-emerald-300">→ {Math.round(b.soc_end)}%</div>}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12px] text-slate-500">Alleen zelfverbruik: niets van of naar het net gepland.</p>
        )}
      </div>
    </div>
  );
}
