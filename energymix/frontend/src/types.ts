export type Reasons = Partial<Record<"pv" | "ess" | "dvcc" | "zappi" | "feed_in" | "setpoint", string>>;

export interface SlotPlan {
  start: string;
  end: string;
  price: number;
  sell_price: number;
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
  reasons: Reasons;
}

export interface CarPlan {
  name: string | null;
  range_km: number | null;
  need_min_kwh: number;
  need_full_kwh: number;
  planned_kwh: number;
  deadline: string | null;
  full_at: string | null;
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
}

export interface Plan {
  created_at: string;
  car: CarPlan;
  active_car: string | null;
  charge_window: [string, string] | null;
  season: Season;
  summary: Summary;
  notes: string[];
  slots: SlotPlan[];
}

export interface LiveCar {
  name: string;
  range_km: number | null;
  max_range_km: number;
  connected: boolean;
  home: boolean;
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
