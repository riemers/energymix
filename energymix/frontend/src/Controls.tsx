import { useState } from "react";
import { setHelper } from "./api";
import { eur } from "./format";
import { Leaf, Moon, Sun } from "./icons";
import { Segmented, Slider, Switch } from "./ui";
import type { HelperInfo, Status } from "./types";

interface Props {
  status: Status;
  onStatus: (s: Status) => void;
}

export default function Controls({ status, onStatus }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const h = Object.fromEntries(status.helpers.map((x) => [x.key, x])) as Record<string, HelperInfo>;

  const set = async (key: string, value: unknown) => {
    setBusy(key);
    try {
      onStatus(await setHelper(key, value));
    } finally {
      setBusy(null);
    }
  };

  const season = status.plan?.season;
  const missing = status.helpers.filter((x) => !x.exists).length;

  return (
    <div className="space-y-4">
      <div className={`-mx-1 rounded-xl px-1 py-1 transition ${h.car_boost?.value ? "bg-pink-500/10 ring-1 ring-pink-400/40" : ""}`}>
        <Row
          title="Auto nu snel laden"
          sub={h.car_boost?.value ? "Aan: Fast tot de auto vol is, daarna gaat dit vanzelf uit" : "Boeien wat het kost: nu Fast tot vol (gaat vanzelf weer uit)"}
          right={<Switch checked={!!h.car_boost?.value} disabled={busy === "car_boost"} onChange={(v) => set("car_boost", v)} />}
        />
      </div>
      <Row
        title="Aansturen"
        sub={status.master ? "Energymix stuurt de onderdelen die live staan" : "Alleen meekijken: er wordt niets geschakeld"}
        right={<Switch checked={!!h.master?.value} disabled={busy === "master"} onChange={(v) => set("master", v)} />}
        highlight={!!h.master?.value}
      />
      <Row
        title="Terugleveren bij pieken"
        sub="Accu naar het net bij een groot prijsverschil, nooit als de auto laadt"
        right={<Switch checked={!!h.export_enabled?.value} disabled={busy === "export_enabled"} onChange={(v) => set("export_enabled", v)} />}
      />
      <Row
        title="Accu goedkoop van net laden"
        sub="Alleen als het later duurdere stroom vervangt"
        right={<Switch checked={!!h.grid_charge_enabled?.value} disabled={busy === "grid_charge_enabled"} onChange={(v) => set("grid_charge_enabled", v)} />}
      />

      <div className="space-y-3 border-t border-white/5 pt-4">
        <Field label="Accu laden tot">
          <Slider value={Number(h.battery_target_soc?.value ?? 95)} min={50} max={100} step={5} unit="%" onCommit={(v) => set("battery_target_soc", v)} />
        </Field>
        <Field label="Reserve bij terugleveren">
          <Slider value={Number(h.battery_reserve_soc?.value ?? 30)} min={10} max={80} step={5} unit="%" accent="#fbbf24" onCommit={(v) => set("battery_reserve_soc", v)} />
        </Field>
        <Field label="Laadmoment auto">
          <Segmented
            value={(h.car_window?.value as "auto" | "night" | "day") ?? "auto"}
            onChange={(v) => set("car_window", v)}
            options={[
              { value: "auto", label: "Goedkoopst" },
              { value: "night", label: <><Moon size={13} /> Nacht</> },
              { value: "day", label: <><Sun size={13} /> Dag</> },
            ]}
          />
        </Field>
        <Field label={<span>Vol vóór<span className="block text-[10px] text-slate-500">alleen met "vannacht" (nu {status.state?.vannacht ? "aan" : "uit"})</span></span>}>
          <label className={`flex items-center gap-2 rounded-lg bg-white/5 px-3 py-1.5 text-sm ${status.state?.vannacht ? "" : "opacity-50"}`}>
            <input
              type="time"
              defaultValue={String(h.car_ready_time?.value ?? "07:30")}
              key={String(h.car_ready_time?.value)}
              onBlur={(e) => e.target.value && set("car_ready_time", e.target.value)}
              className="w-full bg-transparent tabular-nums outline-none [color-scheme:dark]"
            />
          </label>
        </Field>
      </div>

      <div className="space-y-2 border-t border-white/5 pt-4">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-slate-300">Seizoenpatroon</span>
          {season && season.day_avg !== null && season.night_avg !== null && (
            <span className="text-[11px] text-slate-500">
              middag {eur(season.day_avg, 2)} · nacht {eur(season.night_avg, 2)}
            </span>
          )}
        </div>
        <Segmented
          value={(h.season_mode?.value as "auto" | "day" | "night") ?? "auto"}
          onChange={(v) => set("season_mode", v)}
          options={[
            { value: "auto", label: <><Leaf size={14} /> Auto{season?.mode === "auto" && season.detected !== "unknown" ? `: ${season.detected === "day" ? "zomer" : season.detected === "night" ? "winter" : "neutraal"}` : ""}</> },
            { value: "day", label: <><Sun size={14} /> Zomer</> },
            { value: "night", label: <><Moon size={14} /> Winter</> },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-slate-500">
          Zomer: de middag is het goedkoopst (zon). Winter: de nacht is goedkoper; dan geen ochtend-eco uit de accu.
          Auto kijkt naar de prijzen van de afgelopen week.
        </p>
      </div>

      {missing > 0 && (
        <p className="text-[11px] text-amber-300/80">
          {missing} helper(s) bestaan nog niet in HA; wijzigingen worden dan in de add-on bewaard.
        </p>
      )}
    </div>
  );
}

function Row({ title, sub, right, highlight }: { title: string; sub: string; right: React.ReactNode; highlight?: boolean }) {
  return (
    <div className={`flex items-center gap-3 ${highlight ? "" : ""}`}>
      <div className="min-w-0 flex-1">
        <div className="text-sm text-slate-200">{title}</div>
        <div className="text-[11px] leading-snug text-slate-500">{sub}</div>
      </div>
      {right}
    </div>
  );
}

function Field({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_1fr] items-center gap-3">
      <span className="text-sm text-slate-300">{label}</span>
      {children}
    </div>
  );
}
