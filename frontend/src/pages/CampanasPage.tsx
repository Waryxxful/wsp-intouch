import { useEffect, useState } from 'react';
import { Card, PageHeader, Badge, Button, Modal, Textarea, Alert, LoadingState, StatsCard } from '@duralux/ui';
import { apiFetch } from '../api';

interface Campana {
  id: number;
  nombre: string;
  campaign_type: string;
  template: string;
  segmento: string;
  objetivo: string;
  mensaje: string;
  palabra_clave: string;
  activa: boolean;
  contactos: number;
  enviados: number;
  entregados: number | null;
  leidos: number | null;
  respuestas: number;
  leads: number;
  conversiones: number;
}

// Entregados y leidos los sabe Meta, no nosotros: llegan en null desde el
// backend y la celda muestra una raya. Rellenarlos con un numero inventado es
// exactamente lo que el guardrail del docx S15 prohibe, y en una demo
// comercial es peor: alguien lo va a citar como si fuera real.
function metrica(valor: number | null): string {
  return valor === null || valor === undefined ? '—' : valor.toLocaleString('es-CL');
}

const COLUMNAS_EMBUDO: { key: keyof Campana; label: string }[] = [
  { key: 'contactos', label: 'Contactos' },
  { key: 'enviados', label: 'Enviados' },
  { key: 'entregados', label: 'Entregados' },
  { key: 'leidos', label: 'Leídos' },
  { key: 'respuestas', label: 'Respuestas' },
  { key: 'leads', label: 'Leads' },
  { key: 'conversiones', label: 'Conversiones' },
];

export function CampanasPage() {
  const [campanas, setCampanas] = useState<Campana[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [enviando, setEnviando] = useState<Campana | null>(null);
  const [csv, setCsv] = useState('wa_id\n');
  const [resultado, setResultado] = useState<string>('');
  const [errorEnvio, setErrorEnvio] = useState('');
  const [enProgreso, setEnProgreso] = useState(false);
  const [modoDemo, setModoDemo] = useState(false);

  const cargar = () => {
    const params = new URLSearchParams();
    if (modoDemo) params.set('modo', 'demo');
    setLoading(true);
    apiFetch<Campana[]>(`/intouch/api/admin/campanas?${params}`)
      .then(setCampanas)
      .catch(err => setError(err.message || 'No se pudieron cargar las campañas.'))
      .finally(() => setLoading(false));
  };

  useEffect(cargar, [modoDemo]);

  const enviar = async () => {
    if (!enviando) return;
    setEnProgreso(true);
    setErrorEnvio('');
    setResultado('');
    try {
      const r = await apiFetch<{ enviados: number; optout_saltados: number; errores: string[] }>(
        '/intouch/api/admin/campanas/enviar',
        { method: 'POST', body: JSON.stringify({ campaign_type: enviando.campaign_type, csv_text: csv }) },
      );
      const partes = [`${r.enviados} enviados`];
      if (r.optout_saltados) partes.push(`${r.optout_saltados} saltados por opt-out`);
      if (r.errores.length) partes.push(`${r.errores.length} con problemas: ${r.errores.join('; ')}`);
      setResultado(partes.join(' · '));
      cargar();
    } catch (err) {
      setErrorEnvio((err as Error).message || 'El envío falló.');
    } finally {
      setEnProgreso(false);
    }
  };

  const totales = campanas.reduce(
    (acc, c) => ({
      enviados: acc.enviados + c.enviados,
      respuestas: acc.respuestas + c.respuestas,
      leads: acc.leads + c.leads,
    }),
    { enviados: 0, respuestas: 0, leads: 0 },
  );

  return (
    <>
      {modoDemo && (
        <Alert variant="warning" icon="feather-alert-triangle" title="Vista de proyección">
          Estas cifras son de demostración, no mediciones reales de este bot.
          Desactivá “Proyección demo” para ver los datos efectivos.
        </Alert>
      )}
      <PageHeader title="Campañas" breadcrumbs={[{ label: 'Asesor Comercial IA', href: '.' }, { label: 'Campañas' }]}>
        <div className="form-check form-switch me-3">
          <input
            className="form-check-input"
            type="checkbox"
            role="switch"
            id="toggle-proyeccion"
            checked={modoDemo}
            onChange={(e) => setModoDemo(e.target.checked)}
          />
          <label className="form-check-label" htmlFor="toggle-proyeccion">
            Proyección demo
          </label>
        </div>
      </PageHeader>
      <div className="main-content">
        {error && <Alert variant="danger" icon="feather-alert-circle">{error}</Alert>}
        {loading ? (
          <LoadingState />
        ) : (
          <div className="row g-4">
            <div className="col-md-4">
              <StatsCard icon="feather-send" iconBg="bg-soft-primary"
                value={totales.enviados.toLocaleString('es-CL')} label="Mensajes enviados" />
            </div>
            <div className="col-md-4">
              <StatsCard icon="feather-message-circle" iconBg="bg-soft-info"
                value={totales.respuestas.toLocaleString('es-CL')} label="Respuestas" />
            </div>
            <div className="col-md-4">
              <StatsCard icon="feather-user-check" iconBg="bg-soft-success"
                value={totales.leads.toLocaleString('es-CL')} label="Leads generados" />
            </div>

            {campanas.map(c => (
              <div className="col-12" key={c.id}>
                <Card
                  title={c.nombre}
                  subtitle={c.segmento}
                  actions={
                    <div className="d-flex gap-2 align-items-center">
                      <Badge variant={c.activa ? 'success' : 'secondary'} soft pill>
                        {c.activa ? 'Activa' : 'Inactiva'}
                      </Badge>
                      {c.palabra_clave && <Badge variant="primary" soft pill>{c.palabra_clave}</Badge>}
                      <Button
                        variant="primary"
                        size="sm"
                        icon="feather-send"
                        disabled={!c.activa || !c.template || modoDemo}
                        title={modoDemo ? 'No disponible en proyección' : ''}
                        onClick={() => { setEnviando(c); setCsv('wa_id\n'); setResultado(''); setErrorEnvio(''); }}
                      >
                        Enviar
                      </Button>
                    </div>
                  }
                >
                  {!c.template && (
                    <Alert variant="warning" icon="feather-alert-triangle">
                      Sin plantilla de Meta configurada — no se puede enviar hasta que esté aprobada.
                    </Alert>
                  )}
                  <p className="text-muted">{c.mensaje}</p>
                  <div className="row text-center g-3">
                    {COLUMNAS_EMBUDO.map(col => (
                      <div className="col" key={col.key}>
                        <div className="fs-4 fw-semibold">{metrica(c[col.key] as number | null)}</div>
                        <small className="text-muted">{col.label}</small>
                      </div>
                    ))}
                  </div>
                  <small className="text-muted d-block mt-3">
                    Entregados y leídos los reporta Meta y se consultan aparte; una raya significa
                    que todavía no hay dato, no cero.
                  </small>
                </Card>
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal
        open={enviando !== null}
        onClose={() => setEnviando(null)}
        title={`Enviar "${enviando?.nombre ?? ''}"`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEnviando(null)}>Cerrar</Button>
            <Button variant="primary" loading={enProgreso} onClick={enviar}>Enviar ahora</Button>
          </>
        }
      >
        <p className="text-muted">
          Pegá los números en formato CSV, con una columna <code>wa_id</code> y un número por fila
          (formato internacional sin “+”). Máximo 200 por envío. Los contactos que pidieron no ser
          contactados se saltan solos.
        </p>
        <Textarea rows={8} value={csv}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setCsv(e.target.value)} />
        {resultado && <Alert variant="success" icon="feather-check-circle">{resultado}</Alert>}
        {errorEnvio && <Alert variant="danger" icon="feather-alert-circle">{errorEnvio}</Alert>}
      </Modal>
    </>
  );
}
