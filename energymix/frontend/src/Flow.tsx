import { Battery, Car, Grid, Home, Sun } from "./icons";
import { watt } from "./format";
import type { Live } from "./types";

const W = 480;
const H = 400;
const HUB = { x: 240, y: 180 };
const R = 32;

interface Node {
  key: string;
  x: number;
  y: number;
  color: string;
}

const NODES: Record<string, Node> = {
  sun: { key: "sun", x: 240, y: 44, color: "var(--color-sun)" },
  grid: { key: "grid", x: 64, y: 180, color: "var(--color-grid)" },
  battery: { key: "battery", x: 240, y: 322, color: "var(--color-batt)" },
  car0: { key: "car0", x: 416, y: 118, color: "var(--color-car)" },
  car1: { key: "car1", x: 416, y: 242, color: "var(--color-car)" },
};

function edgePath(n: Node): string {
  // Zachte curve van node naar het huis in het midden
  const mx = (n.x + HUB.x) / 2;
  const my = (n.y + HUB.y) / 2;
  const cx = n.x === HUB.x ? mx : mx + (n.y - HUB.y) * 0.15;
  const cy = n.x === HUB.x ? my : my - (n.x - HUB.x) * 0.05;
  return `M ${n.x} ${n.y} Q ${cx} ${cy} ${HUB.x} ${HUB.y}`;
}

function Edge({ node, power, toHub }: { node: Node; power: number; toHub: boolean }) {
  const active = power > 30;
  const dur = Math.max(0.35, 2.4 - Math.log10(Math.max(power, 1)) * 0.55);
  const width = 1.5 + Math.min(4.5, power / 2200);
  const d = edgePath(node);
  return (
    <g>
      <path d={d} fill="none" stroke="white" strokeOpacity={0.07} strokeWidth={2} />
      {active && (
        <path
          d={d}
          fill="none"
          stroke={node.color}
          strokeWidth={width}
          strokeLinecap="round"
          className="flow-line"
          style={{ animationDuration: `${dur}s`, animationDirection: toHub ? "normal" : "reverse", filter: `drop-shadow(0 0 4px ${node.color})` }}
        />
      )}
    </g>
  );
}

function Bubble({ node, icon, label, value, sub, ring, dim, side }: {
  node: Node; icon: React.ReactNode; label: string; value: string; sub?: string; ring?: number | null; dim?: boolean; side?: boolean;
}) {
  const hasRing = ring !== undefined && ring !== null;
  const lx = side ? node.x + R + 12 : node.x;
  const ly = side ? node.y - 2 : node.y + R + (hasRing ? 22 : 17);
  const anchor = side ? "start" : "middle";
  const circ = 2 * Math.PI * (R + 5);
  return (
    <g opacity={dim ? 0.45 : 1}>
      <circle cx={node.x} cy={node.y} r={R} fill="var(--color-ink-800)" stroke={node.color} strokeOpacity={0.5} strokeWidth={1.5} />
      {ring !== undefined && ring !== null && (
        <>
          <circle cx={node.x} cy={node.y} r={R + 5} fill="none" stroke="white" strokeOpacity={0.06} strokeWidth={4} />
          <circle
            cx={node.x}
            cy={node.y}
            r={R + 5}
            fill="none"
            stroke={node.color}
            strokeWidth={4}
            strokeLinecap="round"
            strokeDasharray={`${(circ * Math.max(0, Math.min(100, ring))) / 100} ${circ}`}
            transform={`rotate(-90 ${node.x} ${node.y})`}
          />
        </>
      )}
      <g color={node.color}>{icon}</g>
      <text x={lx} y={ly} textAnchor={anchor} className="fill-slate-100 text-[13px] font-semibold">
        {value}
      </text>
      <text x={lx} y={ly + 14} textAnchor={anchor} className="fill-slate-500 text-[10.5px]">
        {sub ?? label}
      </text>
    </g>
  );
}

const ic = (Icon: typeof Sun, n: Node) => <Icon size={26} x={n.x - 13} y={n.y - 13} />;

export default function Flow({ live }: { live: Live }) {
  const pv = Math.max(0, live.pv_w ?? 0);
  const grid = live.grid_w ?? 0;
  const batt = live.battery_w ?? 0;
  const zappi = Math.max(0, live.zappi_w ?? 0);
  const house = live.house_w;
  const cars = live.cars.slice(0, 2);
  // De auto die aan de Zappi hangt krijgt de stroom
  const activeIdx = cars.findIndex((c) => c.connected && c.home);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mx-auto block h-auto w-full max-w-[620px]">
      <Edge node={NODES.sun} power={pv} toHub />
      <Edge node={NODES.grid} power={Math.abs(grid)} toHub={grid > 0} />
      <Edge node={NODES.battery} power={Math.abs(batt)} toHub={batt < 0} />
      {cars.map((_, i) => (
        <Edge key={i} node={NODES[`car${i}`]} power={i === activeIdx ? zappi : 0} toHub={false} />
      ))}

      {/* Huis in het midden */}
      <circle cx={HUB.x} cy={HUB.y} r={R + 8} fill="var(--color-ink-800)" stroke="var(--color-house)" strokeOpacity={0.6} strokeWidth={1.5} />
      <circle cx={HUB.x} cy={HUB.y} r={R + 16} fill="none" stroke="var(--color-house)" strokeOpacity={0.08} strokeWidth={8} />
      <g color="var(--color-house)">
        <Home size={30} x={HUB.x - 15} y={HUB.y - 22} />
      </g>
      <text x={HUB.x} y={HUB.y + 22} textAnchor="middle" className="fill-slate-100 text-[12px] font-semibold">
        {watt(house)}
      </text>

      <Bubble node={NODES.sun} icon={ic(Sun, NODES.sun)} label="zon" value={watt(pv)} sub="zonnepanelen" dim={pv < 30} side />
      <Bubble
        node={NODES.grid}
        icon={ic(Grid, NODES.grid)}
        label="net"
        value={watt(grid)}
        sub={grid > 30 ? "van het net" : grid < -30 ? "naar het net" : "net"}
      />
      <Bubble
        node={NODES.battery}
        icon={ic(Battery, NODES.battery)}
        label="accu"
        value={live.soc !== null ? `${Math.round(live.soc)}%` : "–"}
        sub={batt > 30 ? `laadt ${watt(batt)}` : batt < -30 ? `levert ${watt(batt)}` : "accu"}
        ring={live.soc}
      />
      {cars.map((c, i) => {
        const pct = c.range_km !== null ? (c.range_km / c.max_range_km) * 100 : null;
        const charging = i === activeIdx && zappi > 30;
        return (
          <Bubble
            key={c.name}
            node={NODES[`car${i}`]}
            icon={ic(Car, NODES[`car${i}`])}
            label={c.name}
            value={c.range_km !== null ? `${Math.round(c.range_km)} km` : "–"}
            sub={charging ? `${c.name} · ${watt(zappi)}` : c.connected ? `${c.name} · aan lader` : c.home ? `${c.name} · thuis` : `${c.name} · weg`}
            ring={pct}
            dim={!c.home}
          />
        );
      })}
    </svg>
  );
}
