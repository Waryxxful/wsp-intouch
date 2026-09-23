import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings


@override_settings(DEBUG=True)
class ConnectionStatusTest(TestCase):
    def test_requiere_login(self):
        resp = self.client.get("/demo/api/connection/status")
        self.assertEqual(resp.status_code, 302)

    def test_con_sesion_django_responde_ok(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        resp = self.client.get("/demo/api/connection/status")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("connected", resp.json())


from datetime import timedelta
from django.utils import timezone
from bot.models import Conversation, Incident, Message


@override_settings(DEBUG=True)
class ChatEndpointsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        self.conv = Conversation.objects.create(wa_id="56911112222", name="Ana")
        Message.objects.create(conversation=self.conv, role="user", content="hola")

    def test_lista_conversaciones_default_no_archivadas(self):
        Conversation.objects.create(wa_id="56900000000", archived=True)
        resp = self.client.get("/demo/api/conversations?estado=abiertas")
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["wa_id"], "•••2222")

    def test_lista_conversaciones_todas_incluye_archivadas(self):
        Conversation.objects.create(wa_id="56900000000", archived=True)
        resp = self.client.get("/demo/api/conversations?estado=todas")
        self.assertEqual(resp.json()["count"], 2)

    def test_lista_conversaciones_filtro_fecha_desde_hasta(self):
        vieja = Conversation.objects.create(wa_id="56900000001")
        Conversation.objects.filter(pk=vieja.pk).update(
            updated_at=timezone.now() - timedelta(days=30)
        )
        # localdate() y no now().date(): el segundo da la fecha en UTC y la
        # vista filtra por `updated_at__date`, que Django evalua en el
        # TIME_ZONE del proyecto (America/Santiago). Las tres horas del dia
        # en que UTC y Santiago estan en fechas distintas, el test pedia un
        # dia y la fila estaba en el otro -- fallaba por la hora del reloj,
        # no por el codigo.
        hoy = timezone.localdate().isoformat()
        resp = self.client.get(f"/demo/api/conversations?estado=todas&desde={hoy}&hasta={hoy}")
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["id"], self.conv.pk)

    def test_lista_conversaciones_cerradas_incluye_inactiva_sin_archivar(self):
        inactiva = Conversation.objects.create(wa_id="56900000002")
        Conversation.objects.filter(pk=inactiva.pk).update(
            updated_at=timezone.now() - timedelta(days=10)
        )
        resp_abiertas = self.client.get("/demo/api/conversations?estado=abiertas")
        ids_abiertas = [i["id"] for i in resp_abiertas.json()["items"]]
        self.assertNotIn(inactiva.pk, ids_abiertas)

        resp_cerradas = self.client.get("/demo/api/conversations?estado=cerradas")
        ids_cerradas = [i["id"] for i in resp_cerradas.json()["items"]]
        self.assertIn(inactiva.pk, ids_cerradas)

    def test_lista_conversaciones_open_incidents(self):
        Incident.objects.create(conversation=self.conv, kind="handoff", status="abierto")
        Incident.objects.create(conversation=self.conv, kind="handoff", status="abierto")
        Incident.objects.create(conversation=self.conv, kind="reclamo", status="cerrado")
        resp = self.client.get("/demo/api/conversations?estado=abiertas")
        item = resp.json()["items"][0]
        self.assertEqual(item["open_incidents"], 2)

    def test_detalle_conversacion_devuelve_wa_id_completo(self):
        resp = self.client.get(f"/demo/api/conversations/{self.conv.pk}")
        self.assertEqual(resp.json()["wa_id"], "56911112222")

    def test_mode_toggle_a_human(self):
        resp = self.client.post(
            f"/demo/api/conversations/{self.conv.pk}/mode",
            data='{"human_mode": true}', content_type="application/json",
        )
        self.assertEqual(resp.json(), {"ok": True, "human_mode": True})
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.get_flow().get("modo"), "HUMAN")

    def test_archive_toggle(self):
        resp = self.client.post(f"/demo/api/conversations/{self.conv.pk}/archive")
        self.assertEqual(resp.json(), {"ok": True, "archived": True})
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.archived)

    def test_mensajes_orden_cronologico(self):
        Message.objects.create(conversation=self.conv, role="assistant", content="dale")
        resp = self.client.get(f"/demo/api/messages/{self.conv.pk}")
        data = resp.json()
        contenidos = [m["content"] for m in data["items"]]
        self.assertEqual(contenidos, ["hola", "dale"])
        self.assertFalse(data["has_more"])

    def test_mensajes_paginacion_before_id(self):
        for i in range(60):
            Message.objects.create(conversation=self.conv, role="assistant", content=f"msg{i}")
        # pagina mas reciente: has_more=True porque hay 61 mensajes en total
        resp = self.client.get(f"/demo/api/messages/{self.conv.pk}")
        data = resp.json()
        self.assertEqual(len(data["items"]), 50)
        self.assertTrue(data["has_more"])
        self.assertEqual(data["items"][0]["content"], "msg10")

        oldest_id = data["items"][0]["id"]
        resp2 = self.client.get(f"/demo/api/messages/{self.conv.pk}?before_id={oldest_id}")
        data2 = resp2.json()
        contenidos2 = [m["content"] for m in data2["items"]]
        self.assertEqual(contenidos2, ["hola"] + [f"msg{i}" for i in range(10)])
        self.assertFalse(data2["has_more"])

    def test_mensaje_sin_media_no_expone_media_url(self):
        resp = self.client.get(f"/demo/api/messages/{self.conv.pk}")
        item = resp.json()["items"][0]
        self.assertIsNone(item["media_url"])
        self.assertIsNone(item["media_type"])

    def test_mensaje_con_imagen_expone_endpoint_de_media_y_tipo_image(self):
        m = Message.objects.create(
            conversation=self.conv, role="user", content="Se ve un Renault Kwid rojo",
            media_url="abc123.jpg",
        )
        resp = self.client.get(f"/demo/api/messages/{self.conv.pk}")
        item = next(i for i in resp.json()["items"] if i["id"] == m.pk)
        self.assertEqual(item["media_url"], f"/intouch/api/media/{m.pk}")
        self.assertEqual(item["media_type"], "image")

    def test_mensaje_con_audio_expone_tipo_audio(self):
        m = Message.objects.create(
            conversation=self.conv, role="user", content="hola quiero cotizar un auto",
            media_url="def456.ogg",
        )
        resp = self.client.get(f"/demo/api/messages/{self.conv.pk}")
        item = next(i for i in resp.json()["items"] if i["id"] == m.pk)
        self.assertEqual(item["media_type"], "audio")

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_text")
    def test_send_message_ok_crea_mensaje_role_human(self, mock_send):
        # send_text mockeado explicitamente -- nunca debe pegarle a la red real
        # de WhatsApp sin importar que credenciales haya cargadas en el entorno
        # (bug real: este test antes dependia de que WHATSAPP_TOKEN estuviera
        # vacio para fallar localmente; en un entorno con credenciales reales
        # mandaba un mensaje de WhatsApp de verdad en cada corrida de tests).
        mock_send.return_value = True
        resp = self.client.post(
            "/demo/api/admin/send-message",
            data=f'{{"conversation_id": {self.conv.pk}, "text": "hola desde el panel"}}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        self.assertTrue(
            Message.objects.filter(conversation=self.conv, role="human", content="hola desde el panel").exists()
        )

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_text")
    def test_send_message_falla_crea_mensaje_role_human_igual(self, mock_send):
        # Simula el fallo real (token invalido, rate limit, etc.) sin depender
        # de que el entorno tenga o no credenciales configuradas.
        mock_send.side_effect = httpx.HTTPError("token invalido o llamada fallida")
        resp = self.client.post(
            "/demo/api/admin/send-message",
            data=f'{{"conversation_id": {self.conv.pk}, "text": "hola desde el panel"}}',
            content_type="application/json",
        )
        self.assertFalse(resp.json()["ok"])
        self.assertTrue(
            Message.objects.filter(conversation=self.conv, role="human", content="hola desde el panel").exists()
        )

    def test_send_message_sin_texto_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/send-message",
            data=f'{{"conversation_id": {self.conv.pk}, "text": ""}}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(DEBUG=True)
class MediaFileEndpointTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.conv = Conversation.objects.create(wa_id="56911112222", name="Ana")

    def test_requiere_login(self):
        m = Message.objects.create(conversation=self.conv, role="user", content="foto", media_url="x.jpg")
        resp = self.client.get(f"/demo/api/media/{m.pk}")
        self.assertEqual(resp.status_code, 302)

    def test_mensaje_sin_media_url_devuelve_404(self):
        self.client.login(username="admin", password="admin123")
        m = Message.objects.create(conversation=self.conv, role="user", content="hola")
        resp = self.client.get(f"/demo/api/media/{m.pk}")
        self.assertEqual(resp.status_code, 404)

    def test_archivo_registrado_pero_ausente_en_disco_devuelve_404(self):
        self.client.login(username="admin", password="admin123")
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                m = Message.objects.create(
                    conversation=self.conv, role="user", content="foto", media_url="no-existe.jpg",
                )
                resp = self.client.get(f"/demo/api/media/{m.pk}")
        self.assertEqual(resp.status_code, 404)

    def test_sirve_el_archivo_con_sesion_valida(self):
        self.client.login(username="admin", password="admin123")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xe0fake jpeg bytes")
            with override_settings(WHATSAPP_MEDIA_ROOT=tmp):
                m = Message.objects.create(
                    conversation=self.conv, role="user", content="foto", media_url="abc123.jpg",
                )
                resp = self.client.get(f"/demo/api/media/{m.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"\xff\xd8\xff\xe0fake jpeg bytes")


@override_settings(DEBUG=True)
class IncidentEndpointsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        self.conv = Conversation.objects.create(wa_id="56911112222", name="Ana")

    def test_lista_incidentes_de_la_conversacion_mas_recientes_primero(self):
        viejo = Incident.objects.create(conversation=self.conv, kind="handoff", context={"reason": "primero"})
        Incident.objects.filter(pk=viejo.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        nuevo = Incident.objects.create(conversation=self.conv, kind="reclamo", context={"reason": "segundo"})
        resp = self.client.get(f"/demo/api/conversations/{self.conv.pk}/incidents")
        data = resp.json()
        self.assertEqual([i["id"] for i in data["items"]], [nuevo.pk, viejo.pk])
        self.assertEqual(data["items"][0]["status"], "abierto")
        self.assertEqual(data["items"][0]["context"]["reason"], "segundo")

    def test_lista_incidentes_no_incluye_los_de_otra_conversacion(self):
        otra = Conversation.objects.create(wa_id="56900000000")
        Incident.objects.create(conversation=otra, kind="handoff", context={})
        resp = self.client.get(f"/demo/api/conversations/{self.conv.pk}/incidents")
        self.assertEqual(resp.json()["items"], [])

    def test_actualiza_status_a_valor_valido(self):
        inc = Incident.objects.create(conversation=self.conv, kind="handoff", context={})
        resp = self.client.post(
            f"/demo/api/incidents/{inc.pk}/status",
            data='{"status": "cerrado"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "cerrado")
        inc.refresh_from_db()
        self.assertEqual(inc.status, "cerrado")

    def test_status_invalido_devuelve_400_y_no_cambia_nada(self):
        inc = Incident.objects.create(conversation=self.conv, kind="handoff", context={})
        resp = self.client.post(
            f"/demo/api/incidents/{inc.pk}/status",
            data='{"status": "no_existe"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        inc.refresh_from_db()
        self.assertEqual(inc.status, "abierto")

    def test_requiere_login(self):
        self.client.logout()
        inc = Incident.objects.create(conversation=self.conv, kind="handoff", context={})
        resp = self.client.get(f"/demo/api/conversations/{self.conv.pk}/incidents")
        self.assertEqual(resp.status_code, 302)
        resp = self.client.post(
            f"/demo/api/incidents/{inc.pk}/status",
            data='{"status": "cerrado"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 302)


@override_settings(DEBUG=True)
class ConfigEndpointsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_bot_state_default_activo(self):
        resp = self.client.get("/demo/api/admin/bot-state")
        self.assertEqual(resp.json(), {"active": True})

    def test_bot_state_toggle(self):
        resp = self.client.post(
            "/demo/api/admin/bot-state", data='{"active": false}', content_type="application/json",
        )
        self.assertEqual(resp.json(), {"ok": True})
        resp = self.client.get("/demo/api/admin/bot-state")
        self.assertEqual(resp.json(), {"active": False})

    def test_prompt_sin_override_devuelve_el_default_del_agente(self):
        from bot.flow.agents.agendamiento import SYSTEM_PROMPT
        resp = self.client.get("/demo/api/admin/prompt?agente=agendamiento")
        data = resp.json()
        self.assertTrue(data["is_default"])
        self.assertEqual(data["prompt"], SYSTEM_PROMPT)
        self.assertEqual(
            set(data["agentes"]),
            {"agendamiento", "confirmacion", "faq", "global", "encuesta_servicio_tecnico", "encuesta_venta_auto_nuevo"},
        )

    def test_prompt_default_de_encuesta_servicio_tecnico(self):
        from bot.flow.agents.encuesta_servicio_tecnico import SYSTEM_PROMPT
        resp = self.client.get("/demo/api/admin/prompt?agente=encuesta_servicio_tecnico")
        self.assertEqual(resp.json()["prompt"], SYSTEM_PROMPT)

    def test_prompt_default_de_encuesta_venta_auto_nuevo(self):
        from bot.flow.agents.encuesta_venta_auto_nuevo import SYSTEM_PROMPT
        resp = self.client.get("/demo/api/admin/prompt?agente=encuesta_venta_auto_nuevo")
        self.assertEqual(resp.json()["prompt"], SYSTEM_PROMPT)

    def test_prompt_con_agente_invalido_devuelve_400(self):
        resp = self.client.get("/demo/api/admin/prompt?agente=no-existe")
        self.assertEqual(resp.status_code, 400)

    def test_prompt_guardar_y_reset_a_default(self):
        self.client.post(
            "/demo/api/admin/prompt?agente=faq",
            data='{"prompt": "prompt de prueba"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/prompt?agente=faq")
        self.assertEqual(resp.json()["prompt"], "prompt de prueba")
        self.assertFalse(resp.json()["is_default"])

        self.client.post(
            "/demo/api/admin/prompt?agente=faq",
            data='{"action": "reset"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/prompt?agente=faq")
        self.assertTrue(resp.json()["is_default"])

    def test_prompt_guardar_dos_veces_crea_dos_versiones(self):
        from bot.models import PromptVersion
        self.client.post(
            "/demo/api/admin/prompt?agente=faq",
            data='{"prompt": "version 1"}', content_type="application/json",
        )
        self.client.post(
            "/demo/api/admin/prompt?agente=faq",
            data='{"prompt": "version 2"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/prompt?agente=faq")
        self.assertEqual(resp.json()["prompt"], "version 2")
        self.assertEqual(PromptVersion.objects.filter(agente="faq").count(), 2)

    def test_prompt_guardar_sin_prompt_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/prompt?agente=faq",
            data='{}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_prompt_global_sin_override_devuelve_el_default(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT
        resp = self.client.get("/demo/api/admin/prompt?agente=global")
        data = resp.json()
        self.assertTrue(data["is_default"])
        self.assertEqual(data["prompt"], SYSTEM_PROMPT)

    def test_prompt_global_guardar_y_reset_a_default(self):
        self.client.post(
            "/demo/api/admin/prompt?agente=global",
            data='{"prompt": "prompt global de prueba"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/prompt?agente=global")
        self.assertEqual(resp.json()["prompt"], "prompt global de prueba")
        self.assertFalse(resp.json()["is_default"])

        self.client.post(
            "/demo/api/admin/prompt?agente=global",
            data='{"action": "reset"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/prompt?agente=global")
        self.assertTrue(resp.json()["is_default"])

    def test_llm_config_default_sin_override(self):
        resp = self.client.get("/demo/api/admin/llm-config")
        data = resp.json()
        self.assertEqual(data["model_source"], "env")
        self.assertFalse(data["key_override"])

    def test_llm_config_guardar_modelo(self):
        self.client.post(
            "/demo/api/admin/llm-config",
            data='{"model": "deepseek/deepseek-v4-flash"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/llm-config")
        data = resp.json()
        self.assertEqual(data["model_source"], "override")
        self.assertEqual(data["active_model"], "deepseek/deepseek-v4-flash")

    def test_llm_config_key_nunca_se_expone(self):
        self.client.post(
            "/demo/api/admin/llm-config",
            data='{"api_key": "secreta123"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/llm-config")
        self.assertNotIn("secreta123", resp.content.decode())
        self.assertTrue(resp.json()["key_set"])


@override_settings(DEBUG=True)
class ScrapingLlmConfigEndpointTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_scraping_llm_config_default_sin_override(self):
        resp = self.client.get("/demo/api/admin/scraping-llm-config")
        data = resp.json()
        self.assertEqual(data["model_source"], "env")
        self.assertEqual(data["active_model"], settings.OPENROUTER_SCRAPING_MODEL)

    def test_scraping_llm_config_guardar_modelo(self):
        self.client.post(
            "/demo/api/admin/scraping-llm-config",
            data='{"model": "deepseek/deepseek-v4-flash-0731"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/scraping-llm-config")
        data = resp.json()
        self.assertEqual(data["model_source"], "override")
        self.assertEqual(data["active_model"], "deepseek/deepseek-v4-flash-0731")

    def test_scraping_llm_config_ya_no_acepta_una_api_key_propia(self):
        # Desde la migracion del 2026-09-02 la extraccion usa la key de
        # OpenRouter, administrada en llm-config. Este endpoint aceptaba una
        # key propia de DeepSeek: si volviera a aceptarla, habria dos lugares
        # del panel editando credenciales del mismo proveedor y el operador no
        # tendria como saber cual manda.
        self.client.post(
            "/demo/api/admin/scraping-llm-config",
            data='{"api_key": "secreta123"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/scraping-llm-config")
        from bot.models import Setting
        self.assertNotIn("secreta123", resp.content.decode())
        self.assertEqual(Setting.objects.filter(key__contains="api_key").count(), 0)

    @override_settings(OPENROUTER_API_KEY="k")
    def test_scraping_llm_config_reporta_la_key_compartida_de_openrouter(self):
        self.assertTrue(self.client.get("/demo/api/admin/scraping-llm-config").json()["key_set"])


@override_settings(DEBUG=True)
class MediaLlmConfigEndpointTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_media_llm_config_default_sin_override(self):
        resp = self.client.get("/demo/api/admin/media-llm-config")
        data = resp.json()
        self.assertEqual(data["model_source"], "env")
        self.assertEqual(data["active_model"], "google/gemini-3.7-flash")

    def test_media_llm_config_guardar_modelo(self):
        self.client.post(
            "/demo/api/admin/media-llm-config",
            data='{"model": "google/gemini-2.5-flash"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/media-llm-config")
        data = resp.json()
        self.assertEqual(data["model_source"], "override")
        self.assertEqual(data["active_model"], "google/gemini-2.5-flash")


@override_settings(DEBUG=True)
class DashboardEndpointsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_dashboard_shape(self):
        resp = self.client.get("/demo/api/admin/dashboard")
        data = resp.json()
        for key in [
            "conv_count", "msg_count", "msg_today", "msg_by_role", "active_24h",
            "active_flows", "chart", "bot_on", "connected", "phone_id", "model",
            "reservas_total", "reservas_today", "reservas_week", "human_mode_count",
            "agent_distribution",
        ]:
            self.assertIn(key, data)
        self.assertEqual(len(data["chart"]), 7)

    def test_range_default_es_7d(self):
        resp = self.client.get("/demo/api/admin/dashboard")
        self.assertEqual(resp.json()["range"], "7d")

    def test_range_today(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=today")
        self.assertEqual(resp.json()["range"], "today")

    def test_range_month_y_prev_month(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=month")
        self.assertEqual(resp.json()["range"], "month")
        resp = self.client.get("/demo/api/admin/dashboard?range=prev_month")
        self.assertEqual(resp.json()["range"], "prev_month")

    def test_range_custom_valido(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        self.assertEqual(resp.json()["range"], "custom")

    def test_range_custom_invalido_cae_a_7d(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=nofecha&to=tampoco")
        self.assertEqual(resp.json()["range"], "7d")

    def test_range_desconocido_cae_a_7d(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=loquesea")
        self.assertEqual(resp.json()["range"], "7d")

    def test_funnel_cuentas_y_tasas(self):
        from bot.models import Servicio, Sucursal, Reserva, CampaignSend
        servicio = Servicio.objects.create(nombre="Corte", duracion_min=30)
        sucursal = Sucursal.objects.create(nombre="Centro")

        # 4 conversaciones en el rango custom 2026-01-01..2026-01-10
        conv_ids = []
        for i in range(4):
            c = Conversation.objects.create(wa_id=f"5691111000{i}")
            Conversation.objects.filter(pk=c.pk).update(created_at="2026-01-05T12:00:00Z")
            conv_ids.append(c.pk)
        convs = list(Conversation.objects.filter(pk__in=conv_ids))

        # 3 de las 4 respondidas por el bot
        for c in convs[:3]:
            Message.objects.create(conversation=c, role="user", content="hola")
            Message.objects.create(conversation=c, role="assistant", content="hola, en que ayudo?")

        # 1 conversacion fuera del rango (no debe contar)
        fuera = Conversation.objects.create(wa_id="56900000000")
        Conversation.objects.filter(pk=fuera.pk).update(created_at="2020-01-01T12:00:00Z")

        # 2 reservas dentro del rango (horas distintas: Reserva exige cupo
        # unico por sucursal/fecha/hora activa)
        for i in range(2):
            Reserva.objects.create(
                codigo=f"R{i}", contacto="56911110000", servicio=servicio, sucursal=sucursal,
                fecha="2026-02-01", hora=f"1{i}:00", estado="activa",
            )
        for r in Reserva.objects.all():
            Reserva.objects.filter(pk=r.pk).update(created_at="2026-01-06T12:00:00Z")

        # 1 recordatorio enviado, confirmado
        send = CampaignSend.objects.create(
            contacto="56911110000", campaign_type="recordatorio_24h", template="t1", respondido=True,
        )
        CampaignSend.objects.filter(pk=send.pk).update(enviado_at="2026-01-07T12:00:00Z")

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        funnel = {f["stage"]: f for f in resp.json()["funnel"]}

        self.assertEqual(funnel["conversaciones"]["count"], 4)
        self.assertIsNone(funnel["conversaciones"]["rate_total"])
        self.assertEqual(funnel["respondidas"]["count"], 3)
        self.assertEqual(funnel["respondidas"]["rate_step"], 0.75)
        self.assertEqual(funnel["respondidas"]["rate_total"], 0.75)
        self.assertEqual(funnel["cita_agendada"]["count"], 2)
        self.assertEqual(funnel["recordatorio_enviado"]["count"], 1)
        self.assertEqual(funnel["recordatorio_confirmado"]["count"], 1)

    def test_funnel_no_cuenta_reservas_canceladas(self):
        """Verifica que las reservas canceladas no se cuentan en el funnel (consistencia con reservas_total)."""
        from bot.models import Servicio, Sucursal, Reserva
        servicio = Servicio.objects.create(nombre="Corte", duracion_min=30)
        sucursal = Sucursal.objects.create(nombre="Centro")

        # 2 conversaciones en el rango custom 2026-01-01..2026-01-10
        for i in range(2):
            c = Conversation.objects.create(wa_id=f"5691111100{i}")
            Conversation.objects.filter(pk=c.pk).update(created_at="2026-01-05T12:00:00Z")

        # 1 reserva activa dentro del rango
        r_activa = Reserva.objects.create(
            codigo="R_ACTIVA", contacto="56911110000", servicio=servicio, sucursal=sucursal,
            fecha="2026-02-01", hora="10:00", estado="activa",
        )
        Reserva.objects.filter(pk=r_activa.pk).update(created_at="2026-01-06T12:00:00Z")

        # 1 reserva cancelada dentro del rango (NO debe contar)
        r_cancelada = Reserva.objects.create(
            codigo="R_CANCELADA", contacto="56911110000", servicio=servicio, sucursal=sucursal,
            fecha="2026-02-01", hora="11:00", estado="cancelada",
        )
        Reserva.objects.filter(pk=r_cancelada.pk).update(created_at="2026-01-07T12:00:00Z")

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        funnel = {f["stage"]: f for f in resp.json()["funnel"]}

        # Solo la reserva activa debe contarse en cita_agendada
        self.assertEqual(funnel["cita_agendada"]["count"], 1)

    def test_kpis_tiempo_respuesta_y_tasas(self):
        conv = Conversation.objects.create(wa_id="56922223333")
        Conversation.objects.filter(pk=conv.pk).update(created_at="2026-01-05T12:00:00Z")
        m1 = Message.objects.create(conversation=conv, role="user", content="hola")
        Message.objects.filter(pk=m1.pk).update(created_at="2026-01-05T12:00:00Z")
        m2 = Message.objects.create(conversation=conv, role="assistant", content="hola!")
        Message.objects.filter(pk=m2.pk).update(created_at="2026-01-05T12:02:00Z")  # 120s despues

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        kpis = resp.json()["kpis"]
        self.assertEqual(kpis["tiempo_respuesta_promedio_seg"], 120)
        self.assertEqual(kpis["tiempo_respuesta_p50_seg"], 120)

    def test_el_promedio_cuenta_cada_pregunta_no_solo_la_primera(self):
        conv = Conversation.objects.create(wa_id="56922224444")
        Conversation.objects.filter(pk=conv.pk).update(created_at="2026-01-05T12:00:00Z")
        pares = [
            ("user", "2026-01-05T12:00:00Z", "hola"),
            ("assistant", "2026-01-05T12:00:10Z", "hola"),
            ("assistant", "2026-01-05T12:00:11Z", "segunda burbuja"),
            ("user", "2026-01-05T12:01:00Z", "precio"),
            ("assistant", "2026-01-05T12:01:30Z", "depende"),
        ]
        for role, cuando, texto in pares:
            mensaje = Message.objects.create(conversation=conv, role=role, content=texto)
            Message.objects.filter(pk=mensaje.pk).update(created_at=cuando)

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        kpis = resp.json()["kpis"]
        self.assertEqual(kpis["tiempo_respuesta_promedio_seg"], 20)
        self.assertEqual(kpis["tiempo_respuesta_p50_seg"], 20)
        self.assertEqual(kpis["pct_respondidas"], 1.0)
        self.assertEqual(kpis["pct_agendada"], 0.0)
        self.assertEqual(kpis["templates_enviados"], 0)

    def test_kpis_sin_datos_en_el_rango_son_none(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2020-01-01&to=2020-01-02")
        kpis = resp.json()["kpis"]
        self.assertIsNone(kpis["tiempo_respuesta_promedio_seg"])
        self.assertIsNone(kpis["tiempo_respuesta_p50_seg"])
        self.assertIsNone(kpis["pct_respondidas"])

    def test_kpi_fuera_de_horario(self):
        # 2026-01-05 es lunes. Horario default lunes: 09:00-18:00 (ver _BUSINESS_HOURS_DEFAULT).
        dentro = Conversation.objects.create(wa_id="56933330001")
        Conversation.objects.filter(pk=dentro.pk).update(created_at="2026-01-05T14:00:00Z")
        fuera = Conversation.objects.create(wa_id="56933330002")
        Conversation.objects.filter(pk=fuera.pk).update(created_at="2026-01-05T22:00:00Z")

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        self.assertEqual(resp.json()["kpis"]["fuera_de_horario"], 1)

    def test_alerta_human_sin_atender_se_dispara(self):
        conv = Conversation.objects.create(wa_id="56944440001", active_agent="faq")
        conv.set_flow({"modo": "HUMAN"})
        conv.save()
        m = Message.objects.create(conversation=conv, role="user", content="hola")
        Message.objects.filter(pk=m.pk).update(created_at=timezone.now() - timedelta(minutes=20))

        resp = self.client.get("/demo/api/admin/dashboard")
        titles = [a["title"] for a in resp.json()["alerts"]]
        self.assertIn("Conversación en HUMAN sin atender", titles)

    def test_alerta_human_no_se_dispara_si_es_reciente(self):
        conv = Conversation.objects.create(wa_id="56944440002", active_agent="faq")
        conv.set_flow({"modo": "HUMAN"})
        conv.save()
        Message.objects.create(conversation=conv, role="user", content="hola recien")

        resp = self.client.get("/demo/api/admin/dashboard")
        titles = [a["title"] for a in resp.json()["alerts"]]
        self.assertNotIn("Conversación en HUMAN sin atender", titles)

    def test_alerta_human_sin_atender_se_dispara_con_active_agent_vacio(self):
        # Simula una conversación que acaba de ser entregada por el bot a un humano.
        # El bot establece active_agent=None que se persiste como "" (empty string).
        # Sin la fix, el .exclude(active_agent="") ignoraría este caso crítico.
        conv = Conversation.objects.create(wa_id="56944440003", active_agent="")
        conv.set_flow({"modo": "HUMAN"})
        conv.save()
        m = Message.objects.create(conversation=conv, role="user", content="hola bot")
        Message.objects.filter(pk=m.pk).update(created_at=timezone.now() - timedelta(minutes=20))

        resp = self.client.get("/demo/api/admin/dashboard")
        titles = [a["title"] for a in resp.json()["alerts"]]
        self.assertIn("Conversación en HUMAN sin atender", titles)

    def test_alerta_tasa_agendamiento_baja_se_dispara(self):
        for i in range(10):
            c = Conversation.objects.create(wa_id=f"5695555{i:04d}")
            Conversation.objects.filter(pk=c.pk).update(created_at="2026-01-05T12:00:00Z")
        # 0 de 10 con reserva -> pct_agendada = 0.0 < 0.10

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-10")
        titles = [a["title"] for a in resp.json()["alerts"]]
        self.assertIn("Tasa de agendamiento baja", titles)

    def test_sin_disparadores_no_hay_alertas(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2020-01-01&to=2020-01-02")
        self.assertEqual(resp.json()["alerts"], [])

    def test_dashboard_incluye_funnel_kpis_alerts_keys(self):
        resp = self.client.get("/demo/api/admin/dashboard")
        data = resp.json()
        self.assertIn("range", data)
        self.assertIn("funnel", data)
        for stage in data["funnel"]:
            for key in ["stage", "label", "count", "rate_step", "rate_total"]:
                self.assertIn(key, stage)
        for key in [
            "tiempo_respuesta_promedio_seg", "tiempo_respuesta_p50_seg", "pct_respondidas",
            "pct_agendada", "templates_enviados", "fuera_de_horario",
        ]:
            self.assertIn(key, data["kpis"])
        self.assertIsInstance(data["alerts"], list)

    def test_funnel_evolution_una_fila_por_dia(self):
        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-01&to=2026-01-03")
        evolution = resp.json()["funnel_evolution"]
        self.assertEqual(len(evolution), 3)
        self.assertEqual([e["date"] for e in evolution], ["2026-01-01", "2026-01-02", "2026-01-03"])

    def test_funnel_evolution_cuenta_por_dia_correctamente(self):
        from bot.models import Servicio, Sucursal, Reserva

        # 2 conversaciones el dia 05, 1 el dia 06, ninguna el dia 04
        for i in range(2):
            c = Conversation.objects.create(wa_id=f"5691112{i}00")
            Conversation.objects.filter(pk=c.pk).update(created_at="2026-01-05T12:00:00Z")
        c3 = Conversation.objects.create(wa_id="56911130000")
        Conversation.objects.filter(pk=c3.pk).update(created_at="2026-01-06T12:00:00Z")

        # 1 reserva activa el dia 06
        servicio = Servicio.objects.create(nombre="Corte", duracion_min=30)
        sucursal = Sucursal.objects.create(nombre="Centro")
        r = Reserva.objects.create(
            codigo="REV1", contacto="56911130000", servicio=servicio, sucursal=sucursal,
            fecha="2026-02-01", hora="10:00", estado="activa",
        )
        Reserva.objects.filter(pk=r.pk).update(created_at="2026-01-06T12:00:00Z")

        resp = self.client.get("/demo/api/admin/dashboard?range=custom&from=2026-01-04&to=2026-01-06")
        evolution = {e["date"]: e for e in resp.json()["funnel_evolution"]}

        self.assertEqual(evolution["2026-01-04"]["conversaciones"], 0)
        self.assertEqual(evolution["2026-01-05"]["conversaciones"], 2)
        self.assertEqual(evolution["2026-01-06"]["conversaciones"], 1)
        self.assertEqual(evolution["2026-01-06"]["cita_agendada"], 1)
        self.assertEqual(evolution["2026-01-05"]["cita_agendada"], 0)


# Nota: NO agregamos aca un endpoint api/metrics propio — el skeleton
# (bot/api.py, montado en /api/ por config/urls.py) ya trae uno con el
# mismo contrato de 4 claves. Duplicarlo en admin_panel bajo /demo/api/
# hubiera dejado dos rutas distintas respondiendo lo mismo con auth
# distinta — se descubrio durante el review de este task y se elimino.


@override_settings(DEBUG=True)
class ScrapingSourceEndpointsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_crear_fuente(self):
        resp = self.client.post(
            "/demo/api/admin/scraping-sources",
            data='{"url": "https://chery.cl/", "nombre": "Chery", "frecuencia_horas": 24}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        from bot.models import ScrapingSource
        self.assertTrue(
            ScrapingSource.objects.filter(url="https://chery.cl/", nombre="Chery", frecuencia_horas=24).exists()
        )

    def test_crear_fuente_estampa_cliente_activo(self):
        # Multi-cliente: sin cliente= explicito en ScrapingSource.objects.create(),
        # la fila caia siempre en "renault" (default del campo) sin importar
        # CLIENTE_ACTIVO -- ver docs/PENDIENTES.md, checklist multi-cliente.
        from bot.models import ScrapingSource
        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.post(
                "/demo/api/admin/scraping-sources",
                data='{"url": "https://astararetail.cl/"}', content_type="application/json",
            )
            self.assertEqual(resp.status_code, 201)
            fuente = ScrapingSource.todos_los_clientes.get(url="https://astararetail.cl/")
            self.assertEqual(fuente.cliente, "astara")

    def test_crear_fuente_sin_url_devuelve_400(self):
        resp = self.client.post("/demo/api/admin/scraping-sources", data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_crear_fuente_sin_esquema_antepone_https(self):
        resp = self.client.post(
            "/demo/api/admin/scraping-sources",
            data='{"url": "toyota.cl"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        from bot.models import ScrapingSource
        self.assertTrue(ScrapingSource.objects.filter(url="https://toyota.cl").exists())

    def test_crear_fuente_duplicada_devuelve_400(self):
        from bot.models import ScrapingSource
        ScrapingSource.objects.create(url="https://chery.cl/")
        resp = self.client.post(
            "/demo/api/admin/scraping-sources",
            data='{"url": "https://chery.cl/"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(ScrapingSource.objects.filter(url="https://chery.cl/").count(), 1)

    def test_listar_fuentes_incluye_last_run(self):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/", nombre="Chery")
        source.runs.create(url=source.url, estado="ok", paginas_procesadas=3)
        resp = self.client.get("/demo/api/admin/scraping-sources")
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["nombre"], "Chery")
        self.assertEqual(data[0]["last_run"]["paginas_procesadas"], 3)

    def test_listar_fuentes_sin_runs_last_run_null(self):
        from bot.models import ScrapingSource
        ScrapingSource.objects.create(url="https://chery.cl/")
        resp = self.client.get("/demo/api/admin/scraping-sources")
        self.assertIsNone(resp.json()[0]["last_run"])

    def test_actualizar_fuente(self):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        resp = self.client.put(
            f"/demo/api/admin/scraping-sources/{source.pk}",
            data='{"nombre": "Chery Chile", "frecuencia_horas": 12}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        source.refresh_from_db()
        self.assertEqual(source.nombre, "Chery Chile")
        self.assertEqual(source.frecuencia_horas, 12)

    def test_borrar_fuente(self):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        resp = self.client.delete(f"/demo/api/admin/scraping-sources/{source.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ScrapingSource.objects.filter(pk=source.pk).exists())

    @patch("admin_panel.views.threading.Thread")
    @patch("admin_panel.views.runner.execute_scrape")
    def test_run_dispara_scrape_en_background(self, mock_execute, mock_thread_cls):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        resp = self.client.post(f"/demo/api/admin/scraping-sources/{source.pk}/run")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["estado"], "corriendo")
        self.assertTrue(source.runs.filter(estado="corriendo").exists())

    @patch("admin_panel.views.threading.Thread")
    @patch("admin_panel.views.runner.execute_scrape")
    def test_run_devuelve_409_si_ya_hay_una_corrida_en_curso(self, mock_execute, mock_thread_cls):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        source.runs.create(url=source.url, estado="corriendo")
        resp = self.client.post(f"/demo/api/admin/scraping-sources/{source.pk}/run")
        self.assertEqual(resp.status_code, 409)
        mock_thread_cls.assert_not_called()

    def test_detail_sin_runs_404(self):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        resp = self.client.get(f"/demo/api/admin/scraping-sources/{source.pk}/detail")
        self.assertEqual(resp.status_code, 404)

    def test_detail_incluye_paginas_catalogo_y_errores(self):
        from bot.models import ScrapingSource
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        run = source.runs.create(
            url=source.url, estado="ok", paginas_procesadas=1,
            catalogo_extraido={"servicios": [{"nombre": "Cambio de aceite"}], "sucursales": []},
            paginas_con_error=[{"url": "https://chery.cl/rota", "error": "timeout"}],
        )
        run.pages.create(url=source.url, texto="Bienvenido a Chery")
        resp = self.client.get(f"/demo/api/admin/scraping-sources/{source.pk}/detail")
        data = resp.json()
        self.assertEqual(data["pages"], [{"url": source.url, "texto": "Bienvenido a Chery"}])
        self.assertEqual(data["catalogo_extraido"]["servicios"][0]["nombre"], "Cambio de aceite")
        self.assertEqual(data["paginas_con_error"], [{"url": "https://chery.cl/rota", "error": "timeout"}])


@override_settings(DEBUG=True)
class EnviarRecordatorioTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        from bot.models import Servicio, Sucursal, Reserva
        servicio = Servicio.objects.create(nombre="Corte", duracion_min=30)
        sucursal = Sucursal.objects.create(nombre="Centro")
        self.reserva = Reserva.objects.create(
            codigo="ABC123", contacto="56911112222", servicio=servicio, sucursal=sucursal,
            fecha="2026-08-01", hora="09:00", estado="activa",
        )

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_text")
    def test_enviar_recordatorio_registra_campaign_send_y_precarga_flow_data(self, mock_send):
        # send_text mockeado -- mismo motivo que ChatEndpointsTest: este test
        # mandaba un recordatorio real por WhatsApp en cualquier entorno con
        # credenciales configuradas, en vez de depender de un mock.
        mock_send.return_value = True
        from bot.models import CampaignSend, Conversation
        resp = self.client.post(f"/demo/api/admin/reservas/{self.reserva.codigo}/enviar-recordatorio")
        self.assertTrue(resp.json()["ok"])
        self.assertTrue(CampaignSend.objects.filter(contacto="56911112222", campaign_type="recordatorio_24h").exists())
        conv = Conversation.objects.get(wa_id="56911112222")
        self.assertEqual(conv.get_flow()["reserva_actual"]["codigo"], "ABC123")

    def test_enviar_recordatorio_reserva_inexistente_404(self):
        resp = self.client.post("/demo/api/admin/reservas/NOEXISTE/enviar-recordatorio")
        self.assertEqual(resp.status_code, 404)

    def test_enviar_recordatorio_reserva_cancelada_409(self):
        self.reserva.estado = "cancelada"
        self.reserva.save(update_fields=["estado"])
        resp = self.client.post(f"/demo/api/admin/reservas/{self.reserva.codigo}/enviar-recordatorio")
        self.assertEqual(resp.status_code, 409)

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_text")
    def test_enviar_recordatorio_contacto_optout_no_envia(self, mock_send):
        from bot.models import CampaignSend, registrar_optout
        registrar_optout("56911112222", motivo="pidio no ser contactado")
        resp = self.client.post(f"/demo/api/admin/reservas/{self.reserva.codigo}/enviar-recordatorio")
        self.assertEqual(resp.status_code, 409)
        mock_send.assert_not_called()
        self.assertFalse(CampaignSend.objects.filter(contacto="56911112222").exists())


@override_settings(DEBUG=True)
class WelcomeVacationTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_welcome_default(self):
        resp = self.client.get("/demo/api/admin/welcome")
        self.assertEqual(resp.json()["message"], "¡Hola! ¿En qué le puedo ayudar?")

    def test_welcome_guardar(self):
        self.client.post(
            "/demo/api/admin/welcome", data='{"message": "Hola custom"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/welcome")
        self.assertEqual(resp.json()["message"], "Hola custom")

    def test_vacation_default_y_guardar(self):
        resp = self.client.get("/demo/api/admin/vacation")
        self.assertIn("horario", resp.json()["message"].lower())
        self.client.post(
            "/demo/api/admin/vacation", data='{"message": "Volvemos el lunes"}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/vacation")
        self.assertEqual(resp.json()["message"], "Volvemos el lunes")


@override_settings(DEBUG=True)
class BusinessHoursTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_get_autosiembra_7_dias(self):
        resp = self.client.get("/demo/api/admin/business-hours")
        dias = resp.json()["dias"]
        self.assertEqual(len(dias), 7)
        self.assertEqual([d["dia_semana"] for d in dias], list(range(7)))

    def test_get_no_duplica_si_ya_existen(self):
        self.client.get("/demo/api/admin/business-hours")
        self.client.get("/demo/api/admin/business-hours")
        from admin_panel.models import BusinessHours
        self.assertEqual(BusinessHours.objects.count(), 7)

    def test_post_actualiza_un_dia(self):
        self.client.get("/demo/api/admin/business-hours")
        self.client.post(
            "/demo/api/admin/business-hours",
            data='{"dias": [{"dia_semana": 0, "hora_inicio": "10:00", "hora_fin": "19:00", "activo": true}]}',
            content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/business-hours")
        lunes = next(d for d in resp.json()["dias"] if d["dia_semana"] == 0)
        self.assertEqual(lunes["hora_inicio"], "10:00")
        self.assertEqual(lunes["hora_fin"], "19:00")


@override_settings(DEBUG=True)
class QuickResponsesTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_crear_y_listar(self):
        resp = self.client.post(
            "/demo/api/admin/quick-responses",
            data='{"pattern": "gracias", "response": "De nada!", "priority": 1}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get("/demo/api/admin/quick-responses")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["pattern"], "gracias")

    def test_actualizar(self):
        from admin_panel.models import QuickResponse
        qr = QuickResponse.objects.create(pattern="hola", response="Hola!", priority=0)
        resp = self.client.put(
            f"/demo/api/admin/quick-responses/{qr.pk}",
            data='{"pattern": "hola", "response": "Buenas!", "priority": 5}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        qr.refresh_from_db()
        self.assertEqual(qr.response, "Buenas!")
        self.assertEqual(qr.priority, 5)

    def test_borrar(self):
        from admin_panel.models import QuickResponse
        qr = QuickResponse.objects.create(pattern="chao", response="Adios!")
        resp = self.client.delete(f"/demo/api/admin/quick-responses/{qr.pk}")
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(QuickResponse.objects.filter(pk=qr.pk).exists())


@override_settings(DEBUG=True)
class SnippetsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_crear_y_listar(self):
        resp = self.client.post(
            "/demo/api/admin/snippets",
            data='{"nombre": "Direccion", "texto": "Estamos en Av. Siempre Viva 123"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get("/demo/api/admin/snippets")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["nombre"], "Direccion")

    def test_actualizar(self):
        from admin_panel.models import Snippet
        s = Snippet.objects.create(nombre="Horario", texto="Lun-Vie 9 a 18")
        resp = self.client.put(
            f"/demo/api/admin/snippets/{s.pk}",
            data='{"nombre": "Horario", "texto": "Lun-Sab 9 a 20"}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        s.refresh_from_db()
        self.assertEqual(s.texto, "Lun-Sab 9 a 20")

    def test_borrar(self):
        from admin_panel.models import Snippet
        s = Snippet.objects.create(nombre="Temp", texto="borrame")
        resp = self.client.delete(f"/demo/api/admin/snippets/{s.pk}")
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(Snippet.objects.filter(pk=s.pk).exists())


@override_settings(DEBUG=True)
class FiltersTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_crear_y_listar(self):
        resp = self.client.post(
            "/demo/api/admin/filters",
            data='{"wa_id": "56911112222", "tipo": "block"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get("/demo/api/admin/filters")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["tipo"], "block")

    def test_borrar(self):
        from admin_panel.models import Filter
        f = Filter.objects.create(wa_id="56900000000", tipo="allow")
        resp = self.client.delete(f"/demo/api/admin/filters/{f.pk}")
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(Filter.objects.filter(pk=f.pk).exists())


@override_settings(DEBUG=True)
class HandoffTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_get_default_vacio(self):
        resp = self.client.get("/demo/api/admin/handoff")
        self.assertEqual(resp.json()["keywords"], [])

    def test_guardar_keywords(self):
        self.client.post(
            "/demo/api/admin/handoff",
            data='{"keywords": ["hablar con humano", "operador"]}',
            content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/handoff")
        self.assertEqual(resp.json()["keywords"], ["hablar con humano", "operador"])


@override_settings(DEBUG=True)
class LogsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        self.conv = Conversation.objects.create(wa_id="56911112222", name="Ana")
        Message.objects.create(conversation=self.conv, role="user", content="hola")
        Message.objects.create(conversation=self.conv, role="assistant", content="hola, en que ayudo?")

    def test_logs_lista_todos_los_mensajes(self):
        resp = self.client.get("/demo/api/admin/logs")
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["wa_id"], "•••2222")

    def test_logs_filtra_por_conversation_id(self):
        otra = Conversation.objects.create(wa_id="56900000000")
        Message.objects.create(conversation=otra, role="user", content="otro chat")
        resp = self.client.get(f"/demo/api/admin/logs?conversation_id={self.conv.pk}")
        self.assertEqual(len(resp.json()), 2)

    def test_logs_export_devuelve_csv(self):
        resp = self.client.get("/demo/api/admin/logs/export")
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment", resp["Content-Disposition"])
        content = resp.content.decode()
        self.assertIn("hola", content)

    def test_logs_con_params_no_numericos_no_revienta(self):
        # Regresion: conversation_id/limit no numericos (link viejo, URL
        # editada a mano) tumbaban la vista con un 500 sin manejar.
        resp = self.client.get("/demo/api/admin/logs?conversation_id=abc&limit=xyz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)


@override_settings(DEBUG=True)
class AuditTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_audit_lista_acciones_ya_registradas(self):
        # api_bot_state ya llama audit() en cada toggle (Parte A2) -- lo
        # usamos para generar una fila real sin mockear nada.
        self.client.post(
            "/demo/api/admin/bot-state", data='{"active": false}', content_type="application/json",
        )
        resp = self.client.get("/demo/api/admin/audit")
        data = resp.json()
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]["action"], "bot_state")


@override_settings(DEBUG=True)
class SpecialistsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_crear_y_listar(self):
        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envíos", "descripcion": "Responde sobre estado de envios.", "prompt": "Sos el especialista de envios."}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["slug"], "envios")

        resp = self.client.get("/demo/api/admin/specialists")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["slug"], "envios")
        self.assertEqual(resp.json()[0]["descripcion"], "Responde sobre estado de envios.")

    def test_crear_estampa_cliente_activo(self):
        # Multi-cliente: sin cliente= explicito en CustomSpecialist.objects.create(),
        # la fila caia siempre en "renault" (default del campo) sin importar
        # CLIENTE_ACTIVO -- ver docs/PENDIENTES.md, checklist multi-cliente.
        from bot.models import CustomSpecialist
        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.post(
                "/demo/api/admin/specialists",
                data='{"label": "Envios", "descripcion": "d", "prompt": "p"}',
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 201)
            specialist = CustomSpecialist.todos_los_clientes.get(slug="envios")
            self.assertEqual(specialist.cliente, "astara")

    def test_crear_sin_descripcion_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envios", "descripcion": "", "prompt": "algo"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_sin_prompt_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envios", "descripcion": "algo", "prompt": ""}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_con_slug_reservado_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Agendamiento", "descripcion": "algo", "prompt": "algo"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        from bot.models import CustomSpecialist
        self.assertFalse(CustomSpecialist.objects.filter(slug="agendamiento").exists())

    def test_crear_con_slug_global_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Global", "descripcion": "algo", "prompt": "algo"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        from bot.models import CustomSpecialist
        self.assertFalse(CustomSpecialist.objects.filter(slug="global").exists())

    def test_crear_con_slug_duplicado_devuelve_400(self):
        self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envios", "descripcion": "d", "prompt": "p"}',
            content_type="application/json",
        )
        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envios", "descripcion": "d2", "prompt": "p2"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_con_slug_de_especialista_borrado_devuelve_400(self):
        from bot.models import CustomSpecialist, save_prompt_version
        s = CustomSpecialist.objects.create(slug="envios", label="Envíos", descripcion="d")
        save_prompt_version("custom:envios", "prompt viejo")
        s.delete()

        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envios", "descripcion": "d2", "prompt": "p2"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_actualizar(self):
        from bot.models import CustomSpecialist, save_prompt_version, get_active_prompt
        s = CustomSpecialist.objects.create(slug="envios", label="Envíos", descripcion="d viejo")
        save_prompt_version("custom:envios", "p viejo")
        resp = self.client.put(
            f"/demo/api/admin/specialists/{s.pk}",
            data='{"descripcion": "d nuevo", "prompt": "p nuevo"}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        s.refresh_from_db()
        self.assertEqual(s.descripcion, "d nuevo")
        self.assertEqual(get_active_prompt("custom:envios"), "p nuevo")

    def test_actualizar_sin_descripcion_devuelve_400(self):
        from bot.models import CustomSpecialist, save_prompt_version
        s = CustomSpecialist.objects.create(slug="envios", label="Envíos", descripcion="d")
        save_prompt_version("custom:envios", "p")
        resp = self.client.put(
            f"/demo/api/admin/specialists/{s.pk}",
            data='{"descripcion": "", "prompt": "p nuevo"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_borrar(self):
        from bot.models import CustomSpecialist, save_prompt_version
        s = CustomSpecialist.objects.create(slug="temp", label="Temp", descripcion="d")
        save_prompt_version("custom:temp", "p")
        resp = self.client.delete(f"/demo/api/admin/specialists/{s.pk}")
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(CustomSpecialist.objects.filter(pk=s.pk).exists())

    def test_requiere_rol_admin(self):
        User.objects.create_user("agente", "agente@test.com", "pass123")
        self.client.logout()
        self.client.login(username="agente", password="pass123")
        resp = self.client.get("/demo/api/admin/specialists")
        self.assertEqual(resp.status_code, 403)


@override_settings(DEBUG=True)
class PromptVersionsEndpointTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def test_lista_vacia_sin_versiones(self):
        resp = self.client.get("/demo/api/admin/prompt-versions?agente=agendamiento")
        self.assertEqual(resp.json(), [])

    def test_lista_orden_descendente_y_marca_la_activa(self):
        from bot.models import save_prompt_version
        save_prompt_version("agendamiento", "v1")
        save_prompt_version("agendamiento", "v2")
        resp = self.client.get("/demo/api/admin/prompt-versions?agente=agendamiento")
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertTrue(data[0]["activa"])
        self.assertFalse(data[1]["activa"])

    def test_restore_reactiva_version_vieja(self):
        from bot.models import save_prompt_version, get_active_prompt, PromptVersion
        save_prompt_version("agendamiento", "v1")
        v1_id = PromptVersion.objects.get(agente="agendamiento").pk
        save_prompt_version("agendamiento", "v2")
        resp = self.client.post(
            "/demo/api/admin/prompt-versions/restore",
            data=f'{{"agente": "agendamiento", "version_id": {v1_id}}}', content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(get_active_prompt("agendamiento"), "v1")
        self.assertEqual(PromptVersion.objects.filter(agente="agendamiento", activa=True).count(), 1)

    def test_restore_con_agente_cruzado_devuelve_400(self):
        from bot.models import save_prompt_version, PromptVersion
        save_prompt_version("agendamiento", "v1")
        version_id = PromptVersion.objects.get(agente="agendamiento").pk
        resp = self.client.post(
            "/demo/api/admin/prompt-versions/restore",
            data=f'{{"agente": "faq", "version_id": {version_id}}}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_requiere_rol_admin(self):
        User.objects.create_user("agente", "agente@test.com", "pass123")
        self.client.logout()
        self.client.login(username="agente", password="pass123")
        resp = self.client.get("/demo/api/admin/prompt-versions?agente=agendamiento")
        self.assertEqual(resp.status_code, 403)

    def test_ciclo_completo_custom_specialist_crear_editar_listar_restaurar(self):
        from bot.models import CustomSpecialist
        from bot.flow.agents.custom import CustomPromptAgent

        resp = self.client.post(
            "/demo/api/admin/specialists",
            data='{"label": "Envios", "descripcion": "d", "prompt": "version 1"}',
            content_type="application/json",
        )
        slug = resp.json()["slug"]
        row = CustomSpecialist.objects.get(slug=slug)
        self.assertEqual(CustomPromptAgent(row).effective_prompt(), "version 1")

        self.client.put(
            f"/demo/api/admin/specialists/{row.pk}",
            data='{"descripcion": "d", "prompt": "version 2"}',
            content_type="application/json",
        )
        self.assertEqual(CustomPromptAgent(row).effective_prompt(), "version 2")

        resp = self.client.get(f"/demo/api/admin/prompt-versions?agente=custom:{slug}")
        versiones = resp.json()
        self.assertEqual(len(versiones), 2)
        v1_id = min(v["id"] for v in versiones)

        self.client.post(
            "/demo/api/admin/prompt-versions/restore",
            data=f'{{"agente": "custom:{slug}", "version_id": {v1_id}}}', content_type="application/json",
        )
        self.assertEqual(CustomPromptAgent(row).effective_prompt(), "version 1")


@override_settings(DEBUG=True)
class TestScenariosTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        from bot.simulator.models import EscenarioDePrueba
        EscenarioDePrueba.objects.all().delete()

    def test_crear_y_listar(self):
        resp = self.client.post(
            "/demo/api/admin/test-scenarios",
            data='{"nombre": "e1", "persona": "cliente x", "objetivo": "objetivo x", "criterios": ["c1"]}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["nombre"], "e1")

        resp = self.client.get("/demo/api/admin/test-scenarios")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["nombre"], "e1")
        self.assertTrue(resp.json()[0]["activo"])

    def test_crear_sin_criterios_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/test-scenarios",
            data='{"nombre": "e1", "persona": "x", "objetivo": "y", "criterios": []}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_con_nombre_duplicado_devuelve_400(self):
        from bot.simulator.models import EscenarioDePrueba
        EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])
        resp = self.client.post(
            "/demo/api/admin/test-scenarios",
            data='{"nombre": "e1", "persona": "a", "objetivo": "b", "criterios": ["c"]}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_actualizar_incluyendo_desactivar(self):
        from bot.simulator.models import EscenarioDePrueba
        e = EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])
        resp = self.client.put(
            f"/demo/api/admin/test-scenarios/{e.pk}",
            data='{"persona": "x2", "objetivo": "y2", "criterios": ["z2"], "activo": false}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        e.refresh_from_db()
        self.assertEqual(e.persona, "x2")
        self.assertFalse(e.activo)

    def test_actualizar_sin_fuente_preserva_fuente_actual(self):
        from bot.simulator.models import EscenarioDePrueba
        e = EscenarioDePrueba.objects.create(
            nombre="e1", persona="x", objetivo="y", criterios=["z"], fuente="scraping",
        )
        resp = self.client.put(
            f"/demo/api/admin/test-scenarios/{e.pk}",
            data='{"persona": "x2", "objetivo": "y2", "criterios": ["z2"]}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        e.refresh_from_db()
        self.assertEqual(e.fuente, "scraping")

    def test_borrar_escenario_sin_corridas(self):
        from bot.simulator.models import EscenarioDePrueba
        e = EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])
        resp = self.client.delete(f"/demo/api/admin/test-scenarios/{e.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(EscenarioDePrueba.objects.filter(pk=e.pk).exists())

    def test_borrar_escenario_con_corridas_devuelve_400(self):
        from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, ResultadoDeEscenario
        e = EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])
        corrida = CorridaDePrueba.objects.create(disparada_por="admin")
        ResultadoDeEscenario.objects.create(corrida=corrida, escenario=e, paso=True, fallos=[], transcript="t")
        resp = self.client.delete(f"/demo/api/admin/test-scenarios/{e.pk}")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(EscenarioDePrueba.objects.filter(pk=e.pk).exists())


@override_settings(DEBUG=True)
class TestRunsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        from bot.simulator.models import EscenarioDePrueba
        # simulator/migrations/0002_seed_escenarios.py siembra 8 escenarios
        # activos en toda BD de test (mismo problema y misma solucion que
        # TestScenariosTest.setUp mas arriba) -- sin este delete(),
        # test_trigger_sin_escenarios_activos_devuelve_400_sin_crear_corrida
        # nunca ve "cero escenarios activos" porque los sembrados siguen ahi.
        EscenarioDePrueba.objects.all().delete()
        self.e1 = EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])

    @patch("admin_panel.views.threading.Thread")
    def test_trigger_crea_la_corrida_y_devuelve_su_id(self, mock_thread_cls):
        from utils.tenant_middleware import get_current_db

        resp = self.client.post(
            "/demo/api/admin/test-runs/trigger",
            data="{}", content_type="application/json",
        )
        self.assertEqual(resp.status_code, 202)
        from bot.simulator.models import CorridaDePrueba
        corrida = CorridaDePrueba.objects.get(pk=resp.json()["id"])
        self.assertEqual(corrida.estado, "corriendo")
        mock_thread_cls.assert_called_once()
        # Antes (Hallazgo 3 de la revision final) el test se quedaba en
        # assert_called_once() y nunca chequeaba que el thread realmente
        # apuntara a _ejecutar_corrida_en_background con los args correctos
        # -- esa funcion (el puente entre el thread y ejecutar_corrida) nunca
        # se ejercitaba en el suite.
        import admin_panel.views as views_module
        _, kwargs = mock_thread_cls.call_args
        self.assertIs(kwargs["target"], views_module._ejecutar_corrida_en_background)
        self.assertEqual(kwargs["args"], (get_current_db(), corrida.pk))
        self.assertTrue(kwargs.get("daemon"))

    @patch("admin_panel.views.threading.Thread")
    def test_trigger_con_corrida_en_curso_devuelve_409_sin_crear_otra(self, mock_thread_cls):
        from bot.simulator.models import CorridaDePrueba

        resp1 = self.client.post(
            "/demo/api/admin/test-runs/trigger",
            data="{}", content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 202)
        self.assertEqual(CorridaDePrueba.objects.count(), 1)

        # El mock de threading.Thread no arranca nada de verdad -- la
        # corrida creada por iniciar_corrida() se queda en "corriendo" en la
        # BD, tal como quedaria si un thread real de background siguiera
        # ejecutandose.
        resp2 = self.client.post(
            "/demo/api/admin/test-runs/trigger",
            data="{}", content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 409)
        self.assertEqual(CorridaDePrueba.objects.count(), 1)
        mock_thread_cls.assert_called_once()

    def test_ejecutar_corrida_en_background_setea_db_y_llama_ejecutar_corrida(self):
        from bot.simulator.models import CorridaDePrueba
        from admin_panel.views import _ejecutar_corrida_en_background

        corrida = CorridaDePrueba.objects.create(disparada_por="consola")
        with patch("admin_panel.views.set_current_db") as mock_set_current_db, \
                patch("admin_panel.views.ejecutar_corrida") as mock_ejecutar_corrida:
            _ejecutar_corrida_en_background("default", corrida.pk)

        mock_set_current_db.assert_called_once_with("default")
        mock_ejecutar_corrida.assert_called_once()
        (llamada_con,), _ = mock_ejecutar_corrida.call_args
        self.assertEqual(llamada_con.pk, corrida.pk)

    def test_trigger_sin_escenarios_activos_devuelve_400_sin_crear_corrida(self):
        self.e1.activo = False
        self.e1.save()
        resp = self.client.post(
            "/demo/api/admin/test-runs/trigger",
            data="{}", content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        from bot.simulator.models import CorridaDePrueba
        self.assertEqual(CorridaDePrueba.objects.count(), 0)

    @patch("admin_panel.views.threading.Thread")
    def test_trigger_con_filtro_de_escenario(self, mock_thread_cls):
        resp = self.client.post(
            "/demo/api/admin/test-runs/trigger",
            data='{"nombre_escenario": "e1"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 202)
        from bot.simulator.models import CorridaDePrueba
        corrida = CorridaDePrueba.objects.get(pk=resp.json()["id"])
        self.assertEqual(corrida.nombre_escenario_filtro, "e1")

    def test_runs_lista_con_resumen_y_marca_stale_como_interrumpida(self):
        from datetime import timedelta
        from django.utils import timezone
        from bot.simulator.models import CorridaDePrueba, ResultadoDeEscenario

        corrida = CorridaDePrueba.objects.create(disparada_por="consola")
        ResultadoDeEscenario.objects.create(corrida=corrida, escenario=self.e1, paso=True, fallos=[], transcript="t")
        CorridaDePrueba.objects.filter(pk=corrida.pk).update(
            actualizado_en=timezone.now() - timedelta(minutes=30)
        )

        resp = self.client.get("/demo/api/admin/test-runs")
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["estado"], "interrumpida")
        self.assertEqual(data[0]["pasaron"], 1)
        self.assertEqual(data[0]["total"], 1)

    def test_run_detail_incluye_resultados_con_transcript(self):
        from bot.simulator.models import CorridaDePrueba, ResultadoDeEscenario

        corrida = CorridaDePrueba.objects.create(disparada_por="consola")
        ResultadoDeEscenario.objects.create(
            corrida=corrida, escenario=self.e1, paso=False,
            fallos=["algo fallo"], transcript="Cliente: hola",
        )

        resp = self.client.get(f"/demo/api/admin/test-runs/{corrida.pk}")
        data = resp.json()
        self.assertEqual(len(data["resultados"]), 1)
        self.assertEqual(data["resultados"][0]["escenario"], "e1")
        self.assertEqual(data["resultados"][0]["transcript"], "Cliente: hola")
        self.assertEqual(data["resultados"][0]["fallos"], ["algo fallo"])


@override_settings(
    DEBUG=True,
    ENCUESTA_SERVICIO_TECNICO_TEMPLATE="tmpl_encuesta_st",
    ENCUESTA_VENTA_AUTO_NUEVO_TEMPLATE="",
)
class EnviarEncuestaMasivaTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_template")
    def test_csv_valido_crea_campaign_send_y_manda_por_cada_fila(self, mock_send):
        mock_send.return_value = True
        from bot.models import CampaignSend
        csv_text = "wa_id,nombre\n56911112222,Juan\n56933334444,Maria\n"
        resp = self.client.post(
            "/demo/api/admin/encuestas/enviar-masivo",
            data=json.dumps({"campaign_type": "encuesta_servicio_tecnico", "csv_text": csv_text}),
            content_type="application/json",
        )
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["enviados"], 2)
        self.assertEqual(body["optout_saltados"], 0)
        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(
            CampaignSend.objects.filter(campaign_type="encuesta_servicio_tecnico").count(), 2,
        )

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_template")
    def test_contacto_en_optout_se_saltea(self, mock_send):
        mock_send.return_value = True
        from bot.models import CampaignSend, registrar_optout
        registrar_optout("56911112222", motivo="no contactar")
        csv_text = "wa_id\n56911112222\n56933334444\n"
        resp = self.client.post(
            "/demo/api/admin/encuestas/enviar-masivo",
            data=json.dumps({"campaign_type": "encuesta_servicio_tecnico", "csv_text": csv_text}),
            content_type="application/json",
        )
        body = resp.json()
        self.assertEqual(body["enviados"], 1)
        self.assertEqual(body["optout_saltados"], 1)
        self.assertFalse(CampaignSend.objects.filter(contacto="56911112222").exists())

    def test_fila_con_wa_id_vacio_se_reporta_como_error_sin_frenar_el_resto(self):
        # nota: una linea EN BLANCO no sirve para este caso -- csv.DictReader
        # las salta silenciosamente (nunca llegan como fila). Se necesita una
        # fila real con el campo wa_id vacio (columna presente, valor vacio).
        with patch("bot.whatsapp.client.WhatsAppCloudClient.send_template", return_value=True):
            csv_text = "wa_id,nombre\n,Sin Numero\n56933334444,Maria\n"
            resp = self.client.post(
                "/demo/api/admin/encuestas/enviar-masivo",
                data=json.dumps({"campaign_type": "encuesta_servicio_tecnico", "csv_text": csv_text}),
                content_type="application/json",
            )
        body = resp.json()
        self.assertEqual(body["enviados"], 1)
        self.assertEqual(len(body["errores"]), 1)

    def test_campaign_type_desconocido_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/encuestas/enviar-masivo",
            data=json.dumps({"campaign_type": "promocion_random", "csv_text": "wa_id\n56911112222\n"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_campaign_type_sin_template_configurado_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/encuestas/enviar-masivo",
            data=json.dumps({"campaign_type": "encuesta_venta_auto_nuevo", "csv_text": "wa_id\n56911112222\n"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_csv_con_mas_de_200_filas_devuelve_400_sin_enviar_nada(self):
        from bot.models import CampaignSend
        filas = "\n".join(f"5691111{i:04d}" for i in range(201))
        csv_text = f"wa_id\n{filas}\n"
        with patch("bot.whatsapp.client.WhatsAppCloudClient.send_template") as mock_send:
            resp = self.client.post(
                "/demo/api/admin/encuestas/enviar-masivo",
                data=json.dumps({"campaign_type": "encuesta_servicio_tecnico", "csv_text": csv_text}),
                content_type="application/json",
            )
            mock_send.assert_not_called()
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(CampaignSend.objects.exists())

    @patch("bot.whatsapp.client.WhatsAppCloudClient.send_template")
    def test_contacto_con_encuesta_ya_activa_no_se_reenvia(self, mock_send):
        from bot.models import CampaignSend
        mock_send.return_value = True
        CampaignSend.objects.create(
            contacto="56911112222", campaign_type="encuesta_servicio_tecnico", template="tmpl_encuesta_st",
        )
        csv_text = "wa_id\n56911112222\n"
        resp = self.client.post(
            "/demo/api/admin/encuestas/enviar-masivo",
            data=json.dumps({"campaign_type": "encuesta_servicio_tecnico", "csv_text": csv_text}),
            content_type="application/json",
        )
        body = resp.json()
        self.assertEqual(body["enviados"], 0)
        self.assertEqual(len(body["errores"]), 1)
        mock_send.assert_not_called()
        self.assertEqual(CampaignSend.objects.filter(contacto="56911112222").count(), 1)

    def test_csv_sin_columna_wa_id_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/encuestas/enviar-masivo",
            data=json.dumps({"campaign_type": "encuesta_servicio_tecnico", "csv_text": "telefono\n56911112222\n"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class ActualizarEnvDockerTest(TestCase):
    """Fix de fondo: nunca reescribir el archivo completo desde una
    estructura en memoria -- .env.docker tiene secretos reales, solo se
    tocan las lineas de las claves pedidas."""

    def test_reemplaza_una_clave_existente_preserva_el_resto(self):
        from admin_panel.cliente_flip import actualizar_env_docker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.docker"
            path.write_text("WHATSAPP_TOKEN=secreto123\nRAG_SCHEMA=renault\nDEBUG=false\n")

            actualizar_env_docker(path, {"RAG_SCHEMA": "astara"})

            self.assertEqual(
                path.read_text(),
                "WHATSAPP_TOKEN=secreto123\nRAG_SCHEMA=astara\nDEBUG=false\n",
            )

    def test_agrega_una_clave_que_no_existia(self):
        from admin_panel.cliente_flip import actualizar_env_docker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.docker"
            path.write_text("RAG_SCHEMA=renault\n")

            actualizar_env_docker(path, {"CLIENTE_ACTIVO": "astara"})

            self.assertEqual(path.read_text(), "RAG_SCHEMA=renault\nCLIENTE_ACTIVO=astara\n")

    def test_reemplaza_varias_claves_a_la_vez(self):
        from admin_panel.cliente_flip import actualizar_env_docker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.docker"
            path.write_text("CLIENTE_ACTIVO=renault\nRAG_SCHEMA=renault\n")

            actualizar_env_docker(path, {"CLIENTE_ACTIVO": "astara", "RAG_SCHEMA": "astara"})

            self.assertEqual(path.read_text(), "CLIENTE_ACTIVO=astara\nRAG_SCHEMA=astara\n")

    def test_no_toca_lineas_comentadas_que_contengan_el_signo_igual(self):
        from admin_panel.cliente_flip import actualizar_env_docker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.docker"
            path.write_text("# CLIENTE_ACTIVO=comentario_viejo\nCLIENTE_ACTIVO=renault\n")

            actualizar_env_docker(path, {"CLIENTE_ACTIVO": "astara"})

            self.assertEqual(
                path.read_text(),
                "# CLIENTE_ACTIVO=comentario_viejo\nCLIENTE_ACTIVO=astara\n",
            )

    def test_archivo_inexistente_lo_crea(self):
        from admin_panel.cliente_flip import actualizar_env_docker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.docker"

            actualizar_env_docker(path, {"CLIENTE_ACTIVO": "astara"})

            self.assertEqual(path.read_text(), "CLIENTE_ACTIVO=astara\n")


class FlipPendienteTest(TestCase):
    def test_escribir_y_leer_flip_pendiente(self):
        from admin_panel.cliente_flip import escribir_flip_pendiente, leer_flip_pendiente
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".flip_request.json"

            escribir_flip_pendiente(path, "astara", "admin@test.com")
            resultado = leer_flip_pendiente(path)

            self.assertEqual(resultado["target_cliente"], "astara")
            self.assertEqual(resultado["solicitado_por"], "admin@test.com")
            self.assertIn("solicitado_en", resultado)

    def test_leer_sin_archivo_devuelve_none(self):
        from admin_panel.cliente_flip import leer_flip_pendiente
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(leer_flip_pendiente(Path(tmp) / "no-existe.json"))


@override_settings(DEBUG=True)
class ClienteActivoEndpointTest(TestCase):
    """El endpoint solo escribe .env.docker + el pedido de flip -- nunca
    toca Docker (ver docstring de api_cliente_activo). El restart real lo
    hace un cron del host, fuera del alcance de estos tests."""

    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        from bot.models import PromptVersion
        PromptVersion.todos_los_clientes.create(agente="global", prompt="hola astara", activa=True, cliente="astara")

    def _paths(self, tmp):
        return {"ENV_DOCKER_PATH": Path(tmp) / ".env.docker", "FLIP_REQUEST_PATH": Path(tmp) / ".flip_request.json"}

    def test_requiere_rol_admin(self):
        User.objects.create_user("agente", "agente@test.com", "pass12345")
        self.client.login(username="agente", password="pass12345")
        resp = self.client.post(
            "/demo/api/admin/cliente-activo", data='{"target": "astara"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_get_devuelve_cliente_activo_y_opciones(self):
        resp = self.client.get("/demo/api/admin/cliente-activo")
        body = resp.json()
        self.assertEqual(body["cliente_activo"], "renault")
        self.assertIn(["astara", "Astara Retail"], [list(o) for o in body["opciones"]])
        self.assertIsNone(body["flip_pendiente"])

    def test_flip_a_cliente_valido_escribe_env_y_pedido(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(**self._paths(tmp)):
                resp = self.client.post(
                    "/demo/api/admin/cliente-activo", data='{"target": "astara"}', content_type="application/json",
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json(), {"ok": True, "target": "astara"})
                env_contenido = (Path(tmp) / ".env.docker").read_text()
                self.assertIn("CLIENTE_ACTIVO=astara", env_contenido)
                self.assertIn("RAG_SCHEMA=astara", env_contenido)
                pedido = json.loads((Path(tmp) / ".flip_request.json").read_text())
                self.assertEqual(pedido["target_cliente"], "astara")

    def test_flip_al_mismo_cliente_activo_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/cliente-activo", data='{"target": "renault"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_flip_a_cliente_invalido_devuelve_400(self):
        resp = self.client.post(
            "/demo/api/admin/cliente-activo", data='{"target": "mazda"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_flip_a_cliente_sin_prompt_global_activo_devuelve_400(self):
        # Justo el desastre que evita este check: sin esto, flippear a un
        # cliente sin identidad configurada dejaria al bot respondiendo con
        # el fallback hardcodeado (identidad de Renault, ver bot/flow/global_prompt.py).
        from bot.models import PromptVersion
        PromptVersion.todos_los_clientes.filter(cliente="astara", agente="global").delete()
        resp = self.client.post(
            "/demo/api/admin/cliente-activo", data='{"target": "astara"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
