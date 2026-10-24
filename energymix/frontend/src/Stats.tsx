import { useState } from "react";
import type { VictronSpec, EfficiencyCheck, EfficiencyCycle, EfficiencyDay, Passthrough, PowerBin, Plan, Stats as StatsT, Status } from "./types";
import { Card } from "./ui";
import { eur, relDay, time, watt } from "./format";

export default function Stats({ stats, status }: { stats: StatsT | null; status: Status }) {
  if (!stats) return <p className="text-sm text-slate-500">Statistiek laden…</p>;
  const plan = status.plan;
  const hrs = stats.to_target_hours;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Laagste stand (14 dagen)" value={stats.lowest_soc !== null ? `${stats.lowest_soc.toFixed(0)}%` : "–"}
          sub={stats.median_daily_min_soc !== null ? `meestal rond ${stats.median_daily_min_soc.toFixed(0)}% op z'n laagst` : "nog te weinig data"} />
        <Tile label="Laadvermogen" value={watt(stats.charge_w_used)} sub={stats.charge_w_source} />
        <Tile
          label={`Naar ${status.battery_target_soc}%`}
          value={hrs !== null && hrs !== undefined ? fmtHours(hrs) : "–"}
          sub={stats.to_target_kwh !== undefined ? `nog ${stats.to_target_kwh.toFixed(1)} kWh` : undefined}
        />
        <Tile
          label="Verwachte besparing"
          value={plan?.summary.saving_eur !== undefined ? eur(plan.summary.saving_eur, 2) : "–"}
          sub="komende planning t.o.v. niets doen"
        />
      </div>

      <Card title="Accu per dag" right={<span className="text-[11px] text-slate-500">{stats.samples} metingen</span>}>
        {stats.days.length ? <SocChart stats={stats} reserve={status.battery_reserve_soc} target={status.battery_target_soc} /> : <Empty />}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Geladen en ontladen per dag">
          {stats.days.length ? <EnergyChart stats={stats} /> : <Empty />}
        </Card>
        <Card title="Seizoen en planning">
          <PlanFacts plan={plan} tz={status.timezone} />
          {stats.house_profile && <HouseProfileNote hp={stats.house_profile} />}
        </Card>
      </div>

      {plan?.summary.passthrough && (
        <Card
          title="Passthrough: huis direct van het net"
          right={
            <span className="text-[11px] text-slate-500">
              {plan.summary.passthrough.kwh > 0 ? `${plan.summary.passthrough.kwh.toString().replace(".", ",")} kWh · scheelt ${eur(plan.summary.passthrough.value_eur, 2)}` : "nu niet gepland"}
            </span>
          }
        >
          <PassthroughChart pt={plan.summary.passthrough} tz={status.timezone} />
        </Card>
      )}

      <Card
        title="Capaciteit accu"
        right={<span className="text-[11px] text-slate-500">ingesteld {status.battery_capacity_kwh_set ?? status.battery_capacity_kwh} kWh</span>}
      >
        {stats.capacity?.kwh ? (
          <p className="text-sm text-slate-300">
            Gemeten <b>{stats.capacity.kwh.toString().replace(".", ",")} kWh</b> uit {stats.capacity.runs} keer flink laden of ontladen
            {stats.capacity.charge_kwh && stats.capacity.discharge_kwh
              ? ` (laden ${stats.capacity.charge_kwh.toString().replace(".", ",")}, ontladen ${stats.capacity.discharge_kwh.toString().replace(".", ",")} kWh)`
              : ""}
            . {status.battery_capacity_mode === "fixed" ? "Staat op vast: de planner rekent met het ingestelde getal." : "De planner rekent hiermee."}
          </p>
        ) : (
          <p className="text-sm text-slate-500">
            Nog niet genoeg gemeten. Zodra de accu een paar keer minstens 20 procentpunt achter elkaar is geladen of ontladen,
            rekent Energymix uit hoeveel kWh er per procent in of uit gaat. Tot die tijd geldt het ingestelde getal.
          </p>
        )}
      </Card>

      <Card
        title="Rendement accu heen en terug"
        right={
          <span className="text-[11px] text-slate-500">
            {stats.efficiency.cycles?.overall != null ? `per cyclus ${pct(stats.efficiency.cycles.overall)} · ` : ""}
            {stats.efficiency.overall !== null ? `per dag ${pct(stats.efficiency.overall)} · ` : ""}ingesteld {pct(stats.roundtrip_setting)}
          </span>
        }
      >
        {stats.efficiency.days.length ? (
          <EfficiencyChart days={stats.efficiency.days} setting={stats.roundtrip_setting} />
        ) : (
          <p className="text-sm text-slate-500">
            Nog geen hele dag gemeten. Energymix telt wat de Multi's aan wisselstroom opnemen en weer leveren; na een
            dag waarop de accu flink geladen en ontladen is staat hier het eerste punt.
          </p>
        )}
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-1 text-xs text-slate-400">Per cyclus (terug op dezelfde accustand)</div>
            <EfficiencyCycles cycles={stats.efficiency.cycles?.list ?? []} />
          </div>
          <div>
            <div className="mb-1 text-xs text-slate-400">Per vermogen (AC tegen DC van de accumonitor)</div>
            <PowerCurve curve={stats.efficiency.power_curve} spec={stats.victron_spec} />
          </div>
        </div>
        {!!stats.efficiency.checked?.length && <EfficiencyChecks checks={stats.efficiency.checked} />}
      </Card>
    </div>
  );
}

function fmtHours(h: number): string {
  const m = Math.round(h * 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)}u ${String(m % 60).padStart(2, "0")}`;
}

function Empty() {
  return <p className="text-sm text-slate-500">Nog geen metingen. Energymix slaat elke minuut de accu-stand en vermogens op.</p>;
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function SocChart({ stats, reserve, target }: { stats: StatsT; reserve: number; target: number }) {
  const days = stats.days.slice(-14);
  const W = 1000;
  const H = 210;
  const pad = 30;
  const bw = (W - pad) / days.length;
  const y = (s: number) => 8 + ((100 - s) / 100) * (H - 30);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
      {[0, 25, 50, 75, 100].map((s) => (
        <g key={s}>
          <line x1={pad} x2={W} y1={y(s)} y2={y(s)} stroke="#1e293b" strokeDasharray="2 5" />
          <text x={pad - 5} y={y(s) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{s}</text>
        </g>
      ))}
      <line x1={pad} x2={W} y1={y(reserve)} y2={y(reserve)} stroke="#fbbf24" strokeOpacity={0.4} strokeDasharray="5 5" />
      <line x1={pad} x2={W} y1={y(target)} y2={y(target)} stroke="#34d399" strokeOpacity={0.4} strokeDasharray="5 5" />
      {days.map((d, i) => {
        if (d.soc_min === null || d.soc_max === null) return null;
        const x = pad + i * bw + bw * 0.25;
        const low = d.soc_min < reserve;
        return (
          <g key={d.date}>
            <rect x={x} y={y(d.soc_max)} width={bw * 0.5} height={Math.max(2, y(d.soc_min) - y(d.soc_max))} rx={4}
              fill="url(#socgrad)" />
            <circle cx={x + bw * 0.25} cy={y(d.soc_min)} r={3} fill={low ? "#f87171" : "#34d399"} />
            <text x={x + bw * 0.25} y={H - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">
              {new Date(d.date).toLocaleDateString("nl-NL", { day: "numeric", month: "numeric" })}
            </text>
          </g>
        );
      })}
      <defs>
        <linearGradient id="socgrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#34d399" stopOpacity="0.9" />
          <stop offset="1" stopColor="#0ea5e9" stopOpacity="0.5" />
        </linearGradient>
      </defs>
    </svg>
  );
}

function EnergyChart({ stats }: { stats: StatsT }) {
  const days = stats.days.slice(-14);
  const W = 600;
  const H = 200;
  const top = 18;
  const mid = top + (H - 20 - top) / 2;
  const max = Math.max(1, ...days.flatMap((d) => [d.charged_kwh, d.discharged_kwh]));
  const bw = W / days.length;
  const sc = (v: number) => (v / max) * (mid - top - 4);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
      <line x1={0} x2={W} y1={mid} y2={mid} stroke="#334155" />
      {days.map((d, i) => (
        <g key={d.date}>
          <rect x={i * bw + bw * 0.2} y={mid - sc(d.charged_kwh)} width={bw * 0.6} height={sc(d.charged_kwh)} rx={3} fill="#34d399" opacity={0.85} />
          <rect x={i * bw + bw * 0.2} y={mid} width={bw * 0.6} height={sc(d.discharged_kwh)} rx={3} fill="#38bdf8" opacity={0.7} />
          <text x={i * bw + bw / 2} y={H - 4} textAnchor="middle" className="fill-slate-500 text-[10px]">
            {new Date(d.date).toLocaleDateString("nl-NL", { day: "numeric" })}
          </text>
        </g>
      ))}
      <text x={0} y={11} className="fill-emerald-300/80 text-[11px]">▲ geladen</text>
      <text x={80} y={11} className="fill-sky-300/80 text-[11px]">▼ ontladen</text>
      <text x={W} y={11} textAnchor="end" className="fill-slate-500 text-[11px]">max {max.toFixed(0)} kWh/dag</text>
    </svg>
  );
}

const pct = (v: number) => `${Math.round(v * 100)}%`;

function EfficiencyCycles({ cycles }: { cycles: EfficiencyCycle[] }) {
  if (!cycles.length)
    return (
      <p className="text-[11px] text-slate-500">
        Nog geen cyclus rond. Een cyclus telt als de accu minstens 15 procentpunt wegloopt en weer op dezelfde stand
        uitkomt, ook als dat een paar dagen duurt.
      </p>
    );
  const fmt = (iso: string) =>
    new Date(iso).toLocaleString("nl-NL", { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
  return (
    <table className="w-full text-[11px] text-slate-400">
      <tbody>
        {[...cycles].reverse().map((c) => (
          <tr key={c.start} className="border-t border-white/5">
            <td className="py-1 pr-2">{fmt(c.start)} → {fmt(c.end)}</td>
            <td className="pr-2">{c.soc_min}–{c.soc_max}%</td>
            <td className="pr-2">{c.in_kwh} / {c.out_kwh} kWh</td>
            <td className="text-right text-slate-200">{pct(c.efficiency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type Tip = { x: number; y: number; text: string } | null;

/** Klein zwevend label boven een punt in een grafiek; werkt met muis en met tikken. */
function TipBox({ tip }: { tip: Tip }) {
  if (!tip) return null;
  return (
    <div
      className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border border-white/10 bg-slate-900/95 px-2 py-1 text-[11px] text-slate-100 shadow-lg"
      style={{
        left: `${tip.x}%`,
        top: `calc(${tip.y}% - 8px)`,
        transform: `translate(${tip.x > 70 ? "-100%" : tip.x < 30 ? "0" : "-50%"}, -100%)`,
      }}
    >
      {tip.text}
    </div>
  );
}

/** Onzichtbare, ruime cirkel rond een punt die het label laat zien. */
function HitDot({ cx, cy, W, H, text, setTip }: { cx: number; cy: number; W: number; H: number; text: string; setTip: (t: Tip) => void }) {
  const show = () => setTip({ x: (cx / W) * 100, y: (cy / H) * 100, text });
  return (
    <circle cx={cx} cy={cy} r={14} fill="transparent" style={{ cursor: "pointer" }}
      onMouseEnter={show} onMouseLeave={() => setTip(null)} onClick={show} />
  );
}

function PowerCurve({ curve, spec }: {
  curve?: { bins: PowerBin[]; standby_w: number | null };
  spec?: VictronSpec;
}) {
  const [tip, setTip] = useState<Tip>(null);
  const bins = curve?.bins ?? [];
  const standby = curve?.standby_w;
  const note = (
    <p className="mt-1 text-[11px] text-slate-500">
      {standby != null
        ? `Eigenverbruik van de Multi's: ${standby} W, ≈${((standby * 24) / 1000).toFixed(1)} kWh per dag. Dat telt in het rendement heen en terug mee als verlies.`
        : ""}
      {spec ? ` Volgens Victron: ${spec.units} × ${spec.zero_load_w} W = ${spec.standby_w} W zonder last (${spec.model}).` : ""}
    </p>
  );
  if (!bins.length)
    return (
      <>
        <p className="text-[11px] text-slate-500">Nog te weinig minuten met laden of ontladen per vermogen.</p>
        {note}
      </>
    );
  const W = 500;
  const H = 150;
  const pad = 34;
  const maxKw = Math.max(...bins.map((b) => b.kw));
  const specPts = (spec?.curve ?? []).filter((c) => c.kw <= maxKw);
  const vals = [
    ...bins.flatMap((b) => [b.charge, b.discharge]).filter((v): v is number => v !== null),
    ...specPts.map((c) => c.efficiency),
  ];
  const lo = Math.max(0.6, Math.floor((Math.min(...vals) - 0.02) * 50) / 50);
  const hi = Math.min(1, Math.ceil((Math.max(...vals) + 0.01) * 50) / 50);
  const x = (kw: number) => pad + 14 + ((kw - 1) / Math.max(1, maxKw - 1)) * (W - pad - 28);
  const y = (v: number) => 18 + ((hi - v) / (hi - lo)) * (H - 40);
  const path = (pts: [number, number][]) => pts.map(([a, b], i) => `${i ? "L" : "M"}${x(a).toFixed(1)},${y(b).toFixed(1)}`).join(" ");
  const series = (key: "charge" | "discharge", color: string, label: string) => {
    const pts = bins.filter((b) => b[key] !== null);
    return (
      <g>
        <path d={path(pts.map((b) => [b.kw, b[key]!]))} fill="none" stroke={color} strokeWidth={2} />
        {pts.map((b) => (
          <circle key={b.kw} cx={x(b.kw)} cy={y(b[key]!)} r={3.5} fill={color} />
        ))}
        {pts.map((b) => {
          const exp = specPts.find((c) => c.kw === b.kw);
          return (
            <HitDot key={`h${b.kw}`} cx={x(b.kw)} cy={y(b[key]!)} W={W} H={H} setTip={setTip}
              text={`${label} rond ${b.kw} kW: ${pct(b[key]!)} gemeten (${key === "charge" ? b.n_charge : b.n_discharge} min)${exp ? `, Victron ±${pct(exp.efficiency)}` : ""}${b.roundtrip ? ` · heen en terug ${pct(b.roundtrip)}` : ""}`} />
          );
        })}
      </g>
    );
  };
  return (
    <>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
          {[lo, (lo + hi) / 2, hi].map((v) => (
            <g key={v}>
              <line x1={pad} x2={W} y1={y(v)} y2={y(v)} stroke="#1e293b" strokeDasharray="2 5" />
              <text x={pad - 5} y={y(v) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{pct(v)}</text>
            </g>
          ))}
          {bins.map((b) => (
            <text key={b.kw} x={x(b.kw)} y={H - 4} textAnchor="middle" className="fill-slate-500 text-[10px]">{b.kw} kW</text>
          ))}
          {specPts.length > 1 && (
            <g>
              <path d={path(specPts.map((c) => [c.kw, c.efficiency]))} fill="none" stroke="#fbbf24" strokeOpacity={0.7}
                strokeWidth={1.5} strokeDasharray="5 4" />
              {specPts.map((c) => (
                <HitDot key={`s${c.kw}`} cx={x(c.kw)} cy={y(c.efficiency)} W={W} H={H} setTip={setTip}
                  text={`Victron verwacht bij ${c.kw} kW: ±${pct(c.efficiency)} per omzetting (benadering uit de datasheet)`} />
              ))}
            </g>
          )}
          {series("charge", "#34d399", "Laden")}
          {series("discharge", "#38bdf8", "Ontladen")}
          <text x={W} y={11} textAnchor="end" className="fill-emerald-300/80 text-[11px]">● laden</text>
          <text x={W - 52} y={11} textAnchor="end" className="fill-sky-300/80 text-[11px]">● ontladen</text>
          {specPts.length > 1 && <text x={W - 120} y={11} textAnchor="end" className="fill-amber-300/80 text-[11px]">- - Victron</text>}
        </svg>
        <TipBox tip={tip} />
      </div>
      {note}
    </>
  );
}


function EfficiencyChecks({ checks }: { checks: EfficiencyCheck[] }) {
  return (
    <details className="mt-2 text-[11px] text-slate-500">
      <summary className="cursor-pointer select-none">Per dag gemeten</summary>
      <table className="mt-1 w-full">
        <tbody>
          {checks.map((c) => (
            <tr key={c.date} className="border-t border-white/5">
              <td className="py-1 pr-2">{new Date(c.date).toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "numeric" })}</td>
              <td className="pr-2">{c.source ? `${c.hours} u (${c.source})` : "–"}</td>
              <td className="pr-2">{c.source ? `${c.in_kwh} kWh erin, ${c.out_kwh} eruit` : ""}</td>
              <td className="pr-2">{c.stored_kwh !== null ? `accu ${c.stored_kwh > 0 ? "+" : ""}${c.stored_kwh} kWh` : ""}</td>
              <td className="text-right">{c.efficiency !== null ? pct(c.efficiency) : c.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function EfficiencyChart({ days, setting }: { days: EfficiencyDay[]; setting: number }) {
  const [tip, setTip] = useState<Tip>(null);
  const W = 1000;
  const H = 120;
  const pad = 34;
  const vals = [...days.map((d) => d.efficiency), setting];
  const lo = Math.max(0.5, Math.floor((Math.min(...vals) - 0.02) * 50) / 50);
  const hi = Math.min(1, Math.ceil((Math.max(...vals) + 0.02) * 50) / 50);
  const y = (v: number) => 8 + ((hi - v) / (hi - lo)) * (H - 28);
  const x = (i: number) => (days.length === 1 ? (pad + W) / 2 : pad + 26 + (i / (days.length - 1)) * (W - pad - 52));
  const line = days.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.efficiency).toFixed(1)}`).join(" ");
  const every = Math.max(1, Math.ceil(days.length / 10));
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
        {[lo, (lo + hi) / 2, hi].map((v) => (
          <g key={v}>
            <line x1={pad} x2={W} y1={y(v)} y2={y(v)} stroke="#1e293b" strokeDasharray="2 5" />
            <text x={pad - 5} y={y(v) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{pct(v)}</text>
          </g>
        ))}
        <line x1={pad} x2={W} y1={y(setting)} y2={y(setting)} stroke="#fbbf24" strokeOpacity={0.5} strokeDasharray="5 5" />
        <path d={line} fill="none" stroke="#38bdf8" strokeWidth={2} strokeLinejoin="round" />
        {days.map((d, i) => (
          <g key={d.date}>
            <circle cx={x(i)} cy={y(d.efficiency)} r={3.5} fill="#38bdf8" />
            {i % every === 0 && (
              <text x={x(i)} y={H - 4} textAnchor="middle" className="fill-slate-500 text-[10px]">
                {new Date(d.date).toLocaleDateString("nl-NL", { day: "numeric", month: "numeric" })}
              </text>
            )}
          </g>
        ))}
        {days.map((d, i) => (
          <HitDot key={`h${d.date}`} cx={x(i)} cy={y(d.efficiency)} W={W} H={H} setTip={setTip}
            text={`${new Date(d.date).toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" })}: ${pct(d.efficiency)} (${d.in_kwh} kWh erin, ${d.out_kwh} kWh eruit)`} />
        ))}
      </svg>
      <TipBox tip={tip} />
    </div>
  );
}


function PlanFacts({ plan, tz }: { plan: Plan | null; tz: string }) {
  if (!plan) return null;
  const s = plan.summary;
  const se = plan.season;
  const rows: [string, string][] = [
    ["Seizoenpatroon", se.effective === "day" ? "Zomer: middag goedkoopst" : se.effective === "night" ? "Winter: nacht goedkoopst" : se.effective === "neutral" ? "Neutraal" : "Onbekend"],
    ["Gem. middag / nacht", se.day_avg !== null && se.night_avg !== null ? `${eur(se.day_avg)} / ${eur(se.night_avg)}` : "–"],
    ["Van net laden (gepland)", s.grid_charge_kwh !== undefined ? `${s.grid_charge_kwh} kWh` : "–"],
    ["Terugleveren (gepland)", s.export_kwh !== undefined ? `${s.export_kwh} kWh` : "–"],
    ["Laagste verwachte stand", s.soc_min !== undefined && s.soc_min_at ? `${s.soc_min}% om ${relDay(s.soc_min_at, tz)}` : "–"],
    ["Verwacht huis / zon (24 u)", s.house_kwh_24h !== undefined ? `${s.house_kwh_24h} kWh / ${s.pv_kwh_24h ?? 0} kWh` : "–"],
    ["Accu leeg (zonder sturing)", s.empty_at ? relDay(s.empty_at, tz) : "niet binnen de planning"],
    ["Accu op doel", s.target_reached_at ? relDay(s.target_reached_at, tz) : "niet binnen de planning"],
    ["Kosten planning", s.cost_eur !== undefined ? `${eur(s.cost_eur, 2)} (zonder sturing ${eur(s.baseline_cost_eur ?? 0, 2)})` : "–"],
  ];
  return (
    <dl className="divide-y divide-white/5 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4 py-2">
          <dt className="text-slate-400">{k}</dt>
          <dd className="text-right text-slate-200">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function HouseProfileNote({ hp }: { hp: NonNullable<StatsT["house_profile"]> }) {
  if (hp.hours_measured === undefined) return null;
  const learned = hp.hours_measured >= 6;
  return (
    <p className={`mt-3 text-[11px] leading-snug ${learned ? "text-slate-500" : "text-amber-300/80"}`}>
      {learned
        ? `Huisverbruik geleerd uit ${hp.hours_measured} van de 24 uur (gemiddeld ${hp.avg_w} W, laatste 14 dagen, zonder auto).`
        : `Huisverbruik nog niet geleerd: de planner rekent met vast ${hp.default_w} W. `
          + `Van ${hp.samples} metingen hadden er ${hp.skipped_no_value} geen huiswaarde en ${hp.skipped_car} vielen weg omdat de auto laadde (Zappi boven 500 W). `
          + `Stel onder Instellingen → Net de huisverbruik-sensor in als het berekenen niet lukt.`}
    </p>
  );
}

function PassthroughChart({ pt, tz }: { pt: Passthrough; tz: string }) {
  const [tip, setTip] = useState<Tip>(null);
  if (!pt.slots.length)
    return (
      <p className="text-sm text-slate-500">
        In deze planning haalt het huis nergens stroom van het net terwijl de accu zijn lading houdt. Dat gebeurt als de
        accu vol is in een goedkoop laadmoment, of met "Accu bewaren" aan als de stroom nu goedkoper is dan later. De accu
        blijft dan staan (niet ontladen, niet laden); valt het net weg, dan neemt de Multi het huis gewoon over.
      </p>
    );
  // Per uur samenvoegen, anders worden het bij kwartierprijzen te dunne staafjes
  const hours: { start: string; end: string; kwh: number; gain: number; cost: number; kind: Set<string> }[] = [];
  for (const s of pt.slots) {
    const hour = s.start.slice(0, 13);
    const last = hours[hours.length - 1];
    if (last && last.start.slice(0, 13) === hour) {
      last.kwh += s.kwh;
      last.gain += s.gain_eur;
      last.cost += s.kwh * s.price;
      last.end = s.end;
      last.kind.add(s.kind);
    } else hours.push({ start: s.start, end: s.end, kwh: s.kwh, gain: s.gain_eur, cost: s.kwh * s.price, kind: new Set([s.kind]) });
  }
  const W = 1000;
  const H = 150;
  const top = 22;
  const base = H - 22;
  const max = Math.max(0.01, ...hours.map((h) => Math.abs(h.gain)));
  const bw = Math.min(60, W / hours.length);
  const sc = (v: number) => (Math.abs(v) / max) * (base - top);
  const every = Math.max(1, Math.ceil(hours.length / 12));
  let cum = 0;
  const cums = hours.map((h) => (cum += h.gain));
  const peak = Math.max(0.01, ...cums);
  const line = cums
    .map((c, i) => `${i ? "L" : "M"}${(i * bw + bw / 2).toFixed(1)},${(base - (Math.max(0, c) / peak) * (base - top)).toFixed(1)}`)
    .join(" ");
  const showTip = (i: number, h: (typeof hours)[number]) =>
    setTip({
      x: ((i * bw + bw / 2) / W) * 100,
      y: ((base - sc(h.gain)) / H) * 100,
      text: `${relDay(h.start, tz)}–${time(h.end, tz)}: ${h.kwh.toFixed(1).replace(".", ",")} kWh van het net à ${eur(h.cost / Math.max(0.001, h.kwh))}, ${h.kind.has("hold") ? "accu bewaren" : "accu vol"}; scheelt ${eur(h.gain, 2)}`,
    });
  return (
    <div>
      <p className="mb-2 text-sm text-slate-300">
        In {hours.length} {hours.length === 1 ? "uur" : "uren"} haalt het huis {pt.kwh.toString().replace(".", ",")} kWh direct van het net
        en blijft de accu staan. Stroom uit de accu is later ongeveer {eur(pt.later_eur_kwh ?? 0)} per kWh waard; nu kost het net
        gemiddeld {eur(hours.reduce((a, h) => a + h.cost, 0) / Math.max(0.001, pt.kwh))}. Samen scheelt dat{" "}
        <b>{eur(pt.value_eur, 2)}</b> tegenover de accu nu laten ontladen.
      </p>
      <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
        <line x1={0} x2={W} y1={base} y2={base} stroke="#334155" />
        {hours.map((h, i) => (
          <g key={h.start}>
            <rect x={i * bw + bw * 0.15} y={base - sc(h.gain)} width={bw * 0.7} height={sc(h.gain)} rx={3}
              fill={h.gain >= 0 ? (h.kind.has("hold") ? "#a78bfa" : "#34d399") : "#f87171"} opacity={0.8} />
            {/* Brede onzichtbare strook: de tooltip komt ook als je er net naast zit */}
            <rect x={i * bw} y={top - 10} width={bw} height={base - top + 10} fill="transparent" style={{ cursor: "pointer" }}
              onMouseEnter={() => showTip(i, h)} onMouseLeave={() => setTip(null)} onClick={() => showTip(i, h)} />
            {i % every === 0 && (
              <text x={i * bw + bw / 2} y={H - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">{time(h.start, tz)}</text>
            )}
          </g>
        ))}
        <path d={line} fill="none" stroke="#fbbf24" strokeWidth={2} strokeOpacity={0.7} strokeLinejoin="round" pointerEvents="none" />
        <text x={0} y={12} className="fill-emerald-300/80 text-[11px]">▮ accu vol</text>
        <text x={90} y={12} className="fill-violet-300/80 text-[11px]">▮ accu bewaren</text>
        <text x={210} y={12} className="fill-amber-300/80 text-[11px]">― opgeteld</text>
        <text x={W} y={12} textAnchor="end" className="fill-slate-500 text-[11px]">max {eur(max, 2)} per uur</text>
      </svg>
      <TipBox tip={tip} />
      </div>
    </div>
  );
}
