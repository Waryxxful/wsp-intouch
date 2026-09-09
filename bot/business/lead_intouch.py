"""Escritura y despacho del lead comercial B2B de InTouch."""

# Los destinos externos que este bot sabe usar. Explícito y no "cualquier
# string": un valor desconocido en LEAD_SINK significaría leads que no se
# despachan a ningún lado sin que nada avise.
SINKS_VALIDOS = frozenset({"none", "http"})
