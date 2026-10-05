import { useCallback, useEffect, useState } from "react";
import { getDecisions, getLive, getStats, getStatus, replan } from "./api";
import Controls from "./Controls";
import Flow from "./Flow";
import { COMPONENTS, dayTime, eur, priceColor, relDay, time, valueLabel, watt } from "./format";
import { ICONS, Refresh } from "./icons";
import Settings from "./Settings";
import Stats from "./Stats";
import Timeline from "./Timeline";
import { Badge, Card } from "./ui";
import type { Action, Decision, Live, Stats as StatsT, Status } from "./types";

type Tab = "overzicht" | "statistiek" | "logboek" | "instellingen";
const TABS: { key: Tab; label: string }[] = [
  { key: "overzicht", label: "Overzicht" },
  { key: "statistiek", label: "Statistiek" },
  { key: "logboek", label: "Logboek" },
  { key: "instellingen", label: "Instellingen" },
];

function initialTab(): Tab {
  try {
    const t = localStorage.getItem("energymix.tab") as Tab | null;
    if (t && TABS.some((x) => x.key === t)) return t;
  } catch {
    /* geen opslag beschikbaar */
  }
  return "overzicht";
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [stats, setStats] = useState<StatsT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>(initialTab);

  const load = useCallback(async () => {
    try {
      const s = await getStatus();
      setStatus(s);
      setLive(s.live);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  // Live stroom: elke 3 s
  useEffect(() => {
    const t = setInterval(() => getLive().then(setLive).catch(() => undefined), 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("energymix.tab", tab);
    } catch {
      /* geen opslag beschikbaar */
    }
    if (tab === "logboek") getDecisions(150).then(setDecisions).catch(() => undefined);
    if (tab === "statistiek") getStats().then(setStats).catch(() => undefined);
  }, [tab, status]);

  const onReplan = async () => {
    setBusy(true);
    try {
      setStatus(await replan());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const tz = status?.timezone ?? "Europe/Amsterdam";

  return (
    <div className="mx-auto max-w-7xl px-4 pb-16 pt-5 sm:px-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        <img src="./icon.png" alt="" className="h-9 w-9 drop-shadow-lg" />
        <div className="mr-2">
          <h1 className="text-lg font-semibold leading-tight tracking-tight">Energymix</h1>
          <p className="text-xs text-slate-500">{status?.plan ? `plan van ${time(status.plan.created_at, tz)}` : "laden…"}</p>
        </div>
        <nav className="order-last -mx-1 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition ${
                tab === t.key ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {status && <ModeBadge status={status} />}
          {status && (
            <span
              title={status.ha_connected ? "Verbonden met Home Assistant" : "Geen verbinding met Home Assistant"}
              className={`h-2.5 w-2.5 rounded-full ${status.ha_connected ? "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400" : "bg-red-500"}`}
            />
          )}
          <button onClick={onReplan} disabled={busy} title="Opnieuw plannen"
            className="rounded-lg border border-white/10 bg-white/5 p-2 transition hover:bg-white/10 disabled:opacity-50">
            <Refresh size={16} className={busy ? "animate-spin" : ""} />
          </button>
        </div>
      </header>

      {(!!error || !!status?.errors.length) && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-200">
          {error ?? status?.errors.join(" · ")}
        </div>
      )}

      {status && tab === "overzicht" && <Overview status={status} live={live ?? status.live} onStatus={setStatus} />}
      {status && tab === "statistiek" && <Stats stats={stats} status={status} />}
      {tab === "logboek" && (
        <Card title="Beslissingen">
          <DecisionLog items={decisions} tz={tz} />
        </Card>
      )}
      {tab === "instellingen" && <Settings onSaved={load} />}
    </div>
  );
}

function Overview({ status, live, onStatus }: { status: Status; live: Live; onStatus: (s: Status) => void }) {
  const tz = status.timezone;
  const plan = status.plan;
  const reg = live.regulator;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="flex flex-col lg:col-span-7" title="Nu" right={<span className="text-[11px] text-slate-500">live · {time(live.ts, tz)}</span>}>
          <div className="grid flex-1 place-items-center">
            <Flow live={live} />
          </div>
          {reg.headroom_w !== null && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl bg-white/[0.03] px-3 py-2 text-xs text-slate-400">
              <span className="text-slate-300">Laadstroom accu {reg.current_a} A</span>
              <span>· ruimte op aansluiting {watt(reg.headroom_w)}</span>
              {reg.car_throttled && <Badge tone="pink">Zappi regelt terug: auto gaat voor</Badge>}
            </div>
          )}
        </Card>

        <div className="space-y-4 lg:col-span-5">
          <PriceStory status={status} live={live} />
          <Card title="Bijsturen">
            <Controls status={status} onStatus={onStatus} />
          </Card>
        </div>
      </div>

      {plan && (
        <Card
          title="Planning"
          right={
            <div className="flex flex-wrap justify-end gap-1.5">
              {plan.summary.target_reached_at && <Badge tone="green">accu {status.battery_target_soc}% om {relDay(plan.summary.target_reached_at, tz)}</Badge>}
              {plan.car.full_at && <Badge tone="pink">{plan.car.name ?? "auto"} vol om {relDay(plan.car.full_at, tz)}</Badge>}
              {(plan.summary.saving_eur ?? 0) > 0.05 && <Badge tone="amber">besparing ≈ {eur(plan.summary.saving_eur!, 2)}</Badge>}
            </div>
          }
        >
          <Timeline plan={plan} tz={tz} cheap={status.cheap_price} fast={status.force_fast_price}
            target={status.battery_target_soc} reserve={status.battery_reserve_soc} />
          {!!plan.notes.length && (
            <ul className="mt-3 space-y-1 text-xs text-amber-200/80">
              {plan.notes.map((n) => <li key={n}>• {n}</li>)}
            </ul>
          )}
        </Card>
      )}

      <div>
        <h2 className="mb-2 px-1 text-[13px] font-medium tracking-wide text-slate-400">Wat Energymix nu {status.shadow ? "zou doen" : "doet"}</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {status.actions.map((a) => <ActionCard key={a.component} a={a} />)}
          {!status.actions.length && <p className="text-sm text-slate-500">Nog geen acties.</p>}
        </div>
      </div>
    </div>
  );
}

function PriceStory({ status, live }: { status: Status; live: Live }) {
  const plan = status.plan;
  const tz = status.timezone;
  const p = live.price;
  const next = plan?.slots.slice(0, 48) ?? [];
  const min = next.length ? next.reduce((a, s) => (s.price < a.price ? s : a), next[0]) : null;
  const max = next.length ? next.reduce((a, s) => (s.price > a.price ? s : a), next[0]) : null;
  return (
    <Card>
      <div className="flex items-start gap-4">
        <div className="shrink-0">
          <div className="text-[11px] uppercase tracking-wider text-slate-500">Prijs nu</div>
          <div className="text-2xl font-semibold tracking-tight" style={{ color: p !== null ? priceColor(p, status.cheap_price, status.force_fast_price) : undefined }}>
            {p !== null ? eur(p) : "–"}
          </div>
          {min && max && (
            <div className="mt-1 space-y-0.5 text-[11px] text-slate-500">
              <div>laagst {eur(min.price, 2)} · {relDay(min.start, tz)}</div>
              <div>hoogst {eur(max.price, 2)} · {relDay(max.start, tz)}</div>
            </div>
          )}
          <Spark plan={plan} status={status} />
        </div>
        <div className="min-w-0 flex-1 border-l border-white/5 pl-4">
          <div className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Wat er gebeurt</div>
          <ul className="space-y-1.5 text-[13px] leading-snug text-slate-300">
            {status.story.slice(1).map((s) => <li key={s}>{s}</li>)}
          </ul>
        </div>
      </div>
    </Card>
  );
}

function Spark({ plan, status }: { plan: Status["plan"]; status: Status }) {
  const slots = plan?.slots.slice(0, 48) ?? [];
  if (slots.length < 2) return null;
  const W = 120;
  const H = 30;
  const prices = slots.map((s) => s.price);
  const mx = Math.max(...prices, 0.01);
  const mn = Math.min(...prices, 0);
  const bw = W / slots.length;
  return (
    <svg width={W} height={H} className="mt-2">
      {slots.map((s, i) => {
        const h = Math.max(1, ((s.price - mn) / (mx - mn)) * H);
        return <rect key={s.start} x={i * bw} y={H - h} width={Math.max(1, bw - 0.6)} height={h} rx={1}
          fill={priceColor(s.price, status.cheap_price, status.force_fast_price)} opacity={i === 0 ? 1 : 0.6} />;
      })}
    </svg>
  );
}

function ModeBadge({ status }: { status: Status }) {
  if (status.shadow)
    return (
      <span title={status.master ? "Geen onderdelen live in de add-on-configuratie" : "Hoofdschakelaar 'Aansturen' staat uit"}>
        <Badge tone="amber">MEEKIJKEN</Badge>
      </span>
    );
  const live = Object.entries(status.control).filter(([, v]) => v).map(([k]) => COMPONENTS[k]?.label ?? k);
  return (
    <span title={`Live: ${live.join(", ")}`}>
      <Badge tone="green">LIVE · {live.length}</Badge>
    </span>
  );
}

function ActionCard({ a }: { a: Action }) {
  const meta = COMPONENTS[a.component] ?? { label: a.component, icon: "bolt" };
  const Icon = ICONS[meta.icon] ?? ICONS.bolt;
  const differs = a.actual !== null && a.actual !== "" && String(a.actual) !== String(a.desired);
  return (
    <div className="card !p-3.5">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-white/5 text-slate-300">
          <Icon size={16} />
        </span>
        <span className="text-sm text-slate-300">{meta.label}</span>
        <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${a.live ? "bg-emerald-400/10 text-emerald-300" : "bg-white/5 text-slate-500"}`}>
          {a.live ? "live" : "meekijken"}
        </span>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-lg font-semibold">{valueLabel(a.component, a.desired)}</span>
        {differs && <span className="text-xs text-amber-300">nu: {valueLabel(a.component, a.actual)}</span>}
      </div>
      <p className="mt-0.5 text-xs leading-relaxed text-slate-400">{a.reason}</p>
      {a.error && <p className="mt-1 text-xs text-red-300">{a.error}</p>}
    </div>
  );
}

function DecisionLog({ items, tz }: { items: Decision[]; tz: string }) {
  if (!items.length) return <p className="text-sm text-slate-500">Nog niets gelogd.</p>;
  return (
    <ul className="-mx-4 divide-y divide-white/5 text-xs">
      {items.map((d) => {
        const meta = COMPONENTS[d.component];
        const Icon = ICONS[meta?.icon ?? "bolt"] ?? ICONS.bolt;
        return (
          <li key={d.id} className="grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-0.5 px-4 py-2 sm:grid-cols-[5.5rem_9.5rem_8rem_1fr]">
            <span className="text-slate-500">{dayTime(d.ts, tz)}</span>
            <span className="flex items-center gap-1.5 whitespace-nowrap">
              <Icon size={13} className="text-slate-500" /> {meta?.label ?? d.component}
              <span className="ml-1 font-medium sm:hidden">{valueLabel(d.component, d.value)}</span>
            </span>
            <span className="hidden font-medium sm:block">
              {valueLabel(d.component, d.value)}
              {d.executed ? <span className="ml-1.5 text-emerald-400">✓</span> : null}
            </span>
            <span className="col-start-2 text-slate-400 sm:col-start-auto">{d.reason}</span>
          </li>
        );
      })}
    </ul>
  );
}
