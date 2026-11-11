import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { getReview } from "./api";
import { eur, time } from "./format";
import { BATT_COLOR } from "./Timeline";
import { Card } from "./ui";
import type { Review as ReviewT, ReviewSlot } from "./types";

const H = 170;
const BARS_H = 46;
const PAD_L = 40;
const PAD_R = 34;

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function dayLabel(day: string, today: string): string {
  if (day === today) return "Vandaag";
  if (day === addDays(today, -1)) return "Gisteren";
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" });
}

const kwh = (v: number) => `${v.toFixed(1).replace(".", ",")} kWh`;
const pct = (v: number | null) => (v === null ? "–" : `${Math.round(v)}%`);

export default function Review({ tz }: { tz: string }) {
  const [day, setDay] = useState<string | undefined>(undefined);
  const [data, setData] = useState<ReviewT | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getReview(day).then((r) => live && (setData(r), setErr(null))).catch((e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, [day]);

  if (err) return null;
  if (!data) return null;
  const m = data.money;
  const canNext = data.day < data.today;
  const nav = (
    <div className="flex items-center gap-1 text-[12px]">
      <button className="rounded-md px-2 py-0.5 text-slate-400 hover:bg-white/5" onClick={() => setDay(addDays(data.day, -1))}>◀</button>
      <span className="min-w-[7rem] text-center text-slate-200">{dayLabel(data.day, data.today)}</span>
      <button className="rounded-md px-2 py-0.5 text-slate-400 hover:bg-white/5 disabled:opacity-30" disabled={!canNext} onClick={() => setDay(addDays(data.day, 1))}>▶</button>
    </div>
  );
  const hasData = data.measured_minutes > 30;
  return (
    <Card title="Terugblik: plan en werkelijkheid" right={nav}>
      {!hasData ? (
        <p className="text-[12px] text-slate-500">Geen metingen voor deze dag.</p>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <Tile label="Accu leverde op" value={eur(m.total_eur, 2)} tone={m.total_eur >= 0 ? "green" : "red"}
              sub={`${eur(m.battery_eur, 2)} goedkoper dan zonder accu${Math.abs(m.stored_eur) >= 0.01 ? `, ${m.stored_eur >= 0 ? "+" : ""}${eur(m.stored_eur, 2)} accu-inhoud` : ""}`} />
            <Tile label="Geladen" value={kwh(m.charged_kwh)} sub={m.charged_avg !== null ? `gem. ${eur(m.charged_avg, 3)}` : "–"} />
            <Tile label="Ontladen" value={kwh(m.discharged_kwh)} sub={m.discharged_avg !== null ? `gem. ${eur(m.discharged_avg, 3)}` : "–"} />
            <Tile label="Accu begin → eind" value={`${pct(m.soc_start)} → ${pct(m.soc_end)}`}
              sub={planEnd(data.slots) !== null ? `plan: eind ${pct(planEnd(data.slots))}` : "geen plan bewaard"} />
          </div>
          <Chart slots={data.slots} tz={tz} />
          <p className="mt-2 text-[11px] leading-snug text-slate-500">
            Doorgetrokken lijn: het echte accu-%. Stippellijn: wat het plan {data.plan ? `van ${time(data.plan.created_at, tz)}` : ""} bij het
            begin van de dag verwachtte (in de loop van de dag plant hij elk kwartier opnieuw). Balken onderin: wat de accu echt deed
            (omhoog laden, omlaag ontladen), met de geplande blokken erachter. "Accu leverde op" = wat het net zonder accu gekost
            had min wat het kostte, plus het verschil in accu-inhoud tegen de mediaanprijs van die dag ({m.median_price !== null ? eur(m.median_price, 3) : "–"}), na verlies.
          </p>
        </>
      )}
    </Card>
  );
}

function planEnd(slots: ReviewSlot[]): number | null {
  for (let i = slots.length - 1; i >= 0; i--) if (slots[i].plan_soc !== null) return slots[i].plan_soc;
  return null;
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "green" | "red" }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-3 py-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone === "green" ? "text-emerald-300" : tone === "red" ? "text-red-300" : "text-slate-100"}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Chart({ slots, tz }: { slots: ReviewSlot[]; tz: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const n = slots.length;
  const innerW = width - PAD_L - PAD_R;
  const bw = innerW / n;
  const x = (i: number) => PAD_L + i * bw;
  const ySoc = (s: number) => 8 + ((100 - s) / 100) * (H - 16);
  const prices = slots.map((s) => s.price ?? 0);
  const pMax = Math.max(0.05, ...prices) * 1.1;
  const yP = (p: number) => H - 8 - (Math.max(0, p) / pMax) * (H * 0.55);
  const maxB = Math.max(0.5, ...slots.map((s) => Math.abs(s.battery_kwh ?? 0)));
  const mid = H + 8 + BARS_H / 2;
  const totalH = H + 8 + BARS_H + 20;

  const line = (get: (s: ReviewSlot) => number | null) => {
    let d = "";
    let on = false;
    slots.forEach((s, i) => {
      const v = get(s);
      if (v === null) {
        on = false;
        return;
      }
      d += `${on ? "L" : "M"}${(x(i) + bw / 2).toFixed(1)},${ySoc(v).toFixed(1)} `;
      on = true;
    });
    return d;
  };
  const h = hover !== null ? slots[hover] : null;
  const ticks = slots.map((s, i) => [s, i] as const).filter(([s]) => new Date(s.start).getMinutes() === 0 &&
    Number(new Date(s.start).toLocaleString("nl-NL", { hour: "2-digit", hour12: false, timeZone: tz })) % (width > 600 ? 3 : 6) === 0);

  return (
    <div ref={wrap} className="relative w-full select-none">
      <svg width={width} height={totalH} className="block touch-none"
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const i = Math.floor((e.clientX - r.left - PAD_L) / bw);
          setHover(i >= 0 && i < n ? i : null);
        }}
        onPointerLeave={() => setHover(null)}>
        {[0, 50, 100].map((s) => (
          <g key={s}>
            <line x1={PAD_L} x2={width - PAD_R} y1={ySoc(s)} y2={ySoc(s)} stroke="#1e293b" strokeDasharray="2 5" />
            <text x={width - PAD_R + 6} y={ySoc(s) + 3} className="fill-emerald-400/50 text-[10px]">{s}%</text>
          </g>
        ))}
        {slots.map((s, i) => s.price !== null && (
          <rect key={s.start} x={x(i)} y={yP(s.price)} width={Math.max(1, bw - 0.8)} height={H - 8 - yP(s.price)} rx={1.5}
            fill="#64748b" opacity={hover === i ? 0.45 : 0.18} />
        ))}
        <text x={PAD_L - 6} y={yP(pMax / 1.1) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{Math.round((pMax / 1.1) * 100)}¢</text>
        {/* Gepland (stippel) en echt (doorgetrokken) */}
        <path d={line((s) => s.plan_soc)} fill="none" stroke="#a7f3d0" strokeWidth={1.6} strokeDasharray="5 4" strokeOpacity={0.7} />
        <path d={line((s) => s.soc)} fill="none" stroke="#34d399" strokeWidth={2.2} strokeLinejoin="round" style={{ filter: "drop-shadow(0 0 3px rgba(52,211,153,.6))" }} />

        {/* Accu: gepland blok achter, echte energie ervoor */}
        <line x1={PAD_L} x2={width - PAD_R} y1={mid} y2={mid} stroke="#334155" />
        {slots.map((s, i) => (
          <g key={s.start}>
            {s.plan_kind && <rect x={x(i)} y={H + 8} width={bw + 0.3} height={BARS_H} fill={BATT_COLOR[s.plan_kind]} opacity={0.12} />}
            {s.battery_kwh !== null && Math.abs(s.battery_kwh) > 0.02 && (
              <rect x={x(i) + 0.3} width={Math.max(1, bw - 0.9)} rx={1}
                y={s.battery_kwh > 0 ? mid - (s.battery_kwh / maxB) * (BARS_H / 2 - 2) : mid}
                height={(Math.abs(s.battery_kwh) / maxB) * (BARS_H / 2 - 2)}
                fill={s.battery_kwh > 0 ? "#34d399" : "#38bdf8"} opacity={0.85} />
            )}
          </g>
        ))}
        <text x={PAD_L - 6} y={H + 8 + 10} textAnchor="end" className="fill-emerald-400/60 text-[9px]">laden</text>
        <text x={PAD_L - 6} y={H + 8 + BARS_H - 3} textAnchor="end" className="fill-sky-400/60 text-[9px]">ontl.</text>
        {ticks.map(([s, i]) => (
          <text key={s.start} x={x(i)} y={totalH - 5} textAnchor="middle" className="fill-slate-500 text-[10px]">{time(s.start, tz)}</text>
        ))}
        {h && <rect x={x(hover!)} y={0} width={bw} height={totalH - 18} fill="#fff" opacity={0.06} />}
      </svg>
      {h && (
        <div className="pointer-events-none absolute top-2 z-10 rounded-xl border border-white/10 bg-ink-800/95 p-2.5 text-[11px] shadow-2xl backdrop-blur"
          style={{ left: Math.max(8, Math.min(x(hover!) - 100, width - 208)), width: 200 }}>
          <div className="flex justify-between text-slate-400">
            <span>{time(h.start, tz)}</span>
            {h.price !== null && <b className="text-slate-100">{eur(h.price, 3)}</b>}
          </div>
          <div className="mt-1 text-slate-300">accu echt <b className="text-emerald-300">{pct(h.soc)}</b> · plan {pct(h.plan_soc)}</div>
          {h.battery_kwh !== null && (
            <div className="text-slate-400">
              {h.battery_kwh > 0.02 ? `geladen ${kwh(h.battery_kwh)}` : h.battery_kwh < -0.02 ? `ontladen ${kwh(-h.battery_kwh)}` : "accu stil"}
              {h.grid_kwh !== null && ` · net ${h.grid_kwh >= 0 ? "in" : "uit"} ${kwh(Math.abs(h.grid_kwh))}`}
            </div>
          )}
          {h.plan_kind && <div className="mt-1 text-slate-500">gepland: {{ charge: "laden", hold: "bewaren", export: "terugleveren" }[h.plan_kind]}</div>}
        </div>
      )}
    </div>
  );
}
