import type { Plan, Stats as StatsT, Status } from "./types";
import { Card } from "./ui";
import { eur, relDay, watt } from "./format";

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
        </Card>
      </div>
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
