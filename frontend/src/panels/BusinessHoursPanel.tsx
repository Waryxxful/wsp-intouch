import { useEffect, useState } from 'react';
import { Card, Button, Alert, Checkbox, LoadingState } from '@duralux/ui';
import { useApiResource } from '../hooks/useApiResource';

interface Dia { dia_semana: number; hora_inicio: string; hora_fin: string; activo: boolean; }
interface BusinessHoursData { dias: Dia[]; }

const DIA_LABELS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

export function BusinessHoursPanel() {
  const { data, loading, saving, saved, save } = useApiResource<BusinessHoursData>('/cavem/api/admin/business-hours');
  const [dias, setDias] = useState<Dia[]>([]);

  useEffect(() => { if (data) setDias(data.dias); }, [data]);

  const updateDia = (dia_semana: number, patch: Partial<Dia>) => {
    setDias(current => current.map(d => d.dia_semana === dia_semana ? { ...d, ...patch } : d));
  };

  if (loading) return <LoadingState />;

  return (
    <Card title="Horario de atención">
      <div className="d-flex flex-column gap-2">
        {dias.map(d => (
          <div key={d.dia_semana} className="d-flex align-items-center gap-3">
            <div style={{ width: 110 }}>
              <Checkbox
                label={DIA_LABELS[d.dia_semana]}
                checked={d.activo}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateDia(d.dia_semana, { activo: e.target.checked })}
              />
            </div>
            <input
              type="time" className="form-control form-control-sm" style={{ width: 120 }}
              value={d.hora_inicio} disabled={!d.activo}
              onChange={e => updateDia(d.dia_semana, { hora_inicio: e.target.value })}
            />
            <span className="fs-12">a</span>
            <input
              type="time" className="form-control form-control-sm" style={{ width: 120 }}
              value={d.hora_fin} disabled={!d.activo}
              onChange={e => updateDia(d.dia_semana, { hora_fin: e.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="mt-3">
        <Button variant="primary" loading={saving} onClick={() => save({ dias })} icon="feather-save">
          Guardar horario
        </Button>
      </div>
      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">Horario guardado.</Alert></div>}
    </Card>
  );
}
