export interface RiskPanelSignal {
  signal_id?: string;
  status?: string;
  risk?: number | null;
  missing_reason?: string | null;
  explanation?: string | null;
}
