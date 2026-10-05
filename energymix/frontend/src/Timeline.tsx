import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { COMPONENTS, eur, dayTime, priceColor, time, valueLabel } from "./format";
import type { Plan, SlotPlan } from "./types";

interface Props {
  plan: Plan;
  tz: string;
  cheap: number;
  fast: number;
}

const LANES: { key: keyof SlotPlan; comp: string; color: (s: SlotPlan) => string | null }[] = [
  {
    key: "ess_state",
    comp: "ess",
    color: (s) => (s.ess_state === 9 ? "#34d399" : s.ess_state === 10 ? "#334155" : null),
  },
  {
    key: "zappi_mode",
    comp: "zappi",
    color: (s) =>
      ({ Fast: "#f472b6", Eco: "#a78bfa", "Eco+": "#334155" } as Record<string, string>)[s.zappi_mode ?? ""] ?? null,
  },
  { key: "pv_on", comp: "pv", color: (s) => (s.pv_on === false ? "#facc15" : s.pv_on ? "#334155" : null) },
  {
    key: "feed_in_disabled",
    comp: "feed_in",
    color: (s) => (s.feed_in_disabled === 0 ? "#38bdf8" : s.feed_in_disabled === 1 ? "#334155" : null),
  },
];

const CHART_H = 200;
const LANE_H = 14;
const LANE_GAP = 6;
const PAD_L = 44;
const PAD_R = 8;
const AXIS_H = 22;

export default function Timeline({ plan, tz, cheap, fast }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<number | null>(null);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(200, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const slots = plan.slots;
  const geo = useMemo(() => {
    if (!slots.length) return null;
    const t0 = Date.parse(slots[0].start);
    const t1 = Date.parse(slots[slots.length - 1].end);
    const prices = slots.map((s) => s.price);
    const max = Math.max(0.05, ...prices) * 1.1;
    const min = Math.min(0, ...prices) * 1.1;
    const innerW = width - PAD_L - PAD_R;
    const x = (t: number) => PAD_L + ((t - t0) / (t1 - t0)) * innerW;
    const y = (p: number) => 8 + ((max - p) / (max - min)) * (CHART_H - 16);
    return { t0, t1, max, min, x, y };
  }, [slots, width]);

  if (!geo) return <div className="text-sm text-slate-400">Geen prijsdata</div>;
  const { x, y, t0, t1, max, min } = geo;
  const lanesTop = CHART_H + 10;
  const totalH = lanesTop + LANES.length * (LANE_H + LANE_GAP) + AXIS_H;
  const now = Date.parse(plan.created_at);

  // Uur-ticks op hele uren in de lokale tijdzone; minder labels op smalle schermen
  const hoursSpan = (t1 - t0) / 3600_000;
  const every = [1, 2, 3, 6, 12].find((n) => ((width - PAD_L) / hoursSpan) * n >= 44) ?? 12;
  const ticks: number[] = [];
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  for (let t = first.getTime(); t <= t1; t += 3600_000) {
    const h = Number(new Date(t).toLocaleString("nl-NL", { hour: "2-digit", hour12: false, timeZone: tz }));
    if (t >= t0 && h % every === 0) ticks.push(t);
  }
  const gridPrices = niceTicks(min, max);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const t = t0 + ((px - PAD_L) / (width - PAD_L - PAD_R)) * (t1 - t0);
    const i = slots.findIndex((s) => Date.parse(s.start) <= t && t < Date.parse(s.end));
    setHover(i >= 0 ? i : null);
  };

  const h = hover !== null ? slots[hover] : null;
  const win = plan.charge_window;

  return (
    <div ref={wrap} className="relative w-full select-none">
      <svg
        width={width}
        height={totalH}
        className="block touch-none"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {/* Laadvenster */}
        {win && (
          <rect
            x={x(Date.parse(win[0]))}
            y={0}
            width={x(Date.parse(win[1])) - x(Date.parse(win[0]))}
            height={CHART_H}
            fill="#f472b6"
            opacity={0.08}
            rx={6}
          />
        )}

        {/* Prijsraster */}
        {gridPrices.map((p) => (
          <g key={p}>
            <line x1={PAD_L} x2={width - PAD_R} y1={y(p)} y2={y(p)} stroke={p === 0 ? "#475569" : "#1e293b"} strokeDasharray={p === 0 ? undefined : "2 4"} />
            <text x={PAD_L - 6} y={y(p) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">
              {(p * 100).toFixed(0)}¢
            </text>
          </g>
        ))}

        {/* Prijsbalken */}
        {slots.map((s, i) => {
          const xs = x(Date.parse(s.start));
          const w = Math.max(1, x(Date.parse(s.end)) - xs - (width > 500 ? 1.5 : 0.5));
          const top = s.price >= 0 ? y(s.price) : y(0);
          const hgt = Math.max(1.5, Math.abs(y(s.price) - y(0)));
          return (
            <rect
              key={s.start}
              x={xs}
              y={top}
              width={w}
              height={hgt}
              rx={Math.min(3, w / 3)}
              fill={priceColor(s.price, cheap, fast)}
              opacity={hover === null || hover === i ? 0.95 : 0.45}
            />
          );
        })}

        {/* Lanes */}
        {LANES.map((lane, li) => {
          const ly = lanesTop + li * (LANE_H + LANE_GAP);
          return (
            <g key={lane.comp}>
              <text x={PAD_L - 6} y={ly + LANE_H - 3} textAnchor="end" className="text-[11px]">
                {COMPONENTS[lane.comp].icon}
              </text>
              {slots.map((s) => {
                const c = lane.color(s);
                if (!c) return null;
                const xs = x(Date.parse(s.start));
                return <rect key={s.start} x={xs} y={ly} width={Math.max(1, x(Date.parse(s.end)) - xs)} height={LANE_H} fill={c} />;
              })}
            </g>
          );
        })}

        {/* Tijd-as */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={0} y2={totalH - AXIS_H + 2} stroke="#ffffff" opacity={0.04} />
            <text x={x(t)} y={totalH - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">
              {time(new Date(t).toISOString(), tz)}
            </text>
          </g>
        ))}

        {/* Nu */}
        {now >= t0 && now <= t1 && (
          <g>
            <line x1={x(now)} x2={x(now)} y1={0} y2={totalH - AXIS_H} stroke="#fff" strokeWidth={1.5} opacity={0.8} />
            <circle cx={x(now)} cy={3} r={3} fill="#fff" />
          </g>
        )}

        {h && (
          <rect
            x={x(Date.parse(h.start))}
            y={0}
            width={x(Date.parse(h.end)) - x(Date.parse(h.start))}
            height={totalH - AXIS_H}
            fill="#fff"
            opacity={0.06}
          />
        )}
      </svg>

      {h && <Tooltip slot={h} tz={tz} left={x(Date.parse(h.start))} width={width} />}

      <Legend />
    </div>
  );
}

function Tooltip({ slot, tz, left, width }: { slot: SlotPlan; tz: string; left: number; width: number }) {
  const w = Math.min(320, width - 16);
  const l = Math.max(8, Math.min(left - w / 2, width - w - 8));
  const rows: [string, unknown][] = [
    ["ess", slot.ess_state],
    ["dvcc", slot.dvcc_current],
    ["zappi", slot.zappi_mode],
    ["pv", slot.pv_on === null ? null : slot.pv_on ? "on" : "off"],
    ["feed_in", slot.feed_in_disabled],
  ];
  return (
    <div
      className="pointer-events-none absolute top-2 z-10 rounded-xl border border-white/10 bg-ink-800/95 p-3 text-xs shadow-2xl backdrop-blur"
      style={{ left: l, width: w }}
    >
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-slate-400">
          {dayTime(slot.start, tz)} – {time(slot.end, tz)}
        </span>
        <span className="text-base font-semibold">{eur(slot.price, 4)}</span>
      </div>
      <div className="space-y-1.5">
        {rows
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([c, v]) => (
            <div key={c} className="grid grid-cols-[1.25rem_5.5rem_1fr] gap-1">
              <span>{COMPONENTS[c].icon}</span>
              <span className="font-medium text-slate-200">{valueLabel(c, v)}</span>
              <span className="text-slate-400">{slot.reasons[c as keyof typeof slot.reasons]}</span>
            </div>
          ))}
      </div>
    </div>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["var(--color-neg)", "negatief"],
    ["var(--color-cheap)", "goedkoop"],
    ["var(--color-mid)", "laad-auto"],
    ["var(--color-high)", "duur"],
    ["#f472b6", "Zappi Fast"],
    ["#a78bfa", "Zappi Eco"],
    ["#38bdf8", "alle loads"],
  ];
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
      {items.map(([c, l]) => (
        <span key={l} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: c }} />
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
