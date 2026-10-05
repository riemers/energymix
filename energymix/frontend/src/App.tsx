import { useCallback, useEffect, useState } from "react";
import { getDecisions, getStatus, replan } from "./api";
import { COMPONENTS, dayTime, eur, priceColor, time, valueLabel } from "./format";
import Timeline from "./Timeline";
import type { Action, Decision, Status } from "./types";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([getStatus(), getDecisions(40)]);
      setStatus(s);
      setDecisions(d);
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

  const onReplan = async () => {
    setBusy(true);
    try {
      setStatus(await replan());
      setDecisions(await getDecisions(40));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const tz = status?.timezone ?? "Europe/Amsterdam";
  const plan = status?.plan;
  const nowSlot = plan?.slots.find((s) => {
    const t = Date.parse(plan.created_at);
    return Date.parse(s.start) <= t && t < Date.parse(s.end);
  });

  return (
    <div className="mx-auto max-w-6xl px-4 pb-16 pt-6 sm:px-6">
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-emerald-400 to-sky-500 text-lg shadow-lg shadow-emerald-500/20">
            ⚡
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight tracking-tight">Energymix</h1>
            <p className="text-xs text-slate-400">
              {plan ? `plan van ${time(plan.created_at, tz)}` : "laden…"}
            </p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {status && <ModeBadge status={status} />}
          {status && (
            <span
              title={status.ha_connected ? "Verbonden met Home Assistant" : "Geen verbinding met Home Assistant"}
              className={`h-2.5 w-2.5 rounded-full ${status.ha_connected ? "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400" : "bg-red-500"}`}
            />
          )}
          <button
            onClick={onReplan}
            disabled={busy}
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm transition hover:bg-white/10 disabled:opacity-50"
          >
            {busy ? "Plannen…" : "↻ Opnieuw plannen"}
          </button>
        </div>
      </header>

      {(!!error || !!status?.errors.length) && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-200">
          {error ?? status?.errors.join(" · ")}
        </div>
      )}

      {status && (
        <>
          <section className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <PriceCard status={status} />
            <Stat
              label="Thuisaccu"
              value={status.state?.soc != null ? `${status.state.soc.toFixed(0)}%` : "–"}
              sub={nowSlot ? valueLabel("ess", nowSlot.ess_state) : undefined}
              bar={status.state?.soc ?? undefined}
            />
            <Stat
              label="Zon vandaag"
              value={status.state?.solar_today_kwh != null ? `${status.state.solar_today_kwh.toFixed(0)} kWh` : "–"}
              sub={status.state?.sunchance != null ? `zonkans ${status.state.sunchance.toFixed(0)}%` : undefined}
            />
            <CarCard status={status} />
          </section>

          <section className="card mb-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-medium text-slate-300">Komende {status.plan ? "uren" : ""}</h2>
              {plan?.charge_window && (
                <span className="rounded-full bg-pink-400/10 px-2.5 py-0.5 text-xs text-pink-300">
                  laadvenster {time(plan.charge_window[0], tz)}–{time(plan.charge_window[1], tz)}
                </span>
              )}
            </div>
            {plan && <Timeline plan={plan} tz={tz} cheap={status.cheap_price} fast={status.force_fast_price} />}
            {!!plan?.notes.length && (
              <ul className="mt-3 space-y-1 text-xs text-slate-400">
                {plan.notes.map((n) => (
                  <li key={n}>• {n}</li>
                ))}
              </ul>
            )}
          </section>

          <section className="mb-4">
            <h2 className="mb-2 px-1 text-sm font-medium text-slate-300">Nu, en waarom</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {status.actions.map((a) => (
                <ActionCard key={a.component} a={a} />
              ))}
              {!status.actions.length && <p className="text-sm text-slate-500">Nog geen acties.</p>}
            </div>
          </section>

          <section className="card">
            <h2 className="mb-3 text-sm font-medium text-slate-300">Beslissingen</h2>
            <DecisionLog items={decisions} tz={tz} />
          </section>
        </>
      )}
    </div>
  );
}

function ModeBadge({ status }: { status: Status }) {
  if (status.shadow)
    return (
      <span
        title="Shadow mode: de add-on rekent en logt, maar stuurt niets aan"
        className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-xs font-medium text-amber-300"
      >
        SHADOW
      </span>
    );
  const live = Object.entries(status.control).filter(([, v]) => v).map(([k]) => COMPONENTS[k]?.label ?? k);
  return (
    <span
      title={`Live: ${live.join(", ")}`}
      className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-0.5 text-xs font-medium text-emerald-300"
    >
      LIVE · {live.length}
    </span>
  );
}

function PriceCard({ status }: { status: Status }) {
  const plan = status.plan;
  if (!plan) return <Stat label="Prijs nu" value="–" />;
  const t = Date.parse(plan.created_at);
  const now = plan.slots.find((s) => Date.parse(s.start) <= t && t < Date.parse(s.end));
  const next = plan.slots.slice(0, 48);
  const min = next.reduce((a, s) => (s.price < a.price ? s : a), next[0]);
  return (
    <div className="card">
      <div className="text-xs text-slate-400">Prijs nu</div>
      <div
        className="mt-1 text-3xl font-semibold tracking-tight"
        style={{ color: now ? priceColor(now.price, status.cheap_price, status.force_fast_price) : undefined }}
      >
        {now ? eur(now.price) : "–"}
      </div>
      {min && (
        <div className="mt-1 text-xs text-slate-400">
          laagste {eur(min.price)} om {time(min.start, status.timezone)}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub, bar }: { label: string; value: string; sub?: string; bar?: number }) {
  return (
    <div className="card">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight">{value}</div>
      {bar !== undefined && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/5">
          <div
            className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-300 transition-all"
            style={{ width: `${Math.max(0, Math.min(100, bar))}%` }}
          />
        </div>
      )}
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function CarCard({ status }: { status: Status }) {
  const st = status.state;
  const plan = status.plan;
  const plugged = st && st.zappi_plug && st.zappi_plug !== "EV Disconnected";
  return (
    <div className="card">
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>Auto</span>
        <span className="rounded bg-white/5 px-1.5 py-0.5 uppercase tracking-wide">{st?.carcharger_mode ?? "–"}</span>
      </div>
      <div className="mt-1 truncate text-xl font-semibold tracking-tight">
        {plan?.active_car ?? (plugged ? "Aangesloten" : "Niet aangesloten")}
      </div>
      <div className="mt-1 text-xs text-slate-400">
        {st?.zappi_mode ? `Zappi ${st.zappi_mode}` : ""}
        {plan?.charge_minutes ? ` · ${Math.round(plan.charge_minutes)} min nodig` : ""}
        {st?.vannacht ? " · vannacht" : ""}
      </div>
    </div>
  );
}

function ActionCard({ a }: { a: Action }) {
  const meta = COMPONENTS[a.component] ?? { label: a.component, icon: "•" };
  const differs = a.actual !== null && a.actual !== "" && String(a.actual) !== String(a.desired);
  return (
    <div className="card">
      <div className="flex items-center gap-2">
        <span className="text-lg">{meta.icon}</span>
        <span className="text-sm text-slate-300">{meta.label}</span>
        <span
          className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
            a.live ? "bg-emerald-400/10 text-emerald-300" : "bg-white/5 text-slate-400"
          }`}
        >
          {a.live ? "live" : "shadow"}
        </span>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-xl font-semibold">{valueLabel(a.component, a.desired)}</span>
        {differs && <span className="text-xs text-amber-300">nu: {valueLabel(a.component, a.actual)}</span>}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">{a.reason}</p>
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
        return (
          <li key={d.id} className="grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-0.5 px-4 py-2 sm:grid-cols-[5rem_9rem_7rem_1fr]">
            <span className="text-slate-500">{dayTime(d.ts, tz)}</span>
            <span className="whitespace-nowrap">
              {meta?.icon} {meta?.label ?? d.component}
              <span className="ml-2 font-medium sm:hidden">{valueLabel(d.component, d.value)}</span>
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
