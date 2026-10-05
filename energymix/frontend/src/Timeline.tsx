import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { LEVELS, dayTime, eur, slotColor, time } from "./format";
import { Battery, Car, Plug, Sun } from "./icons";
import type { Plan, SlotPlan } from "./types";

interface Props {
  plan: Plan;
  tz: string;
  cheap: number;
  fast: number;
  target?: number;
  reserve?: number;
}

export const CAR_COLOR: Record<string, string> = { Fast: "#f472b6", Eco: "#a78bfa" };
export const BATT_COLOR: Record<string, string> = { charge: "#34d399", hold: "#818cf8", export: "#fbbf24" };
export const BATT_LABEL: Record<string, string> = { charge: "Laden", hold: "Bewaren", export: "Terug" };

const CHART_H = 200;
const PILL_H = 20;
const THIN_H = 6;
const GAP = 6;
const PAD_L = 40;
const PAD_R = 34;
const AXIS_H = 22;

export default function Timeline({ plan, tz, cheap, fast, target, reserve }: Props) {
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

  const slots = plan.slots;
  const geo = useMemo(() => {
    if (!slots.length) return null;
    const t0 = Date.parse(slots[0].start);
    const t1 = Date.parse(slots[slots.length - 1].end);
    const prices = slots.map((s) => s.price);
    const max = Math.max(0.05, ...prices) * 1.12;
    const min = Math.min(0, ...prices) * 1.15;
    const innerW = width - PAD_L - PAD_R;
    const x = (t: number) => PAD_L + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * innerW;
    const y = (p: number) => 10 + ((max - p) / (max - min)) * (CHART_H - 20);
    const ySoc = (s: number) => 10 + ((100 - s) / 100) * (CHART_H - 20);
    const pvMaxKw = Math.max(0.5, ...slots.map((s) => s.pv_kwh / ((Date.parse(s.end) - Date.parse(s.start)) / 3600_000)));
    return { t0, t1, max, min, x, y, ySoc, pvMaxKw };
  }, [slots, width]);

  if (!geo) return <div className="text-sm text-slate-400">Geen prijsdata</div>;
  const { x, y, ySoc, t0, t1, max, min, pvMaxKw } = geo;

  // Lanes onder de grafiek
  const carTop = CHART_H + 12;
  const battTop = carTop + PILL_H + GAP;
  const pvTop = battTop + PILL_H + GAP + 2;
  const loadsTop = pvTop + THIN_H + 4;
  const totalH = loadsTop + THIN_H + AXIS_H;
  const now = Date.parse(plan.created_at);

  const hoursSpan = (t1 - t0) / 3600_000;
  const every = [1, 2, 3, 6, 12].find((n) => ((width - PAD_L) / hoursSpan) * n >= 46) ?? 12;
  const ticks: number[] = [];
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  for (let t = first.getTime(); t <= t1; t += 3600_000) {
    const h = Number(new Date(t).toLocaleString("nl-NL", { hour: "2-digit", hour12: false, timeZone: tz }));
    if (t >= t0 && h % every === 0) ticks.push(t);
  }

  const socPts = slots.filter((s) => s.soc !== null).map((s) => [x(Date.parse(s.end)), ySoc(s.soc!)] as const);
  const socStart = slots.length && slots[0].soc !== null ? [[x(Math.max(now, t0)), ySoc(slots[0].soc!)] as const] : [];
  const socLine = [...socStart, ...socPts].map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const pvPts = slots.map((s) => {
    const h = (Date.parse(s.end) - Date.parse(s.start)) / 3600_000;
    return [x((Date.parse(s.start) + Date.parse(s.end)) / 2), CHART_H - 10 - (s.pv_kwh / h / pvMaxKw) * (CHART_H * 0.45)] as const;
  });
  const pvArea = pvPts.length
    ? `M${x(t0)},${CHART_H - 10} ` + pvPts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") + ` L${x(t1)},${CHART_H - 10} Z`
    : "";

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const t = t0 + ((e.clientX - rect.left - PAD_L) / (width - PAD_L - PAD_R)) * (t1 - t0);
    const i = slots.findIndex((s) => Date.parse(s.start) <= t && t < Date.parse(s.end));
    setHover(i >= 0 ? i : null);
  };
  const h = hover !== null ? slots[hover] : null;
  const gridPrices = niceTicks(min, max);

  return (
    <div ref={wrap} className="relative w-full select-none">
      <svg width={width} height={totalH} className="block touch-none" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="pvfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#fbbf24" stopOpacity="0.28" />
            <stop offset="1" stopColor="#fbbf24" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {gridPrices.map((p) => (
          <g key={p}>
            <line x1={PAD_L} x2={width - PAD_R} y1={y(p)} y2={y(p)} stroke={p === 0 ? "#475569" : "#1e293b"} strokeDasharray={p === 0 ? undefined : "2 5"} />
            <text x={PAD_L - 6} y={y(p) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">
              {(p * 100).toFixed(0)}¢
            </text>
          </g>
        ))}
        {[0, 50, 100].map((s) => (
          <text key={s} x={width - PAD_R + 6} y={ySoc(s) + 3} className="fill-emerald-400/50 text-[10px]">
            {s}%
          </text>
        ))}
        {target !== undefined && <line x1={PAD_L} x2={width - PAD_R} y1={ySoc(target)} y2={ySoc(target)} stroke="#34d399" strokeOpacity={0.25} strokeDasharray="6 6" />}
        {reserve !== undefined && <line x1={PAD_L} x2={width - PAD_R} y1={ySoc(reserve)} y2={ySoc(reserve)} stroke="#fbbf24" strokeOpacity={0.25} strokeDasharray="6 6" />}

        {pvArea && <path d={pvArea} fill="url(#pvfill)" />}

        {slots.map((s, i) => {
          const xs = x(Date.parse(s.start));
          const w = Math.max(1, x(Date.parse(s.end)) - xs - (width > 600 ? 1.2 : 0.4));
          const top = s.price >= 0 ? y(s.price) : y(0);
          const hgt = Math.max(1.5, Math.abs(y(s.price) - y(0)));
          return <rect key={s.start} x={xs} y={top} width={w} height={hgt} rx={Math.min(2.5, w / 3)} fill={slotColor(s.price, s.level, cheap, fast)} opacity={hover === null || hover === i ? 0.85 : 0.35} />;
        })}

        {socLine && <path d={socLine} fill="none" stroke="#34d399" strokeWidth={2.2} strokeLinejoin="round" style={{ filter: "drop-shadow(0 0 3px rgba(52,211,153,.6))" }} />}

        {/* Lane-iconen */}
        <g color="#94a3b8">
          <Car size={14} x={PAD_L - 24} y={carTop + 3} />
          <Battery size={14} x={PAD_L - 24} y={battTop + 3} />
        </g>
        <g color="#64748b">
          <Sun size={11} x={PAD_L - 22} y={pvTop - 2.5} />
          <Plug size={11} x={PAD_L - 22} y={loadsTop - 2.5} />
        </g>
        <rect x={PAD_L} y={carTop} width={width - PAD_L - PAD_R} height={PILL_H} rx={6} fill="#ffffff" opacity={0.025} />
        <rect x={PAD_L} y={battTop} width={width - PAD_L - PAD_R} height={PILL_H} rx={6} fill="#ffffff" opacity={0.025} />

        {/* Auto: laadsessies als balken met tekst */}
        {plan.car_sessions.map((c) => {
          const xs = x(Date.parse(c.start));
          const w = Math.max(3, x(Date.parse(c.end)) - xs);
          const times = `${time(c.start, tz)}–${time(c.end, tz)}`;
          const full = `${c.mode} ${times}${c.range_end_km ? ` → ${c.range_end_km} km` : ""}`;
          const label = w > full.length * 5.6 + 12 ? full : w > (c.mode.length + times.length) * 5.6 + 12 ? `${c.mode} ${times}` : w > 34 ? c.mode : "";
          return (
            <g key={c.start}>
              <rect x={xs} y={carTop} width={w} height={PILL_H} rx={6} fill={CAR_COLOR[c.mode]} opacity={0.92} />
              {label && <text x={xs + 7} y={carTop + 13.5} className="fill-slate-950 text-[10.5px] font-semibold">{label}</text>}
            </g>
          );
        })}

        {/* Accu: laden / bewaren / terug */}
        {plan.battery_sessions.map((b) => {
          const xs = x(Date.parse(b.start));
          const w = Math.max(3, x(Date.parse(b.end)) - xs);
          const full = `${BATT_LABEL[b.kind]} ${time(b.start, tz)}–${time(b.end, tz)}`;
          const label = w > full.length * 5.6 + 12 ? full : w > BATT_LABEL[b.kind].length * 5.8 + 12 ? BATT_LABEL[b.kind] : "";
          return (
            <g key={b.start}>
              <rect x={xs} y={battTop} width={w} height={PILL_H} rx={6} fill={BATT_COLOR[b.kind]} opacity={0.92} />
              {label && <text x={xs + 7} y={battTop + 13.5} className="fill-slate-950 text-[10.5px] font-semibold">{label}</text>}
            </g>
          );
        })}

        {/* Dun: panelen uit / alle loads */}
        {slots.map((s) => {
          const xs = x(Date.parse(s.start));
          const w = Math.max(1, x(Date.parse(s.end)) - xs + 0.3);
          return (
            <g key={s.start}>
              <rect x={xs} y={pvTop} width={w} height={THIN_H} rx={1} fill={s.pv_on === false ? "#facc15" : "#1e293b"} />
              <rect x={xs} y={loadsTop} width={w} height={THIN_H} rx={1} fill={s.feed_in_disabled === 0 ? "#38bdf8" : "#1e293b"} />
            </g>
          );
        })}

        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={0} y2={totalH - AXIS_H + 2} stroke="#fff" opacity={0.035} />
            <text x={x(t)} y={totalH - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">{time(new Date(t).toISOString(), tz)}</text>
          </g>
        ))}

        {now >= t0 && now <= t1 && (
          <g>
            <line x1={x(now)} x2={x(now)} y1={0} y2={totalH - AXIS_H} stroke="#fff" strokeWidth={1.5} opacity={0.75} />
            <rect x={x(now) - 13} y={0} width={26} height={14} rx={7} fill="#fff" />
            <text x={x(now)} y={10} textAnchor="middle" className="fill-slate-900 text-[9px] font-semibold">nu</text>
          </g>
        )}

        {h && <rect x={x(Date.parse(h.start))} y={0} width={x(Date.parse(h.end)) - x(Date.parse(h.start))} height={totalH - AXIS_H} fill="#fff" opacity={0.06} />}
      </svg>

      {h && <Tooltip slot={h} tz={tz} left={x(Date.parse(h.start))} width={width} />}
      <Legend />
    </div>
  );
}

function Tooltip({ slot, tz, left, width }: { slot: SlotPlan; tz: string; left: number; width: number }) {
  const w = Math.min(290, width - 16);
  const l = Math.max(8, Math.min(left - w / 2, width - w - 8));
  const chips: [string, string][] = [];
  if ((slot.setpoint_w ?? 0) < 0) chips.push([BATT_COLOR.export, `Accu terug ${(Math.abs(slot.setpoint_w!) / 1000).toFixed(1)} kW`]);
  else if (slot.ess_state === 9 && slot.dvcc_current === 0) chips.push([BATT_COLOR.hold, "Accu bewaren"]);
  else if (slot.ess_state === 9) chips.push([BATT_COLOR.charge, `Accu laden ${slot.dvcc_current} A`]);
  if (slot.zappi_mode === "Fast" || slot.zappi_mode === "Eco") chips.push([CAR_COLOR[slot.zappi_mode], `Auto ${slot.zappi_mode}`]);
  if (slot.pv_on === false) chips.push(["#facc15", "Panelen uit"]);
  const main =
    slot.zappi_mode === "Fast" || slot.zappi_mode === "Eco"
      ? slot.reasons.zappi
      : (slot.setpoint_w ?? 0) < 0
        ? slot.reasons.setpoint
        : slot.ess_state === 9
          ? slot.reasons.ess
          : null;
  return (
    <div className="pointer-events-none absolute top-6 z-10 rounded-xl border border-white/10 bg-ink-800/95 p-3 text-xs shadow-2xl backdrop-blur" style={{ left: l, width: w }}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-slate-400">{dayTime(slot.start, tz)}</span>
        <span className="text-right">
          <span className="text-base font-semibold">{eur(slot.price, 3)}</span>
          {slot.level && LEVELS[slot.level] && (
            <span className="ml-1.5 rounded px-1 text-[10px] font-medium text-slate-950" style={{ background: LEVELS[slot.level].color }}>
              {LEVELS[slot.level].label}
            </span>
          )}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-400">
        {slot.soc !== null && <span>accu <b className="text-emerald-300">{slot.soc.toFixed(0)}%</b></span>}
        {slot.car_range_km !== null && <span>auto <b className="text-pink-300">{slot.car_range_km} km</b></span>}
        {slot.pv_kwh > 0.05 && <span>zon {(slot.pv_kwh / ((Date.parse(slot.end) - Date.parse(slot.start)) / 3600_000)).toFixed(1)} kW</span>}
      </div>
      {chips.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {chips.map(([c, t]) => (
            <span key={t} className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-slate-950" style={{ background: c }}>{t}</span>
          ))}
        </div>
      )}
      {main && <p className="mt-2 line-clamp-2 text-[11px] leading-snug text-slate-400">{main}</p>}
    </div>
  );
}

function Legend() {
  const items: [string, string, "box" | "line"][] = [
    ["var(--color-neg)", "negatief", "box"],
    ...Object.values(LEVELS).map((l) => [l.color, l.label, "box"] as [string, string, "box"]),
    ["#34d399", "accu verwacht", "line"],
    [CAR_COLOR.Fast, "auto Fast", "box"],
    [CAR_COLOR.Eco, "auto Eco", "box"],
    [BATT_COLOR.charge, "accu laden", "box"],
    [BATT_COLOR.hold, "accu bewaren", "box"],
    [BATT_COLOR.export, "terugleveren", "box"],
    ["#38bdf8", "alle loads", "box"],
  ];
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
      {items.map(([c, l, kind]) => (
        <span key={l} className="flex items-center gap-1.5">
          <span className={kind === "line" ? "h-0.5 w-3 rounded" : "h-2.5 w-2.5 rounded-sm"} style={{ background: c }} />
          {l}
        </span>
      ))}
    </div>
  );
}

function niceTicks(min: number, max: number): number[] {
  const span = max - min;
  const step = span > 0.6 ? 0.2 : span > 0.3 ? 0.1 : 0.05;
  const out: number[] = [];
  for (let p = Math.ceil(min / step) * step; p <= max; p += step) out.push(Math.round(p * 100) / 100);
  if (!out.includes(0)) out.push(0);
  return out;
}
