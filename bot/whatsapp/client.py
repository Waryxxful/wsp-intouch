import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SEGUNDOS = 10

# Un httpx.Client de modulo, no la funcion httpx.post(): esa crea un cliente
# nuevo por llamada, o sea DNS + TCP + TLS contra graph.facebook.com en CADA
# mensaje. Medido dentro del contenedor: 175 ms por request abriendo conexion
# nueva contra 142 ms reusandola. Con 2,12 mensajes de WhatsApp por turno
# (mediana real medida en la BD) son ~70 ms por turno -- chico, pero es
# corregir el cliente, no optimizarlo: una API que se llama varias veces por
# turno no tiene por que rehacer el handshake TLS cada vez.
#
# httpx.Client es thread-safe para requests, y hace falta que lo sea: la cola
# de envio (bot/whatsapp/cola_envio.py) manda desde un thread propio mientras
# el request principal puede estar mandando el primer mensaje.
_http = httpx.Client(
    timeout=_TIMEOUT_SEGUNDOS,
    limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
)


def _post(url: str, body: dict, headers: dict) -> httpx.Response:
    """POST con UN reintento ante error de transporte.

    El reintento aparece junto con el pooling y por causa de el: una conexion
    keepalive que Meta cerro del otro lado se descubre recien al reusarla, y
    ahi httpx levanta un TransportError en vez de abrir otra. Sin este
    reintento, reusar conexiones cambiaria 30 ms de handshake por un mensaje
    perdido cada tanto. Solo se reintenta el error de transporte: un 4xx/5xx
    de Meta se devuelve tal cual, como antes.
    """
    try:
        return _http.post(url, json=body, headers=headers)
    except httpx.TransportError as exc:
        logger.warning("[wa] conexion caida (%s), un reintento", exc)
        return _http.post(url, json=body, headers=headers)


class WhatsAppCloudClient:
    def __init__(self):
        self.token = settings.WHATSAPP_TOKEN
        self.phone_id = settings.WHATSAPP_PHONE_ID
        self.base_url = f"https://graph.facebook.com/v20.0/{self.phone_id}"

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def send_text(self, wa_id: str, text: str, reply_to: str | None = None) -> bool:
        body = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "text",
            "text": {"body": text},
        }
        if reply_to:
            body["context"] = {"message_id": reply_to}
        resp = _post(f"{self.base_url}/messages", body, self._headers())
        return resp.status_code == 200

    def send_image(self, wa_id: str, image_url: str, caption: str | None = None) -> bool:
        body = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "image",
            "image": {"link": image_url},
        }
        if caption:
            body["image"]["caption"] = caption
        resp = _post(f"{self.base_url}/messages", body, self._headers())
        return resp.status_code == 200

    def send_document(self, wa_id: str, document_url: str, filename: str | None = None, caption: str | None = None) -> bool:
        body = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "document",
            "document": {"link": document_url},
        }
        if filename:
            body["document"]["filename"] = filename
        if caption:
            body["document"]["caption"] = caption
        resp = _post(f"{self.base_url}/messages", body, self._headers())
        return resp.status_code == 200

    def send_template(self, wa_id: str, template_name: str, language: str = "es", components: list | None = None) -> bool:
        body = {
            "messaging_product": "whatsapp", "to": wa_id, "type": "template",
            "template": {"name": template_name, "language": {"code": language}},
        }
        if components:
            body["template"]["components"] = components
        resp = _post(f"{self.base_url}/messages", body, self._headers())
        if resp.status_code != 200:
            logger.warning(
                "send_template fallo para %s (template=%s): status=%s body=%s",
                wa_id, template_name, resp.status_code, resp.text[:500],
            )
        return resp.status_code == 200

    def send_location(self, wa_id: str, latitude: float, longitude: float, name: str | None = None, address: str | None = None) -> bool:
        location = {"latitude": latitude, "longitude": longitude}
        if name:
            location["name"] = name
        if address:
            location["address"] = address
        body = {
            "messaging_product": "whatsapp",
            "to": wa_id,
            "type": "location",
            "location": location,
        }
        resp = _post(f"{self.base_url}/messages", body, self._headers())
        return resp.status_code == 200

    def mark_as_read(self, msg_id: str, mostrar_escribiendo: bool = False):
        """Marca leido y, opcionalmente, muestra el indicador "escribiendo...".

        El indicador viaja en la MISMA peticion que el acuse de lectura (asi lo
        define la Cloud API), que ya se hace en cada mensaje entrante: no agrega
        ni una request ni un milisegundo de latencia. Meta lo descarta solo
        cuando respondemos, o a los 25 segundos.

        Por que importa: un turno con tool-calling y RAG toma 7-14s (auditoria
        de latencia, docs/PENDIENTES.md #14). Sin indicador el contacto ve
        silencio y no sabe si el bot lo leyo; con indicador ve actividad desde
        el primer instante. No acelera nada -- ataca la latencia PERCIBIDA, que
        es la que el cliente siente."""
        body = {"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}
        if mostrar_escribiendo:
            body["typing_indicator"] = {"type": "text"}
        _post(f"{self.base_url}/messages", body, self._headers())


_client = None


def get_wa_client() -> WhatsAppCloudClient:
    global _client
    if _client is None:
        _client = WhatsAppCloudClient()
    return _client
