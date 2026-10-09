import { useEffect, useRef, useState } from "react";
import { getSettings, saveSettings, searchEntities } from "./api";
import { COMPONENTS } from "./format";
import { Badge, Card } from "./ui";
import type { EntityOption, Live, SettingField, Settings as SettingsT } from "./types";

export default function Settings({ onSaved, live }: { onSaved: () => void; live?: Live }) {
  const [data, setData] = useState<SettingsT | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    getSettings().then(setData).catch((e) => setMsg(String(e)));
  }, []);

  if (!data) return <p className="text-sm text-slate-500">{msg ?? "Laden…"}</p>;
  const groups = [...new Set(data.fields.map((f) => f.group))];
  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      setData(await saveSettings(draft));
      setDraft({});
      setMsg("Opgeslagen en opnieuw gepland");
      onSaved();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 3000);
    }
  };

  return (
    <div className="space-y-4 pb-20">
      <Card title="Wat mag Energymix aansturen?">
        <div className="flex flex-wrap gap-2">
          {Object.entries(data.control).map(([k, v]) => (
            <Badge key={k} tone={v ? "green" : "slate"}>
              {COMPONENTS[k]?.label ?? k}: {v ? "live" : "alleen meekijken"}
            </Badge>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Dit zet je per onderdeel in de add-on-configuratie (<code>control</code>). De hoofdschakelaar "Aansturen" op het
          overzicht zet alles in één keer uit.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {groups.map((g) => (
          <Card key={g} title={g}>
            <div className="space-y-3">
              {data.fields.filter((f) => f.group === g).map((f) => (
                <FieldRow key={f.key} f={f} value={(draft[f.key] ?? f.value) as string | number}
                  onChange={(v) => setDraft((d) => ({ ...d, [f.key]: v }))} />
              ))}
            </div>
          </Card>
        ))}
      </div>

      <Card title="Auto's">
        <div className="grid gap-3 sm:grid-cols-2">
          {data.cars.map((c) => {
            const lc = live?.cars.find((x) => x.name === c.name);
            const ents = lc?.entities ?? {};
            return (
              <div key={c.name} className="rounded-xl bg-white/[0.03] p-3 text-xs">
                <div className="mb-1 text-sm font-medium text-slate-200">{c.name}</div>
                <div className="text-slate-400">
                  vol: {lc?.learned_max_km ? <><b className="text-slate-200">{lc.learned_max_km} km</b> geleerd</> : `${c.max_range_km} km ingesteld`}
                  {lc?.learned_max_km ? <span className="text-slate-600"> (ingesteld {c.max_range_km})</span> : null}
                </div>
                <div className="text-slate-400">
                  laadsnelheid: {lc?.learned_speed_kmh ? <><b className="text-slate-200">{Math.round(lc.learned_speed_kmh)} km/u</b> gemeten</> : "nog niet gemeten"}
                </div>
                <div className="mt-2 space-y-0.5 text-[11px]">
                  {[["battery_level", "accu %"], ["charge_limit", "laadlimiet"], ["time_to_full", "tijd tot vol"], ["charge_rate", "laadsnelheid"]].map(([k, l]) => (
                    <div key={k} className="flex gap-2">
                      <span className={ents[k] ? "text-emerald-400" : "text-slate-600"}>{ents[k] ? "✓" : "–"}</span>
                      <span className="w-20 text-slate-500">{l}</span>
                      <span className="truncate text-slate-400">{ents[k] ?? "niet gevonden"}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Max km en laadsnelheid leert Energymix uit de Tesla-gegevens (accu %, laadlimiet, laadsnelheid). Niet gevonden?
          Vul de entity in bij <code>cars</code> in de add-on-configuratie (bv. <code>battery_level_entity</code>).
        </p>
      </Card>

      {(dirty || msg) && (
        <div className="fixed inset-x-0 bottom-4 z-20 flex justify-center px-4">
          <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-ink-800/95 px-4 py-2.5 shadow-2xl backdrop-blur">
            <span className="text-sm text-slate-300">{msg ?? `${Object.keys(draft).length} wijziging(en)`}</span>
            {dirty && (
              <>
                <button onClick={() => setDraft({})} className="rounded-lg px-3 py-1.5 text-sm text-slate-400 hover:text-white">
                  Annuleren
                </button>
                <button onClick={save} disabled={saving}
                  className="rounded-lg bg-emerald-500 px-3 py-1.5 text-sm font-medium text-emerald-950 hover:bg-emerald-400 disabled:opacity-50">
                  {saving ? "Opslaan…" : "Opslaan"}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function FieldRow({ f, value, onChange }: { f: SettingField; value: string | number; onChange: (v: unknown) => void }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-slate-400">{f.label}</label>
      {f.kind === "battery" ? (
        <BatteryChoice f={f} value={String(value)} onChange={onChange} />
      ) : f.kind === "entity" ? (
        <>
          <EntityPicker value={String(value ?? "")} current={f} onChange={onChange} />
          {!value && f.suggestion && (
            <div className="mt-1.5 flex items-center gap-2 rounded-lg bg-emerald-400/[0.06] px-2.5 py-1.5 text-[11px]">
              <span className="text-emerald-300/90">Gevonden:</span>
              <span className="min-w-0 flex-1 truncate text-slate-300">{f.suggestion.entity_id}</span>
              <span className="shrink-0 text-slate-500">
                {f.suggestion.state}
                {f.suggestion.unit ? ` ${f.suggestion.unit}` : ""}
              </span>
              <button type="button" onClick={() => onChange(f.suggestion!.entity_id)}
                className="shrink-0 rounded-md bg-emerald-500/20 px-2 py-0.5 font-medium text-emerald-200 hover:bg-emerald-500/30">
                gebruik
              </button>
            </div>
          )}
          {!!value && f.age_s !== null && f.age_s > 12 * 3600 && (
            <div className="mt-1 text-[11px] text-amber-300/90">
              ⚠ al {fmtAge(f.age_s)} niet bijgewerkt: klopt deze entity nog?
            </div>
          )}
          {!!value && f.fallback && <div className="mt-1 text-[11px] text-sky-300/80">✓ {f.fallback.label}</div>}
          {!value && f.fallback && (
            <div className={`mt-1 text-[11px] ${f.fallback.active ? "text-sky-300/80" : "text-slate-500"}`}>
              {f.fallback.active ? "✓ " : ""}leeg laten = {f.fallback.label}
              {!f.fallback.active && " (nog geen data)"}
            </div>
          )}
        </>
      ) : f.kind.startsWith("select:") ? (
        <select value={String(value)} onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none focus:border-emerald-400/50">
          {f.kind.slice(7).split(",").map((o) => (
            <option key={o} value={o} className="bg-ink-900">
              {o === "total" ? "Totaalprijs (salderen)" : o === "energy" ? "Kale energieprijs"
                : o === "measured" ? "Gemeten (uit laden en ontladen, anders het getal hierboven)" : o === "fixed" ? "Vast (het getal hierboven)"
                : o === "careful" ? "Voorzichtig (alleen bekende prijzen)" : o === "week" ? "Zoals de afgelopen week (eerder vol laden)" : o}
            </option>
          ))}
        </select>
      ) : f.kind === "text" ? (
        <>
          <input type="text" value={String(value)} onChange={(e) => onChange(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none focus:border-emerald-400/50" />
          {f.fallback && <div className="mt-1 text-[11px] text-sky-300/80">✓ {f.fallback.label}</div>}
        </>
      ) : (
        <NumberInput value={value} onChange={onChange} />
      )}
    </div>
  );
}

// Getal als tekst bewerken: met type="number" en Number() verdween de punt tijdens het typen
// ("0." werd 0), en een komma (0,04) werkte niet. Nu mag allebei.
function NumberInput({ value, onChange }: { value: string | number; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(String(value ?? ""));
  useEffect(() => {
    if (Number(text.replace(",", ".")) !== Number(value)) setText(String(value ?? ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  const parsed = Number(text.replace(",", "."));
  const valid = text.trim() !== "" && Number.isFinite(parsed);
  return (
    <input type="text" inputMode="decimal" value={text}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value.replace(",", "."));
        if (e.target.value.trim() !== "" && Number.isFinite(n)) onChange(n);
      }}
      className={`w-full rounded-lg border bg-white/5 px-3 py-2 text-sm tabular-nums outline-none focus:border-emerald-400/50 ${
        valid ? "border-white/10" : "border-rose-400/60"}`} />
  );
}

function EntityPicker({ value, current, onChange }: { value: string; current: SettingField; onChange: (v: string) => void }) {
  const [q, setQ] = useState(value);
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<EntityOption[]>([]);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => setQ(value), [value]);
  useEffect(() => {
    if (!open) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      searchEntities(q).then(setOpts).catch(() => setOpts([]));
    }, 200);
  }, [q, open]);

  const showState = value === current.value && current.state !== null;
  return (
    <div className="relative">
      <div className="flex items-center rounded-lg border border-white/10 bg-white/5 focus-within:border-emerald-400/50">
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="zoek entity…"
          className="min-w-0 flex-1 bg-transparent px-3 py-2 text-sm outline-none"
        />
        {showState && (
          <span className="mr-2 shrink-0 rounded bg-white/5 px-1.5 py-0.5 text-[11px] text-slate-400">
            {current.state}
            {current.unit ? ` ${current.unit}` : ""}
          </span>
        )}
      </div>
      {open && opts.length > 0 && (
        <ul className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-xl border border-white/10 bg-ink-800 py-1 shadow-2xl">
          <li>
            <button type="button" onMouseDown={() => { onChange(""); setQ(""); }} className="w-full px-3 py-1.5 text-left text-xs text-slate-500 hover:bg-white/5">
              (leeg laten)
            </button>
          </li>
          {opts.map((o) => (
            <li key={o.entity_id}>
              <button type="button" onMouseDown={() => { onChange(o.entity_id); setQ(o.entity_id); }}
                className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-white/5">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-slate-200">{o.name || o.entity_id}</span>
                  <span className="block truncate text-[11px] text-slate-500">{o.entity_id}</span>
                </span>
                <span className="shrink-0 text-[11px] text-slate-400">
                  {o.state}
                  {o.unit ? ` ${o.unit}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function fmtAge(s: number): string {
  const h = s / 3600;
  return h < 48 ? `${Math.round(h)} uur` : `${Math.round(h / 24)} dagen`;
}

function BatteryChoice({ f, value, onChange }: { f: SettingField; value: string; onChange: (v: string) => void }) {
  const opts = f.options ?? [];
  if (opts.length <= 1 && opts[0]?.soc === null) {
    return <p className="text-[11px] text-slate-500">Nog geen gegevens van de Victron GX (MQTT). Standaard: de actieve monitor.</p>;
  }
  return (
    <div className="space-y-1.5">
      {opts.map((o) => {
        const selected = value === o.source;
        return (
          <button key={o.source} type="button" onClick={() => onChange(o.source)}
            className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition ${
              selected ? "border-emerald-400/50 bg-emerald-400/[0.07]" : "border-white/10 bg-white/[0.03] hover:bg-white/5"
            }`}>
            <span className={`h-3.5 w-3.5 shrink-0 rounded-full border-2 ${selected ? "border-emerald-400 bg-emerald-400" : "border-slate-500"}`} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm text-slate-200">
                {o.name}
                {o.active && <span className="ml-2 rounded bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-300">actief in GX</span>}
              </span>
              <span className="block text-[11px] text-slate-500">{o.source === "system" ? "volgt de keuze in de GX" : o.source}</span>
            </span>
            <span className="shrink-0 text-right">
              <span className="block text-sm font-semibold tabular-nums">{o.soc !== null ? `${o.soc.toFixed(1)}%` : "–"}</span>
              {o.power_w !== null && <span className="block text-[11px] text-slate-500">{Math.round(o.power_w)} W</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}
