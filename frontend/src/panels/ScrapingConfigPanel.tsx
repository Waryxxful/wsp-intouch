import { useEffect, useRef, useState } from 'react';
import { Card, Button, Input, FormField, Alert, Badge, Modal, LoadingState, EmptyState } from '@duralux/ui';
import { apiFetch } from '../api';
import { useApiList } from '../hooks/useApiList';

interface LastRun {
  estado: string;
  paginas_procesadas: number;
  error_detalle: string;
  started_at: string;
  finished_at: string | null;
}

interface ScrapingSource {
  id: number;
  url: string;
  nombre: string;
  frecuencia_horas: number;
  last_run: LastRun | null;
}

interface SourceDetail {
  last_run: LastRun;
  pages: { url: string; texto: string }[];
  paginas_con_error: { url: string; error: string }[];
  catalogo_extraido: { servicios: Record<string, unknown>[]; sucursales: Record<string, unknown>[] };
}

const PATH = '/cavem/api/admin/scraping-sources';
// Tope de caracteres a renderizar por pagina en el modal de Detalle -- una
// pagina real (ej. un adjunto que se cuele pese al filtro de content-type,
// o un caso legitimo de texto muy largo) puede tener cientos de miles o
// millones de caracteres; volcar eso entero en un <pre> congela el thread
// principal del navegador (bug real detectado en produccion con renault.cl).
const PAGINA_PREVIEW_CHARS = 2000;

function hostnameOf(url: string): string {
  try { return new URL(url).hostname; } catch { return url; }
}

function truncarTexto(texto: string): string {
  if (texto.length <= PAGINA_PREVIEW_CHARS) return texto;
  return `${texto.slice(0, PAGINA_PREVIEW_CHARS)}… (truncado, ${texto.length} caracteres en total)`;
}

export function ScrapingConfigPanel() {
  const { items, loading, saving, error, create, update, remove, reload } = useApiList<ScrapingSource>(PATH);
  const [url, setUrl] = useState('');
  const [nombre, setNombre] = useState('');
  const [frecuenciaHoras, setFrecuenciaHoras] = useState('0');

  const submit = async () => {
    const ok = await create({ url, nombre, frecuencia_horas: Number(frecuenciaHoras) || 0 });
    if (ok) { setUrl(''); setNombre(''); setFrecuenciaHoras('0'); }
  };

  if (loading) return <LoadingState />;

  return (
    <Card
      title="Fuentes de conocimiento (scraping)"
      subtitle="El bot usa el contenido de estos sitios para responder preguntas de catálogo"
    >
      <div className="row g-2 mb-3">
        <div className="col-md-5">
          <FormField label="URL del sitio a scrapear" hint="Solo un admin autenticado puede configurar esto — nunca se recibe por WhatsApp.">
            <Input
              type="url" startAddon={<i className="feather-globe" />} placeholder="https://ejemplo.cl"
              value={url} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setUrl(e.target.value)}
            />
          </FormField>
        </div>
        <div className="col-md-3">
          <FormField label="Nombre (opcional)" hint="Para identificarla en la lista, ej. 'Chery'.">
            <Input value={nombre} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNombre(e.target.value)} />
          </FormField>
        </div>
        <div className="col-md-2">
          <FormField label="Repetir cada (horas)" hint="0 desactiva la repetición automática.">
            <Input
              type="number" min={0} startAddon={<i className="feather-clock" />}
              value={frecuenciaHoras} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrecuenciaHoras(e.target.value)}
            />
          </FormField>
        </div>
        <div className="col-md-2 d-flex align-items-end">
          <Button variant="primary" loading={saving} disabled={!url} onClick={submit} icon="feather-plus">
            Agregar
          </Button>
        </div>
      </div>

      {error && <div className="mt-3"><Alert variant="danger" icon="feather-alert-triangle">{error}</Alert></div>}

      {items.length === 0 ? (
        <EmptyState title="Sin fuentes de scraping" message="Agregá la primera con el formulario de arriba." />
      ) : (
        <div className="d-flex flex-column gap-2">
          {items.map(source => (
            <SourceCard key={source.id} source={source} onUpdate={update} onRemove={remove} onReload={reload} />
          ))}
        </div>
      )}
    </Card>
  );
}

function SourceCard({
  source, onUpdate, onRemove, onReload,
}: {
  source: ScrapingSource;
  onUpdate: (id: number, body: unknown) => Promise<boolean>;
  onRemove: (id: number) => Promise<boolean>;
  onReload: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [frecuenciaHoras, setFrecuenciaHoras] = useState(String(source.frecuencia_horas));
  const [running, setRunning] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const mountedRef = useRef(true);
  // started_at cambia cada vez que corre un scrape nuevo (a diferencia de
  // last_run.estado, que puede volver a 'ok' y "verse igual" que la corrida
  // anterior). Se usa como señal para invalidar el detalle cacheado.
  const prevStartedAtRef = useRef(source.last_run?.started_at ?? null);

  useEffect(() => () => { mountedRef.current = false; }, []);
  useEffect(() => { setFrecuenciaHoras(String(source.frecuencia_horas)); }, [source.frecuencia_horas]);

  useEffect(() => {
    const startedAt = source.last_run?.started_at ?? null;
    if (prevStartedAtRef.current !== startedAt) {
      prevStartedAtRef.current = startedAt;
      setDetail(null);
      setErrorMsg('');
    }
  }, [source.last_run?.started_at]);

  const guardarFrecuencia = () =>
    onUpdate(source.id, { nombre: source.nombre, frecuencia_horas: Number(frecuenciaHoras) || 0 });

  const pollUntilDone = async () => {
    // Sin WebSocket/SSE (alcance de demo): polling simple cada 2s, SIN
    // tope de iteraciones -- un scrape completo puede tardar mas de 10
    // minutos con el crawler en max_pages=60 y la extraccion por chunks
    // (hasta 20 llamadas secuenciales al LLM), y un tope fijo dejaba la UI
    // congelada para siempre (mostrando "corriendo" sin volver a consultar)
    // si la corrida real duraba mas que el tope. mountedRef ya corta el
    // loop si el usuario navega fuera del panel, asi que no hace falta un
    // limite de iteraciones -- el propio backend es lo que acota cuanto
    // dura esto en la practica (timeouts por pagina, reintentos con tope
    // por llamada al LLM).
    for (;;) {
      if (!mountedRef.current) return;
      const fresh = await apiFetch<ScrapingSource[]>(PATH).catch(() => null);
      const mine = fresh?.find(s => s.id === source.id);
      if (mine?.last_run && mine.last_run.estado !== 'corriendo') break;
      await new Promise(r => setTimeout(r, 2000));
      if (!mountedRef.current) return;
    }
    await onReload();
  };

  const runNow = async () => {
    setRunning(true);
    try {
      await apiFetch(`${PATH}/${source.id}/run`, { method: 'POST' });
      if (mountedRef.current) setErrorMsg('');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      if (mountedRef.current) setErrorMsg(msg || 'No se pudo iniciar el scrape.');
    }
    await pollUntilDone();
    if (mountedRef.current) setRunning(false);
  };

  // Fetch compartido por el resumen inline (al expandir) y el modal de
  // detalle (al apretar "Detalle") — así no se pide dos veces lo mismo.
  // Un 404 con este mensaje puntual significa "todavía no corrió", que es
  // un estado esperado (se muestra como EmptyState, no como error); cualquier
  // otra falla es un error real y se muestra con errorMsg.
  const loadDetail = async () => {
    if (detail || detailLoading) return;
    setDetailLoading(true);
    try {
      const data = await apiFetch<SourceDetail>(`${PATH}/${source.id}/detail`);
      if (mountedRef.current) { setDetail(data); setErrorMsg(''); }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      if (mountedRef.current && msg !== 'esta fuente todavia no tiene corridas') {
        setErrorMsg('No se pudo cargar el detalle de esta fuente.');
      }
    } finally {
      if (mountedRef.current) setDetailLoading(false);
    }
  };

  useEffect(() => {
    if (expanded && source.last_run && !detail && !detailLoading) {
      void loadDetail();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  const verDetalle = () => {
    setDetailOpen(true);
    void loadDetail();
  };

  const estado = source.last_run?.estado ?? null;
  const corriendo = estado === 'corriendo' || running;

  return (
    <Card noPadding className="border">
      <div className="d-flex align-items-center gap-3 p-3">
        <button
          type="button" className="btn btn-sm btn-light-brand" onClick={() => setExpanded(e => !e)}
          aria-label={expanded ? 'Contraer' : 'Expandir'}
        >
          <i className={expanded ? 'feather-chevron-up' : 'feather-chevron-down'} />
        </button>
        <div className="flex-grow-1 me-2">
          <div className="fw-semibold">{source.nombre || hostnameOf(source.url)}</div>
          <div className="text-muted small">{source.url}</div>
        </div>
        {estado ? (
          <Badge variant={estado === 'ok' ? 'success' : estado === 'error' ? 'danger' : 'info'} soft>
            {corriendo ? 'corriendo' : estado}
          </Badge>
        ) : (
          <Badge variant="secondary" soft>sin corridas</Badge>
        )}
        {estado === 'ok' && <span className="text-muted small">{source.last_run!.paginas_procesadas} páginas</span>}
        <div className="d-flex align-items-center gap-1 text-muted small">
          <span>cada</span>
          <div style={{ width: 70 }}>
            <Input
              type="number" min={0} value={frecuenciaHoras}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrecuenciaHoras(e.target.value)}
              onBlur={guardarFrecuencia}
            />
          </div>
          <span>h</span>
        </div>
        <div className="d-flex align-items-center gap-2 ps-2 border-start">
          <Button size="sm" loading={corriendo} disabled={corriendo} onClick={runNow} icon="feather-refresh-cw">
            {corriendo ? 'Actualizando' : 'Actualizar'}
          </Button>
          <Button size="sm" variant="light-brand" onClick={verDetalle} icon="feather-eye">Detalle</Button>
          <Button
            size="sm" variant="danger" icon="feather-trash-2"
            onClick={() => { if (window.confirm(`¿Borrar la fuente ${source.nombre || source.url}?`)) onRemove(source.id); }}
          />
        </div>
      </div>

      {errorMsg && (
        <div className="px-3 pb-2">
          <Alert variant="danger" icon="feather-alert-triangle" className="mb-0">{errorMsg}</Alert>
        </div>
      )}

      {expanded && (
        <div className="px-3 pb-3">
          {estado === 'error' && (
            <Alert variant="danger" icon="feather-alert-triangle">{source.last_run!.error_detalle}</Alert>
          )}
          {!source.last_run && <div className="text-muted small">Todavía no corrió ningún scrape para esta fuente.</div>}
          {source.last_run && detailLoading && <LoadingState message="Cargando resumen..." />}
          {source.last_run && !detailLoading && detail && (
            <div className="small">
              {detail.paginas_con_error.length > 0 && (
                <div className="text-danger mb-1">{detail.paginas_con_error.length} página(s) fallaron.</div>
              )}
              <div className="fw-semibold mb-1">Páginas scrapeadas ({detail.pages.length}):</div>
              {detail.pages.map(page => (
                <div key={page.url} className="text-muted">{page.url}</div>
              ))}
            </div>
          )}
        </div>
      )}

      <Modal
        open={detailOpen} onClose={() => setDetailOpen(false)}
        title={`Detalle — ${source.nombre || source.url}`} size="lg" scrollable
      >
        {detailLoading && <LoadingState />}
        {!detailLoading && detail && (
          <>
            {detail.paginas_con_error.length > 0 && (
              <Alert variant="warning" icon="feather-alert-triangle" className="mb-3">
                {detail.paginas_con_error.length} página(s) fallaron: {detail.paginas_con_error.map(p => p.url).join(', ')}
              </Alert>
            )}
            <h6>Catálogo extraído</h6>
            <pre className="small bg-light p-2 rounded">{JSON.stringify(detail.catalogo_extraido, null, 2)}</pre>
            <h6 className="mt-3">Páginas ({detail.pages.length})</h6>
            {detail.pages.map(page => (
              <div key={page.url} className="mb-3">
                <div className="fw-semibold small">{page.url}</div>
                <pre className="small bg-light p-2 rounded" style={{ maxHeight: 200, overflow: 'auto' }}>{truncarTexto(page.texto)}</pre>
              </div>
            ))}
          </>
        )}
        {!detailLoading && !detail && errorMsg && (
          <Alert variant="danger" icon="feather-alert-triangle">{errorMsg}</Alert>
        )}
        {!detailLoading && !detail && !errorMsg && (
          <EmptyState title="Sin detalle" message="Esta fuente todavía no tiene una corrida." />
        )}
      </Modal>
    </Card>
  );
}
