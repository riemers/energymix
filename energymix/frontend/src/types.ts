export type Reasons = Partial<Record<"pv" | "ess" | "dvcc" | "zappi" | "feed_in" | "setpoint", string>>;

export interface SlotPlan {
  start: string;
  end: string;
  price: number;
  sell_price: number;
  level: string | null;
  pv_on: boolean | null;
  ess_state: number | null;
  dvcc_current: number | null;
  setpoint_w: number | null;
  zappi_mode: string | null;
  feed_in_disabled: number | null;
  soc: number | null;
  pv_kwh: number;
  house_kwh: number;
  car_kwh: number;
  grid_charge_kwh: number;
  export_kwh: number;
  import_kwh: number;
  car_range_km: number | null;
  reasons: Reasons;
}

export interface CarSession {
  mode: "Fast" | "Eco";
  kinds: string[];
  start: string;
  end: string;
  kwh: number;
  cost: number;
  avg_price: number | null;
  range_start_km: number | null;
  range_end_km: number | null;
  reason: string;
}

export interface BatterySession {
  kind: "charge" | "hold" | "export";
  start: string;
  end: string;
  kwh: number;
  soc_end: number | null;
  reason: string;
}

export interface CarPlan {
  name: string | null;
  range_km: number | null;
  need_min_kwh: number;
  need_full_kwh: number;
  planned_kwh: number;
  deadline: string | null;
  full_at: string | null;
  max_range_km: number | null;
  kwh_per_km: number;
  need_km: number;
  need_minutes: number;
  eco_km: number;
  eco_reason: string;
  boost: boolean;
  max_source: string;
  charge_limit: number | null;
  speed_kmh: number | null;
  speed_source: string;
  time_source: string;
  window_mode: string;
  window_options: { kind: "night" | "day"; start: string; end: string; avg_price: number }[];
}

export interface Season {
  detected: "day" | "night" | "neutral" | "unknown";
  effective: "day" | "night" | "neutral" | "unknown";
  mode: "auto" | "day" | "night";
  day_avg: number | null;
  night_avg: number | null;
}

export interface Summary {
  cost_eur?: number;
  baseline_cost_eur?: number;
  saving_eur?: number;
  grid_charge_kwh?: number;
  export_kwh?: number;
  soc_end?: number;
  soc_min?: number;
  soc_min_at?: string;
  target_reached_at?: string | null;
  hold_slots?: number;
  empty_at?: string | null;
  empty_why?: string;
  house_kwh_24h?: number;
  pv_kwh_24h?: number;
  hold_value_eur?: number;
  hold_windows?: { start: string; end: string }[];
  runway?: { expected?: string | null; early?: string | null; late?: string | null; basis_until?: string };
}

export interface Plan {
  created_at: string;
  car: CarPlan;
  active_car: string | null;
  charge_window: [string, string] | null;
  season: Season;
  summary: Summary;
  notes: string[];
  car_sessions: CarSession[];
  battery_sessions: BatterySession[];
  slots: SlotPlan[];
}

export interface LiveCar {
  name: string;
  range_km: number | null;
  max_range_km: number;
  configured_max_km: number;
  learned_max_km: number | null;
  learned_speed_kmh: number | null;
  soc: number | null;
  charge_limit: number | null;
  time_to_full_min: number | null;
  charge_rate_kmh: number | null;
  connected: boolean;
  home: boolean;
  entities: Record<string, string>;
}

export interface Live {
  ts: string;
  soc: number | null;
  pv_w: number | null;
  grid_w: number | null;
  battery_w: number | null;
  house_w: number | null;
  zappi_w: number | null;
  zappi_mode: string;
  zappi_status: string;
  zappi_plug: string;
  cars: LiveCar[];
  price: number | null;
  phase_a: (number | null)[];
  phase_max_a: number;
  victron_phases: number[];
  sources: Record<string, string>;
  victron_connected: boolean;
  regulator: {
    current_a: number | null;
    target_a: number | null;
    headroom_w: number | null;
    car_throttled: boolean;
    phase_free_a: number[];
    basis: string;
    reason: string;
  };
}

export interface Action {
  component: string;
  desired: string | number;
  actual: string | number | null;
  reason: string;
  live: boolean;
  executed: boolean;
  error: string | null;
}

export interface HelperInfo {
  key: string;
  entity_id: string;
  label: string;
  exists: boolean;
  value: boolean | number | string | null;
  min: number | null;
  max: number | null;
  step: number | null;
}

export interface Status {
  shadow: boolean;
  master: boolean;
  control: Record<string, boolean>;
  timezone: string;
  cheap_price: number;
  force_fast_price: number;
  battery_capacity_kwh: number;
  battery_target_soc: number;
  battery_reserve_soc: number;
  ha_connected: boolean;
  errors: string[];
  story: string[];
  helpers: HelperInfo[];
  plan: Plan | null;
  actions: Action[];
  live: Live;
  today: Today;
  state: { vannacht: boolean } | null;
}

export interface Today {
  house: number;
  pv: number;
  car: number;
  battery_in: number;
  battery_out: number;
  battery_net: number;
  grid_in: number;
  grid_out: number;
  samples: number;
}

export interface Decision {
  id: number;
  ts: string;
  component: string;
  value: string;
  previous: string;
  reason: string;
  executed: number;
}

export interface StatsDay {
  date: string;
  soc_min: number | null;
  soc_max: number | null;
  charged_kwh: number;
  discharged_kwh: number;
}

export interface Stats {
  days: StatsDay[];
  lowest_soc: number | null;
  median_daily_min_soc: number | null;
  learned_charge_w: number | null;
  charge_w_used: number;
  charge_w_source: string;
  to_target_kwh?: number;
  to_target_hours?: number | null;
  samples: number;
}

export interface SettingField {
  key: string;
  label: string;
  kind: string;
  group: string;
  value: string | number | boolean;
  state: string | null;
  unit: string | null;
  suggestion: { entity_id: string; state: string; unit: string | null } | null;
  fallback: { label: string; active: boolean } | null;
  age_s: number | null;
  options: { source: string; name: string; soc: number | null; power_w: number | null; active: boolean }[] | null;
}

export interface Settings {
  fields: SettingField[];
  control: Record<string, boolean>;
  cars: { name: string; max_range_km: number; cable_entity: string; location_entity: string; range_entity: string; kwh_per_km: number }[];
}

export interface EntityOption {
  entity_id: string;
  name: string;
  state: string;
  unit: string | null;
}
