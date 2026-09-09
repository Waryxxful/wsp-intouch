import jwt
from django.test import TestCase, Client
from django.conf import settings
from bot.models import Conversation, Message


class MetricsEndpointTest(TestCase):
    def _token(self):
        return jwt.encode({"email": "cliente@intouch.cl"}, settings.GRANCRM_JWT_SECRET, algorithm="HS256")

    def test_sin_cookie_devuelve_401(self):
        client = Client()
        response = client.get("/api/metrics")
        self.assertEqual(response.status_code, 401)

    def test_con_cookie_valida_devuelve_las_4_claves_fijas(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="hola")
        client = Client()
        client.cookies["grancrm_session"] = self._token()
        response = client.get("/api/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data.keys()), {"estado", "ultima_actividad", "conversaciones_7d", "mensajes_7d"})
        self.assertEqual(data["conversaciones_7d"], 1)
        self.assertEqual(data["mensajes_7d"], 1)

    def test_sin_actividad_ultima_actividad_es_null(self):
        client = Client()
        client.cookies["grancrm_session"] = self._token()
        response = client.get("/api/metrics")
        self.assertIsNone(response.json()["ultima_actividad"])
