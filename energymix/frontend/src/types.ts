export type Reasons = Partial<Record<"pv" | "ess" | "dvcc" | "zappi" | "feed_in", string>>;

export interface SlotPlan {
  start: string;
  end: string;
  price: number;
  pv_on: boolean | null;
  ess_state: number | null;
  dvcc_current: number | null;
  zappi_mode: string | null;
  feed_in_disabled: number | null;
  reasons: Reasons;
}

export interface Plan {
  created_at: string;
  active_car: string | null;
  charge_minutes: number | null;
  charge_window: [string, string] | null;
  notes: string[];
  slots: SlotPlan[];
}

export interface CarState {
  name: string;
  max_range_km: number;
  cable: string;
  location: string;
  range_km: string;
}

export interface State {
  soc: number | null;
  solar_today_kwh: number | null;
  sunchance: number | null;
  carcharger_mode: string;
  vannacht: boolean;
  zappi_plug: string;
  zappi_status: string;
  zappi_mode: string;
  cars: CarState[];
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

export interface Status {
  shadow: boolean;
  control: Record<string, boolean>;
  timezone: string;
  cheap_price: number;
  force_fast_price: number;
  ha_connected: boolean;
  errors: string[];
  state: State | null;
  plan: Plan | null;
  actions: Action[];
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
