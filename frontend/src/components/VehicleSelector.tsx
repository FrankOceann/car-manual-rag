import type { Vehicle } from "../types";

type Props = { vehicles: Vehicle[]; value: string; onChange: (value: string) => void; disabled?: boolean };

export function VehicleSelector({ vehicles, value, onChange, disabled }: Props) {
  return <label className="field">车型<select aria-label="车型" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
    <option value="">请选择车型</option>
    {vehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.brand}{vehicle.model} {vehicle.year}</option>)}
  </select></label>;
}
