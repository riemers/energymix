import { useEffect, useRef, useState } from "react";
import { getSettings, saveSettings, searchEntities } from "./api";
import { COMPONENTS } from "./format";
import { Badge, Card } from "./ui";
import type { EntityOption, SettingField, Settings as SettingsT } from "./types";

export default function Settings({ onSaved }: { onSaved: () => void }) {
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
          {data.cars.map((c) => (
            <div key={c.name} className="rounded-xl bg-white/[0.03] p-3 text-xs">
              <div className="mb-1 text-sm font-medium text-slate-200">{c.name}</div>
              <div className="text-slate-400">vol: {c.max_range_km} km · {c.kwh_per_km} kWh/km</div>
              <div className="mt-1 truncate text-slate-500">{c.range_entity}</div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">Auto's pas je aan in de add-on-configuratie (<code>cars</code>).</p>
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
      {f.kind === "entity" ? (
        <EntityPicker value={String(value ?? "")} current={f} onChange={onChange} />
      ) : f.kind.startsWith("select:") ? (
        <select value={String(value)} onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none focus:border-emerald-400/50">
          {f.kind.slice(7).split(",").map((o) => (
            <option key={o} value={o} className="bg-ink-900">
              {o === "total" ? "Totaalprijs (salderen)" : o === "energy" ? "Kale energieprijs" : o}
            </option>
          ))}
        </select>
      ) : (
        <input type="number" step="any" value={String(value)} onChange={(e) => onChange(Number(e.target.value))}
          className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm tabular-nums outline-none focus:border-emerald-400/50" />
      )}
    </div>
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
