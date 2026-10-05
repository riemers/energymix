import type { ReactNode } from "react";

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center gap-2">
          {title && <h2 className="text-[13px] font-medium tracking-wide text-slate-400">{title}</h2>}
          {right && <div className="ml-auto">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Switch({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-40 ${
        checked ? "bg-emerald-500" : "bg-white/10"
      }`}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${checked ? "left-[22px]" : "left-0.5"}`}
      />
    </button>
  );
}

export function Slider({
  value, min, max, step, unit, onCommit, accent = "#34d399",
}: { value: number; min: number; max: number; step: number; unit: string; onCommit: (v: number) => void; accent?: string }) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        defaultValue={value}
        key={value}
        onPointerUp={(e) => onCommit(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => onCommit(Number((e.target as HTMLInputElement).value))}
        onInput={(e) => {
          const el = e.target as HTMLInputElement;
          const out = el.parentElement?.querySelector("output");
          if (out) out.textContent = `${el.value}${unit}`;
          el.style.setProperty("--pct", `${((Number(el.value) - min) / (max - min)) * 100}%`);
        }}
        className="range w-full"
        style={{ ["--pct" as string]: `${pct}%`, ["--accent" as string]: accent }}
      />
      <output className="w-14 text-right text-sm tabular-nums text-slate-200">
        {value}
        {unit}
      </output>
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-lg bg-white/5 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition ${
            value === o.value ? "bg-white/10 text-white shadow" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "amber" | "pink" | "sky" | "red" }) {
  const tones: Record<string, string> = {
    slate: "bg-white/5 text-slate-300 border-white/10",
    green: "bg-emerald-400/10 text-emerald-300 border-emerald-400/20",
    amber: "bg-amber-400/10 text-amber-300 border-amber-400/25",
    pink: "bg-pink-400/10 text-pink-300 border-pink-400/20",
    sky: "bg-sky-400/10 text-sky-300 border-sky-400/20",
    red: "bg-red-400/10 text-red-300 border-red-400/20",
  };
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>;
}
