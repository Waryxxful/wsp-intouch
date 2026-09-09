export interface GranCrmSession {
  user_id: number;
  email: string;
  nombre: string;
  rol: 'sa' | 'admin' | 'ejecutivo';
  tenant_id: string;
  apps: number[];
}

export interface EventBus {
  emit(event: 'logout' | 'sessionExpired' | 'navigate', payload?: unknown): void;
  on(event: string, cb: (payload: unknown) => void): () => void;
}

export interface GranCrmRemoteProps {
  contractVersion: '1';
  basename: string;
  apiBase: string;
  session: GranCrmSession;
  bus: EventBus;
}
