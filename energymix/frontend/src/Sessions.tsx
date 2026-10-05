import { eur, relDay, time } from "./format";
import { BATT_COLOR, BATT_LABEL, CAR_COLOR } from "./Timeline";
import type { Live, Plan } from "./types";

const KIND: Record<string, string> = {
  goedkoopst: "goedkoopste uren",
  vannacht: "vannacht vol",
  eco: "zon/accu",
  snel: "snel laden (handmatig)",
  handmatig: "handmatig",
};

function fmtMin(m: number): string {
  const r = Math.round(m);
  return r < 60 ? `${r} min` : `${Math.floor(r / 60)}u${String(r % 60).padStart(2, "0")}`;
}

function span(start: string, end: string, tz: string) {
  return `${relDay(start, tz)} – ${time(end, tz)}`;
}

export default function Sessions({ plan, tz, live }: { plan: Plan; tz: string; live?: Live }) {
  const car = plan.car;
  const liveCar = live?.cars.find((c) => c.name === car.name);
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
        {car.name && car.max_range_km && (
          <div className="mb-2 flex flex-wrap gap-1.5 text-[11px]">
            <Chip>
              max {Math.round(car.max_range_km)} km
              <span className="text-slate-500">
                {" "}
                {car.max_source === "geleerd" ? `geleerd${car.charge_limit ? ` bij ${Math.round(car.charge_limit)}%` : ""}` : "ingesteld"}
              </span>
            </Chip>
            {car.speed_kmh && (
              <Chip>
                {Math.round(car.speed_kmh)} km/u <span className="text-slate-500">{car.speed_source}</span>
              </Chip>
            )}
            {liveCar?.soc != null && <Chip>accu auto {Math.round(liveCar.soc)}%</Chip>}
            {liveCar?.time_to_full_min != null && liveCar.time_to_full_min > 0 && (
              <Chip tone={car.time_source === "auto" ? "pink" : undefined}>
                auto zegt: nog {fmtMin(liveCar.time_to_full_min)}
                {car.time_source === "auto" && <span className="text-pink-200/70"> · gebruikt</span>}
              </Chip>
            )}
          </div>
        )}
        {car.window_options.length > 0 && (
          <div className="mb-2 grid grid-cols-2 gap-1.5 text-[11px]">
            {car.window_options.map((o) => {
              const chosen = plan.car_sessions.some((c) => c.mode === "Fast" && Date.parse(c.start) <= Date.parse(o.start) + 60_000 && Date.parse(o.start) < Date.parse(c.end));
              return (
                <div key={o.kind} className={`rounded-lg px-2.5 py-1.5 ${chosen ? "bg-pink-400/10 ring-1 ring-pink-400/40" : "bg-white/[0.03]"}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">{o.kind === "night" ? "'s Nachts" : "Overdag"}</span>
                    {chosen && <span className="text-pink-300">gekozen</span>}
                  </div>
                  <div className="text-slate-200">
                    {relDay(o.start, tz)} – {time(o.end, tz)} · gem. {eur(o.avg_price, 3)}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {car.name && car.need_km > 0.5 && car.eco_reason && (
          <div className={`mb-2 rounded-lg px-3 py-1.5 text-[11px] ${car.eco_km > 0.5 ? "bg-violet-400/10 text-violet-200" : "bg-white/[0.03] text-slate-500"}`}>
            {car.eco_km > 0.5 ? `Ochtend-eco vandaag: ~${Math.round(car.eco_km)} km uit accu/zon. ` : "Geen ochtend-eco: "}
            {car.eco_km > 0.5 ? car.eco_reason : car.eco_reason}
          </div>
        )}
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

function Chip({ children, tone }: { children: React.ReactNode; tone?: "pink" }) {
  return (
    <span className={`rounded-md px-2 py-0.5 ${tone === "pink" ? "bg-pink-400/15 text-pink-200" : "bg-white/[0.05] text-slate-300"}`}>
      {children}
    </span>
  );
}
