import type { Live } from "./types";

// Stroom per fase t.o.v. de hoofdzekering. De fase(s) van de Victron zijn gemarkeerd.
export default function Phases({ live }: { live: Live }) {
  const max = live.phase_max_a || 25;
  if (!live.phase_a.length) {
    return (
      <p className="text-[11px] text-slate-500">
        Geen fasemeting ingesteld: de regelaar neemt aan dat het verbruik gelijk over de fases is verdeeld. Kies de
        sensoren per fase onder Instellingen → Net.
      </p>
    );
  }
  return (
    <div className="grid grid-cols-3 gap-3">
      {live.phase_a.map((a, i) => {
        const pct = a === null ? 0 : Math.max(0, Math.min(100, (a / max) * 100));
        const victron = live.victron_phases.includes(i + 1);
        const free = live.regulator.phase_free_a[i];
        const tone = pct > 90 ? "#f87171" : pct > 70 ? "#fbbf24" : "#34d399";
        return (
          <div key={i}>
            <div className="mb-1 flex items-baseline justify-between text-[11px]">
              <span className="font-medium text-slate-300">
                L{i + 1}
                {victron && <span className="ml-1 rounded bg-emerald-400/10 px-1 text-[9px] text-emerald-300">victron</span>}
              </span>
              <span className="tabular-nums text-slate-400">
                {a === null ? "–" : `${a.toFixed(1)} A`}
                <span className="text-slate-600"> / {max}</span>
              </span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full bg-white/5">
              <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: tone }} />
              <div className="absolute inset-y-0 w-px bg-white/30" style={{ left: `${((max - 2) / max) * 100}%` }} />
            </div>
            {free !== undefined && <div className="mt-0.5 text-[10px] text-slate-500">{free.toFixed(1)} A vrij</div>}
          </div>
        );
      })}
    </div>
  );
}
