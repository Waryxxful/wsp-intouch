import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ChatBubble, ChatInputBar, ChatSidebar, Avatar, Button, Badge, Alert, PageHeader } from '@duralux/ui';
import { apiFetch } from '../api';
import { IncidentsPanel } from '../panels/IncidentsPanel';
import {
  clampMaxRange, conversationTarget, parseChatFilters, parseConversationId,
  whenCurrent, writeChatFilters, type ChatFilters,
} from '../chat/chatState';
import { useBreadcrumbLink } from '../nav/useBreadcrumbLink';

interface Conversation {
  id: number;
  wa_id: string;
  name: string;
  active_agent: string;
  updated_at: string;
  human_mode?: boolean;
  archived?: boolean;
  open_incidents?: number;
  // docx S18: la tabla de conversaciones muestra intencion, resumen IA y lead
  // score. Vienen de LeadComercial, no de Conversation.
  stage?: string;
  intencion?: string;
  resumen?: string;
  lead_score?: number | null;
  temperatura?: string;
}

interface Message {
  id: number;
  role: string;
  content: string;
  created_at: string;
  media_url?: string | null;
  media_type?: string | null;
}

interface MessagesPage {
  items: Message[];
  has_more: boolean;
}

const GENERIC_AVATAR =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#adb5bd">' +
    '<circle cx="12" cy="8" r="4"/><path d="M4 20c0-4.4 3.6-8 8-8s8 3.6 8 8v1H4v-1z"/>' +
    '</svg>'
  );

function formatMessageTime(iso: string): string {
  const d = new Date(iso);
  const day = String(d.getDate()).padStart(2, '0');
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const hour = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${day}/${month} ${hour}:${min}`;
}

export function ChatOperatorPage({ basename }: { basename: string }) {
  // Breadcrumb a la raíz del remoto sin recargar el shell (ver useBreadcrumbLink).
  const inicio = useBreadcrumbLink(basename);
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // La URL es la única fuente de verdad de qué conversación está abierta: el
  // id se deriva del parámetro en cada render, sin copiarlo a un estado que
  // quede un render atrasado respecto de la URL.
  const activeId = parseConversationId(conversationId);
  // Destino absoluto calculado en chatState. Un clic sobre el chat que ya está
  // abierto no navega: apilaría una entrada duplicada en el historial.
  const goToConversation = (id: number) => {
    const target = conversationTarget(location, activeId, id);
    if (target) navigate(target);
  };
  const [convs, setConvs] = useState<Conversation[]>([]);
  // Fallback cuando la conv activa no esta en `convs` (el filtro estado la
  // dejo afuera, ej. el operador cambia a "Cerradas" con un chat abierto en
  // "Abiertas") — se llena con un fetch puntual al detalle por id.
  const [directConv, setDirectConv] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasMoreOlder, setHasMoreOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [togglingMode, setTogglingMode] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [showIncidents, setShowIncidents] = useState(false);
  const [count, setCount] = useState(0);
  const [search, setSearch] = useState('');
  // Filtro de la lista (estado + rango de fechas sobre updated_at) en el query
  // string, no en estado local: recargar, volver atrás o abrir el enlace
  // muestra la misma lista. Default = abiertas de la semana actual (lunes a
  // domingo). Máximo 62 días entre desde y hasta. Se escribe con `replace`:
  // cambiar un filtro no agrega entradas al historial.
  const [searchParams, setSearchParams] = useSearchParams();
  const { estado, desde: dateDesde, hasta: dateHasta } = parseChatFilters(searchParams);
  // Un solo setSearchParams por evento: el `prev` de react-router no se
  // encola como el de useState, dos llamadas seguidas se pisarían.
  const updateFilters = (patch: Partial<ChatFilters>) =>
    setSearchParams(prev => writeChatFilters(prev, { ...parseChatFilters(prev), ...patch }), { replace: true });
  const [rangeClampWarning, setRangeClampWarning] = useState('');
  // Modo proyeccion (docx S19/S20): el panel muestra conversaciones y
  // transcripciones ficticias para una demostracion comercial, sin tocar la
  // base. Mismo interruptor que el resto de las paginas del panel.
  const [modoDemo, setModoDemo] = useState(false);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const messagesContainer = useRef<HTMLDivElement>(null);
  // Solo auto-scrollear al fondo si el operador ya estaba ahi -- si subio a
  // leer historial, el polling de mensajes (cada 5s) no debe devolverlo.
  const stickToBottom = useRef(true);
  // Cuando se prepende historial (loadOlderMessages), guarda el scrollHeight
  // previo para compensar el salto visual una vez el DOM se actualiza.
  const prevScrollHeight = useRef<number | null>(null);
  // Alcances contra respuestas obsoletas (ver whenCurrent en chatState). Cada
  // conversación abierta tiene su AbortController, y cada combinación de
  // filtros de la lista el suyo: al cambiar, el anterior se aborta, sus
  // fetches en vuelo se cancelan y lo que resuelva tarde se descarta. Así una
  // respuesta de la conversación A no pisa a la B, ni una lista vieja a la
  // del filtro nuevo. Los handlers (enviar, archivar...) capturan el alcance
  // vigente al empezar, antes de su primer await.
  const convScope = useRef(new AbortController());
  const listScope = useRef(new AbortController());

  const loadConvs = (signal: AbortSignal = listScope.current.signal) => {
    const params = new URLSearchParams({ estado });
    if (dateDesde) params.set('desde', dateDesde);
    if (dateHasta) params.set('hasta', dateHasta);
    if (modoDemo) params.set('modo', 'demo');
    return whenCurrent(
      signal,
      apiFetch<{ items: Conversation[]; count: number }>(`/intouch/api/conversations?${params.toString()}`, { signal }),
      d => { setConvs(d.items); setCount(d.count); },
    );
  };

  const mergeNewer = (prev: Message[], incoming: Message[]) => {
    const known = new Set(prev.map(m => m.id));
    const fresh = incoming.filter(m => !known.has(m.id));
    return fresh.length ? [...prev, ...fresh].sort((a, b) => a.id - b.id) : prev;
  };

  // Sufijo de las lecturas de mensajes: en proyeccion las transcripciones
  // vienen del backend igual que las reales, por el mismo endpoint.
  const queryDemo = modoDemo ? '?modo=demo' : '';

  // Trae la ultima pagina (mas reciente) y la reemplaza -- uso inicial al
  // abrir una conversacion.
  const loadInitialMessages = (id: number, signal: AbortSignal) =>
    whenCurrent(signal, apiFetch<MessagesPage>(`/intouch/api/messages/${id}${queryDemo}`, { signal }), d => {
      setMessages(d.items);
      setHasMoreOlder(d.has_more);
    });

  // Polling: solo agrega mensajes nuevos que no esten ya cargados, sin
  // descartar el historial anterior que el operador pudo haber cargado
  // scrolleando hacia arriba.
  const loadMessages = (id: number, signal: AbortSignal) =>
    whenCurrent(signal, apiFetch<MessagesPage>(`/intouch/api/messages/${id}${queryDemo}`, { signal }), d => {
      setMessages(prev => mergeNewer(prev, d.items));
    });

  const loadOlderMessages = async () => {
    if (activeId == null || loadingOlder || !hasMoreOlder || messages.length === 0) return;
    const { signal } = convScope.current;
    const oldestId = messages[0].id;
    const el = messagesContainer.current;
    setLoadingOlder(true);
    await whenCurrent(
      signal,
      apiFetch<MessagesPage>(`/intouch/api/messages/${activeId}?before_id=${oldestId}`, { signal }),
      d => {
        if (el) prevScrollHeight.current = el.scrollHeight;
        setMessages(prev => {
          const known = new Set(prev.map(m => m.id));
          return [...d.items.filter(m => !known.has(m.id)), ...prev];
        });
        setHasMoreOlder(d.has_more);
      },
    );
    // Si la conversación cambió mientras tanto, el efecto de abajo ya
    // reinició loadingOlder para la nueva: no tocarlo desde la vieja.
    if (!signal.aborted) setLoadingOlder(false);
  };

  const loadDirectConv = (id: number, signal: AbortSignal) =>
    whenCurrent(
      signal,
      apiFetch<Conversation>(`/intouch/api/conversations/${id}`, { signal }),
      setDirectConv,
      () => setDirectConv(null),
    );

  useEffect(() => {
    const scope = new AbortController();
    listScope.current = scope;
    loadConvs(scope.signal);
    const i = setInterval(() => loadConvs(scope.signal), 10000);
    return () => { scope.abort(); clearInterval(i); };
  }, [estado, dateDesde, dateHasta, modoDemo]);

  // Clamp automatico a 62 dias entre desde/hasta. Si el user pone un rango
  // mayor, se ajusta el hasta y se muestra un aviso amarillo por 4s.
  const warnClamped = () => {
    setRangeClampWarning('Ajustado a máximo 62 días.');
    setTimeout(() => setRangeClampWarning(''), 4000);
  };

  const onDesdeChange = (v: string) => {
    const clamped = v && dateHasta ? clampMaxRange(v, dateHasta) : dateHasta;
    updateFilters({ desde: v, hasta: clamped });
    if (clamped !== dateHasta) warnClamped();
  };

  const onHastaChange = (v: string) => {
    const clamped = dateDesde && v ? clampMaxRange(dateDesde, v) : v;
    updateFilters({ hasta: clamped });
    if (clamped !== v) warnClamped();
  };

  useEffect(() => {
    const scope = new AbortController();
    convScope.current = scope;
    // Nada de la conversación anterior sobrevive al cambio: ni sus mensajes
    // (el polling de la nueva los mezclaría con mergeNewer) ni una carga de
    // historial a medias.
    setMessages([]);
    setHasMoreOlder(false);
    setLoadingOlder(false);
    if (activeId == null) { setDirectConv(null); return () => scope.abort(); }
    stickToBottom.current = true;
    loadInitialMessages(activeId, scope.signal);
    loadDirectConv(activeId, scope.signal);
    const i = setInterval(() => loadMessages(activeId, scope.signal), 5000);
    return () => { scope.abort(); clearInterval(i); };
  }, [activeId, modoDemo]);

  const handleMessagesScroll = () => {
    const el = messagesContainer.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (el.scrollTop < 60) loadOlderMessages();
  };

  useLayoutEffect(() => {
    const el = messagesContainer.current;
    if (!el) return;
    if (prevScrollHeight.current != null) {
      el.scrollTop += el.scrollHeight - prevScrollHeight.current;
      prevScrollHeight.current = null;
      return;
    }
    if (stickToBottom.current) {
      messagesEnd.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
    }
  }, [messages]);

  const send = async (t: string) => {
    if (activeId == null || modoDemo) return;
    // El POST no lleva signal: el mensaje se manda aunque el operador cambie
    // de chat. Sólo la relectura posterior queda atada a esta conversación.
    const { signal } = convScope.current;
    await apiFetch('/intouch/api/admin/send-message', {
      method: 'POST', body: JSON.stringify({ conversation_id: activeId, text: t }),
    }).catch(console.error);
    await loadMessages(activeId, signal);
  };

  // El header del chat siempre debe mostrar el wa_id COMPLETO (abrir una
  // conv es una accion explicita del operador) — api_conversations (lista)
  // enmascara, api_conversation_detail (directConv) no. directConv se carga
  // para toda conversacion activa, asi que preferimos su wa_id sin
  // enmascarar por sobre el de `convs` en vez de mostrar uno u otro segun
  // de que endpoint haya resuelto activeConv (inconsistencia real: el
  // mismo header mostraba a veces enmascarado, a veces no).
  const activeConvBase = convs.find(c => c.id === activeId)
    ?? (directConv?.id === activeId ? directConv : undefined);
  const activeConv = activeConvBase && directConv?.id === activeId
    ? { ...activeConvBase, wa_id: directConv.wa_id }
    : activeConvBase;

  const toggleHumanMode = async () => {
    if (activeId == null || togglingMode || !activeConv) return;
    const { signal } = convScope.current;
    setTogglingMode(true);
    await apiFetch(`/intouch/api/conversations/${activeId}/mode`, {
      method: 'POST', body: JSON.stringify({ human_mode: !activeConv.human_mode }),
    }).catch(console.error);
    // loadConvs() sola no alcanza cuando activeConv viene de directConv (la
    // conv activa quedo fuera del filtro 'estado' actual) — human_mode no
    // cambia si la conv sigue archivada/no archivada, entonces nunca
    // reaparece en `convs` y el fallback quedaria mostrando el estado viejo.
    await Promise.all([loadConvs(), loadDirectConv(activeId, signal)]);
    setTogglingMode(false);
  };

  const toggleArchive = async () => {
    if (activeId == null || archiving || !activeConv) return;
    const { signal } = convScope.current;
    setArchiving(true);
    await apiFetch(`/intouch/api/conversations/${activeId}/archive`, {
      method: 'POST', body: JSON.stringify({ archived: !activeConv.archived }),
    }).catch(console.error);
    await Promise.all([loadConvs(), loadDirectConv(activeId, signal)]);
    setArchiving(false);
  };

  const sidebarContacts = convs
    .filter(c => !search || (c.name + ' ' + c.wa_id).toLowerCase().includes(search.toLowerCase()))
    .map(c => ({
      id: c.id,
      name: c.name || c.wa_id,
      avatar: GENERIC_AVATAR,
      preview:
        (c.open_incidents ? '⚠️ ' : '') +
        (c.temperatura ? `${c.temperatura} · ` : '') +
        c.wa_id +
        (c.human_mode ? ' · HUMAN' : ''),
      time: formatMessageTime(c.updated_at),
      online: !c.human_mode,
      // Duralux ChatSidebar renderiza `unread` como badge -- lo reusamos para
      // el contador de incidentes abiertos (bot.models.Incident).
      unread: c.open_incidents ?? 0,
    }));

  return (
    <div>
      {modoDemo && (
        <Alert variant="warning" icon="feather-alert-triangle" title="Vista de proyección">
          Estas conversaciones son de demostración, no chats reales de este bot.
          Las acciones sobre el chat quedan deshabilitadas mientras el modo está activo.
        </Alert>
      )}
      <PageHeader
        title="Chat"
        breadcrumbs={[{ label: 'Inicio', ...inicio }, { label: 'Chat' }]}
      >
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
      {/* main-content afuera del PageHeader, mismo motivo que las otras 2
          paginas -- PageHeader ya trae 30px de padding propio. */}
      <main className="main-content">
      <div className="card overflow-hidden" style={{ height: 'calc(100vh - 180px)', minHeight: 480 }}>
        <div className="d-flex h-100">
        <div className="flex-shrink-0 h-100 d-flex flex-column" style={{ minHeight: 0 }}>
          <div className="d-flex align-items-center gap-1 p-2 border-bottom flex-shrink-0">
            {(['abiertas', 'cerradas', 'todas'] as const).map(e => (
              <Button
                key={e} size="sm"
                variant={estado === e ? 'primary' : 'light-brand'}
                onClick={() => updateFilters({ estado: e })}
              >
                {e === 'abiertas' ? 'Abiertas' : e === 'cerradas' ? 'Cerradas' : 'Todas'}
              </Button>
            ))}
            <span className="ms-auto"><Badge variant="secondary">{count}</Badge></span>
          </div>
          {/* Filtro por rango de fechas sobre updated_at. Default =
              lunes-domingo de la semana actual. Max 62 dias entre desde/hasta. */}
          <div className="px-2 py-2 border-bottom flex-shrink-0">
            <div className="d-flex gap-2 align-items-end">
              <div className="flex-grow-1">
                <label className="form-label fs-11 mb-1 text-body-secondary">Desde</label>
                <input
                  type="date"
                  className="form-control form-control-sm"
                  value={dateDesde}
                  onChange={e => onDesdeChange(e.target.value)}
                  style={{ colorScheme: 'light dark' }}
                />
              </div>
              <div className="flex-grow-1">
                <label className="form-label fs-11 mb-1 text-body-secondary">Hasta</label>
                <input
                  type="date"
                  className="form-control form-control-sm"
                  value={dateHasta}
                  onChange={e => onHastaChange(e.target.value)}
                  style={{ colorScheme: 'light dark' }}
                />
              </div>
            </div>
            {rangeClampWarning && (
              <div className="fs-11 text-warning mt-1">
                <i className="feather-info me-1"></i>{rangeClampWarning}
              </div>
            )}
          </div>
          <div style={{ overflowY: 'auto', minHeight: 0 }}>
            <ChatSidebar
              contacts={sidebarContacts}
              selectedId={activeId ?? undefined}
              onSelect={(c) => goToConversation(Number(c.id))}
              onSearch={(q: string) => setSearch(q)}
            />
          </div>
        </div>

        <div className="flex-grow-1 d-flex flex-column" style={{ minHeight: 0 }}>
          {!activeConv ? (
            <div className="flex-grow-1 d-flex align-items-center justify-content-center text-body-secondary">
              <div className="text-center">
                <i className="feather-message-circle" style={{ fontSize: 48, opacity: 0.4 }}></i>
                <p className="mt-3 fs-14 mb-0">Seleccioná una conversación</p>
              </div>
            </div>
          ) : (
            <>
              <div className="border-bottom px-4 py-3 d-flex align-items-center justify-content-between flex-shrink-0 bg-body-tertiary">
                <div className="d-flex align-items-center gap-3">
                  <div className="position-relative">
                    <Avatar name={activeConv.name || activeConv.wa_id} size="md" bg="bg-primary" />
                    {!activeConv.human_mode && (
                      <span className="position-absolute bottom-0 end-0 wd-10 ht-10 bg-success rounded-circle border border-2 border-white"></span>
                    )}
                  </div>
                  <div>
                    <div className="fw-semibold fs-14 text-body">{activeConv.name || activeConv.wa_id}</div>
                    <div className="fs-11 text-body-secondary d-flex align-items-center gap-2">
                      <span className="font-monospace">{activeConv.wa_id}</span>
                      {activeConv.active_agent && <Badge variant="dark" soft>{activeConv.active_agent}</Badge>}
                      {activeConv.human_mode && (
                        <Badge variant="warning"><i className="feather-user-check me-1"></i>Modo HUMAN</Badge>
                      )}
                    </div>
                  </div>
                </div>
                <div className="d-flex gap-2">
                  {activeConv.lead_score != null && (
                    <Badge
                      variant={
                        activeConv.temperatura === 'HOT' ? 'danger'
                        : activeConv.temperatura === 'WARM' ? 'warning' : 'info'
                      }
                      soft
                      pill
                    >
                      {activeConv.temperatura || 'lead'} · {activeConv.lead_score}/100
                    </Badge>
                  )}
                  {/* Incidentes es lectura, pero su endpoint no tiene camino
                      de proyeccion: con un id negativo responde 404 y el panel
                      se abriria vacio delante del cliente. */}
                  <Button
                    variant={activeConv.open_incidents ? 'warning' : 'light-brand'}
                    size="sm"
                    icon="feather-alert-triangle"
                    disabled={modoDemo}
                    title={modoDemo ? 'No disponible en proyección' : ''}
                    onClick={() => setShowIncidents(true)}
                  >
                    Incidentes{activeConv.open_incidents ? ` (${activeConv.open_incidents})` : ''}
                  </Button>
                  <Button
                    variant="light-brand"
                    size="sm"
                    icon={activeConv.archived ? 'feather-inbox' : 'feather-archive'}
                    loading={archiving}
                    disabled={modoDemo}
                    title={modoDemo ? 'No disponible en proyección' : ''}
                    onClick={toggleArchive}
                  >
                    {activeConv.archived ? 'Desarchivar' : 'Archivar'}
                  </Button>
                  <Button
                    variant={activeConv.human_mode ? 'success' : 'warning'}
                    size="sm"
                    icon={activeConv.human_mode ? 'feather-cpu' : 'feather-user-check'}
                    loading={togglingMode}
                    disabled={modoDemo}
                    title={modoDemo ? 'No disponible en proyección' : ''}
                    onClick={toggleHumanMode}
                  >
                    {activeConv.human_mode ? 'Devolver al bot' : 'Tomar el chat'}
                  </Button>
                </div>
              </div>

              {(activeConv.resumen || activeConv.intencion) && (
                // docx S18/S21: el resumen automatico es para que un ejecutivo
                // que toma la conversacion se ponga al dia sin leer el hilo
                // entero -- por eso va arriba de los mensajes, no escondido en
                // un detalle.
                <Alert variant="info" icon="feather-cpu" title="Resumen IA">
                  {activeConv.intencion && (
                    <div className="mb-1"><strong>Intención:</strong> {activeConv.intencion}</div>
                  )}
                  {activeConv.resumen}
                </Alert>
              )}

              {activeConv.human_mode && (
                <div className="flex-shrink-0">
                  <Alert variant="warning" icon="feather-info">
                    <strong>Modo HUMAN activo</strong> — el bot no responde a este cliente.
                    Lo que escribas acá llega directo por WhatsApp.
                  </Alert>
                </div>
              )}

              <IncidentsPanel
                conversationId={activeConv.id}
                open={showIncidents}
                onClose={() => setShowIncidents(false)}
                onChanged={() => { loadConvs(); }}
              />

              <div
                ref={messagesContainer}
                onScroll={handleMessagesScroll}
                className="flex-grow-1 overflow-auto p-4"
                style={{ minHeight: 0 }}
              >
                {messages.length === 0 && (
                  <div className="text-center text-body-secondary py-4 fs-12">Sin mensajes</div>
                )}
                {loadingOlder && (
                  <div className="text-center text-body-secondary py-2 fs-12">Cargando mensajes anteriores...</div>
                )}
                {messages.map(m => {
                  // Los mensajes del operador humano se distinguen de las
                  // respuestas del bot con burbuja propia -- en el historial
                  // no se ve diferencia si no.
                  if (m.role === 'human') {
                    return (
                      <div key={m.id} className="mb-4 d-flex flex-column align-items-end">
                        <div className="d-flex align-items-center gap-2 mb-2 flex-row-reverse">
                          <span className="fs-13 fw-semibold">Operador</span>
                          <span className="wd-5 ht-5 bg-warning rounded-circle"></span>
                          <span className="fs-11 text-body-secondary">{formatMessageTime(m.created_at)}</span>
                        </div>
                        <div className="p-3 rounded-4 bg-warning text-dark ms-auto" style={{ maxWidth: '60%' }}>
                          <p className="mb-0 fs-13" style={{ whiteSpace: 'pre-wrap' }}>{m.content}</p>
                        </div>
                      </div>
                    );
                  }
                  // ChatBubble (@duralux/ui) solo soporta texto -- para un
                  // mensaje con adjunto (imagen/audio del cliente) se arma
                  // una burbuja propia con el mismo look, mismo patron que
                  // la rama role === 'human' de arriba.
                  if (m.media_url) {
                    return (
                      <div key={m.id} className="mb-4 d-flex flex-column align-items-start">
                        <div className="d-flex align-items-center gap-2 mb-2">
                          <span className="fs-13 fw-semibold">Cliente</span>
                          <span className="fs-11 text-body-secondary">{formatMessageTime(m.created_at)}</span>
                        </div>
                        <div className="p-3 rounded-4 bg-gray-100" style={{ maxWidth: '60%' }}>
                          {m.media_type === 'image' ? (
                            <img
                              src={m.media_url}
                              alt="Imagen enviada por el cliente"
                              className="img-fluid rounded-3 mb-2"
                              style={{ maxWidth: 240, display: 'block' }}
                            />
                          ) : (
                            <audio controls src={m.media_url} className="mb-2" style={{ display: 'block', maxWidth: 240 }} />
                          )}
                          <p className="mb-0 fs-13" style={{ whiteSpace: 'pre-wrap' }}>{m.content}</p>
                        </div>
                      </div>
                    );
                  }
                  return (
                    <ChatBubble
                      key={m.id}
                      message={{
                        id: m.id, text: m.content, time: formatMessageTime(m.created_at),
                        sender: { name: m.role === 'user' ? 'Cliente' : 'Bot', avatar: GENERIC_AVATAR },
                        mine: m.role !== 'user',
                      }}
                    />
                  );
                })}
                <div ref={messagesEnd}></div>
              </div>

              <div className="flex-shrink-0">
                {modoDemo ? (
                  // ChatInputBar no tiene prop `disabled` (ver su .d.ts), asi
                  // que en proyeccion se reemplaza por el aviso en vez de
                  // dejar una caja de texto que no manda nada.
                  <div className="border-top px-4 py-3 text-center text-body-secondary fs-12">
                    <i className="feather-lock me-1"></i>
                    Envío de mensajes deshabilitado en la vista de proyección
                  </div>
                ) : (
                  <ChatInputBar
                    onSend={send}
                    placeholder={activeConv.human_mode ? 'Modo HUMAN: tu mensaje va directo al cliente' : 'Escribe un mensaje...'}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
      </div>
      </main>
    </div>
  );
}
