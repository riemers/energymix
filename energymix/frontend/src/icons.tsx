// Kleine set lijn-iconen (24x24, stroke), in de stijl van Lucide
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 20, ...rest }: P) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    ...rest,
  };
}

export const Sun = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);

export const Grid = (p: P) => (
  <svg {...base(p)}>
    <path d="M8 22 12 2l4 20M6 8h12M5 14h14M9.5 14 12 8l2.5 6M7.6 22h8.8" />
  </svg>
);

export const Home = (p: P) => (
  <svg {...base(p)}>
    <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" />
  </svg>
);

export const Battery = (p: P) => (
  <svg {...base(p)}>
    <rect x="3" y="7" width="16" height="10" rx="2" />
    <path d="M22 11v2" />
  </svg>
);

export const Car = (p: P) => (
  <svg {...base(p)}>
    <path d="M5 17h14M3 13l2-5.5A2 2 0 0 1 6.9 6h10.2a2 2 0 0 1 1.9 1.5L21 13v4a1 1 0 0 1-1 1h-1M3 13v4a1 1 0 0 0 1 1h1M3 13h18" />
    <circle cx="7" cy="17" r="2" />
    <circle cx="17" cy="17" r="2" />
  </svg>
);

export const Bolt = (p: P) => (
  <svg {...base(p)}>
    <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
  </svg>
);

export const Plug = (p: P) => (
  <svg {...base(p)}>
    <path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4" />
  </svg>
);

export const Refresh = (p: P) => (
  <svg {...base(p)}>
    <path d="M21 12a9 9 0 1 1-2.6-6.4L21 8M21 3v5h-5" />
  </svg>
);

export const Leaf = (p: P) => (
  <svg {...base(p)}>
    <path d="M11 20A7 7 0 0 1 4 13c0-6 7-10 16-10 0 9-4 16-9 17zM4 21c4-4 7-7 10-10" />
  </svg>
);

export const Moon = (p: P) => (
  <svg {...base(p)}>
    <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
  </svg>
);

export const Clock = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
);

export const ICONS: Record<string, (p: P) => React.JSX.Element> = {
  sun: Sun,
  grid: Grid,
  home: Home,
  battery: Battery,
  car: Car,
  bolt: Bolt,
  plug: Plug,
};
