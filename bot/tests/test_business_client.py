from datetime import date, time
from unittest.mock import patch
from django.test import TestCase
from bot.business import (
    _listar_catalogo_impl, _consultar_disponibilidad_impl, _agendar_hora_impl,
    _buscar_reserva_impl, _reagendar_hora_impl, _anular_hora_impl,
    _buscar_sucursales_cercanas_impl,
)
from bot.models import Servicio, Sucursal, Reserva


class BusinessImplTest(TestCase):
    # BusinessClient (facade con estos mismos nombres, sin "_impl"/self) se
    # borro en la Task 10 -- sin usuarios una vez migrados agendamiento.py/
    # confirmacion.py a tool-calling nativo (Tasks 9/10). Estos tests
    # llamaban antes a self.client.<metodo>(...); se adaptan a llamar
    # directo a la funcion _impl module-level para no perder la cobertura
    # real de la logica de negocio (generacion de slots, deteccion de
    # conflictos, etc.) que solo vivia aca.
    def setUp(self):
        # precio: la tool lo entrega desde el 2026-09-02 (el bot mandaba
        # "valor referencial desde $XX" porque nunca le llegaba, ver
        # docs/PENDIENTES.md #20).
        self.servicio = Servicio.objects.create(nombre="Corte", duracion_min=60, precio=89900)
        self.sucursal = Sucursal.objects.create(nombre="Centro", horario_texto="Lun-Vie 9:00-11:00")

    def test_listar_catalogo_devuelve_servicios_y_sucursales(self):
        catalogo = _listar_catalogo_impl()
        self.assertEqual(
            catalogo["servicios"],
            [{"id": self.servicio.id, "nombre": "Corte", "duracion_min": 60,
              "precio": 89900, "precio_formateado": "$89.900"}],
        )
        self.assertEqual(catalogo["sucursales"][0]["nombre"], "Centro")

    def test_listar_catalogo_sin_categoria_devuelve_todas_las_sucursales(self):
        Sucursal.objects.create(nombre="Solo Ventas", categorias=["ventas"])
        Sucursal.objects.create(nombre="Solo Servicio", categorias=["servicio_tecnico"])
        catalogo = _listar_catalogo_impl()
        nombres = {s["nombre"] for s in catalogo["sucursales"]}
        self.assertEqual(nombres, {"Centro", "Solo Ventas", "Solo Servicio"})

    def test_listar_catalogo_con_categoria_filtra(self):
        Sucursal.objects.create(nombre="Solo Ventas", categorias=["ventas"])
        Sucursal.objects.create(nombre="Solo Servicio", categorias=["servicio_tecnico"])
        catalogo = _listar_catalogo_impl(categoria="servicio_tecnico")
        nombres = {s["nombre"] for s in catalogo["sucursales"]}
        # "Centro" (del setUp, sin categorias) siempre visible pese al filtro.
        self.assertEqual(nombres, {"Centro", "Solo Servicio"})

    def test_buscar_sucursales_cercanas_con_categoria_filtra(self):
        self.sucursal.latitud, self.sucursal.longitud = -33.45, -70.66
        self.sucursal.save()
        Sucursal.objects.create(
            nombre="Solo Servicio Cercana", categorias=["servicio_tecnico"],
            latitud=-33.46, longitud=-70.67,
        )
        Sucursal.objects.create(
            nombre="Solo Ventas Cercana", categorias=["ventas"],
            latitud=-33.44, longitud=-70.65,
        )
        with patch("bot.business.geocoding.geocodificar_direccion", return_value=(-33.45, -70.66)):
            resultado = _buscar_sucursales_cercanas_impl("Las Condes", categoria="servicio_tecnico")
        nombres = {s["nombre"] for s in resultado["sucursales"]}
        self.assertIn("Centro", nombres)  # sin categorias, siempre visible
        self.assertIn("Solo Servicio Cercana", nombres)
        self.assertNotIn("Solo Ventas Cercana", nombres)

    def test_consultar_disponibilidad_genera_slots_segun_horario_y_duracion(self):
        slots = _consultar_disponibilidad_impl(self.servicio.id, self.sucursal.id, "2026-08-01")
        self.assertEqual(slots, [{"hora": "09:00"}, {"hora": "10:00"}])

    def test_consultar_disponibilidad_excluye_horas_ya_reservadas(self):
        Reserva.objects.create(
            codigo="ABC123", contacto="56900000000", servicio=self.servicio, sucursal=self.sucursal,
            fecha=date(2026, 8, 1), hora=time(9, 0), estado="activa",
        )
        slots = _consultar_disponibilidad_impl(self.servicio.id, self.sucursal.id, "2026-08-01")
        self.assertEqual(slots, [{"hora": "10:00"}])

    def test_consultar_disponibilidad_con_id_inexistente_devuelve_lista_vacia(self):
        # El LLM puede alucinar un servicio_id/sucursal_id que no existe en el
        # catalogo — nunca debe reventar la conversacion completa, solo
        # reportar "sin horas" (lista vacia) para que el agente lo maneje.
        slots = _consultar_disponibilidad_impl(9999, self.sucursal.id, "2026-08-01")
        self.assertEqual(slots, [])

    def test_agendar_hora_crea_reserva_y_devuelve_codigo(self):
        result = _agendar_hora_impl(
            self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222", nombre="Ana",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(Reserva.objects.filter(codigo=result["codigo"]).exists())

    def test_agendar_hora_rechaza_horario_ya_ocupado(self):
        _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        result = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56933334444")
        self.assertFalse(result["ok"])

    def test_agendar_hora_con_id_inexistente_no_revienta(self):
        result = _agendar_hora_impl(9999, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        self.assertFalse(result["ok"])

    def test_agendar_hora_ante_carrera_con_otra_reserva_no_revienta(self):
        # El check-then-create de _agendar_hora_impl tiene una ventana de
        # carrera: dos conversaciones concurrentes pueden pasar el .exists()
        # antes de que la primera haga el .create(). Se simula forzando el
        # .exists() a False (como si la reserva conflictiva se hubiera creado
        # justo despues del check) mientras la fila ya existe de verdad en la
        # BD -- el UniqueConstraint del modelo debe frenar el segundo
        # .create() y _agendar_hora_impl debe atraparlo, no dejar escapar el
        # IntegrityError crudo hacia el ToolNode.
        Reserva.objects.create(
            codigo="EXIST1", contacto="56900000000", servicio=self.servicio, sucursal=self.sucursal,
            fecha=date(2026, 8, 1), hora=time(9, 0), estado="activa",
        )
        with patch("bot.business.agendamiento.Reserva.objects.filter") as mock_filter:
            mock_filter.return_value.exists.return_value = False
            result = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        self.assertFalse(result["ok"])

    def test_agendar_hora_si_no_puede_generar_codigo_unico_no_revienta(self):
        # _generar_codigo() puede lanzar RuntimeError tras 10 intentos
        # fallidos (estadisticamente casi imposible, 36**6 combinaciones,
        # pero rompia la convencion del resto del codigo de nunca dejar
        # escapar una excepcion cruda hacia el ToolNode).
        with patch("bot.business.agendamiento._generar_codigo", side_effect=RuntimeError("no se pudo generar un codigo unico")):
            result = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        self.assertFalse(result["ok"])

    def test_buscar_reserva_encuentra_por_codigo(self):
        creada = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        result = _buscar_reserva_impl(creada["codigo"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["servicio"], "Corte")

    def test_buscar_reserva_inexistente_devuelve_ok_false(self):
        result = _buscar_reserva_impl("NOEXISTE")
        self.assertFalse(result["ok"])

    def test_reagendar_hora_mueve_la_reserva(self):
        creada = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        result = _reagendar_hora_impl(creada["codigo"], "2026-08-02", "10:00")
        self.assertTrue(result["ok"])
        reserva = Reserva.objects.get(codigo=creada["codigo"])
        self.assertEqual(reserva.fecha, date(2026, 8, 2))

    def test_reagendar_hora_ante_carrera_con_otra_reserva_no_revienta(self):
        creada = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        Reserva.objects.create(
            codigo="EXIST2", contacto="56900000000", servicio=self.servicio, sucursal=self.sucursal,
            fecha=date(2026, 8, 2), hora=time(10, 0), estado="activa",
        )
        with patch("bot.business.agendamiento.Reserva.objects.filter") as mock_filter:
            mock_filter.return_value.get.return_value = Reserva.objects.get(codigo=creada["codigo"])
            mock_filter.return_value.exclude.return_value.exists.return_value = False
            result = _reagendar_hora_impl(creada["codigo"], "2026-08-02", "10:00")
        self.assertFalse(result["ok"])

    def test_anular_hora_marca_cancelada(self):
        creada = _agendar_hora_impl(self.servicio.id, self.sucursal.id, "2026-08-01", "09:00", "56911112222")
        result = _anular_hora_impl(creada["codigo"])
        self.assertTrue(result["ok"])
        reserva = Reserva.objects.get(codigo=creada["codigo"])
        self.assertEqual(reserva.estado, "cancelada")


class BusinessToolsTest(TestCase):
    def test_agendar_hora_es_un_tool_con_el_nombre_correcto(self):
        from bot.business import agendar_hora
        self.assertEqual(agendar_hora.name, "agendar_hora")

    def test_registrar_no_contactar_es_un_tool_que_solo_expone_motivo(self):
        from bot.business import registrar_no_contactar
        self.assertEqual(registrar_no_contactar.name, "registrar_no_contactar")
        self.assertEqual(set(registrar_no_contactar.args.keys()), {"motivo"})

    def test_los_6_tools_existen_con_nombres_correctos(self):
        from bot.business import (
            listar_catalogo, consultar_disponibilidad, agendar_hora,
            buscar_reserva, reagendar_hora, anular_hora,
        )
        nombres = {t.name for t in [
            listar_catalogo, consultar_disponibilidad, agendar_hora,
            buscar_reserva, reagendar_hora, anular_hora,
        ]}
        self.assertEqual(nombres, {
            "listar_catalogo", "consultar_disponibilidad", "agendar_hora",
            "buscar_reserva", "reagendar_hora", "anular_hora",
        })

    def test_agendar_hora_impl_delega_correctamente(self):
        from bot.business import _agendar_hora_impl
        servicio = Servicio.objects.create(nombre="Corte", duracion_min=30)
        sucursal = Sucursal.objects.create(nombre="Centro", horario_texto="9:00-18:00")
        resultado = _agendar_hora_impl(
            servicio_id=servicio.id, sucursal_id=sucursal.id,
            fecha="2026-09-01", hora="10:00", contacto="56911112222", nombre="Juan",
        )
        self.assertTrue(resultado["ok"])
        self.assertIn("codigo", resultado)

    def test_registrar_no_contactar_impl_con_wa_id_deja_el_opt_out(self):
        from bot.business import _registrar_no_contactar_impl
        from bot.models import esta_optout
        resultado = _registrar_no_contactar_impl(wa_id="56911112222", motivo="no quiere mas mensajes")
        self.assertTrue(resultado["ok"])
        self.assertTrue(esta_optout("56911112222"))

    def test_registrar_no_contactar_impl_sin_wa_id_falla(self):
        from bot.business import _registrar_no_contactar_impl
        resultado = _registrar_no_contactar_impl(motivo="no quiere mas mensajes")
        self.assertFalse(resultado["ok"])


class RegistrarConsentimientoImplTest(TestCase):
    # Sin cobertura directa hasta ahora (docs/PENDIENTES.md, "Cobertura de
    # tests desigual entre dominios de tools") -- solo se ejercitaba
    # indirecto via test_agents.py/test_graph.py, que mockean el ToolMessage
    # y no la logica real (el guard de wa_id vacio, el registro en BD).
    def test_con_wa_id_registra_el_consentimiento_otorgado(self):
        from bot.business.compliance import _registrar_consentimiento_impl
        from bot.models import tiene_consentimiento
        resultado = _registrar_consentimiento_impl(
            wa_id="56911112222", otorgado=True, motivo="acepto seguimiento comercial",
        )
        self.assertTrue(resultado["ok"])
        self.assertTrue(tiene_consentimiento("56911112222"))

    def test_con_wa_id_registra_la_revocacion(self):
        from bot.business.compliance import _registrar_consentimiento_impl
        from bot.models import tiene_consentimiento
        resultado = _registrar_consentimiento_impl(
            wa_id="56911112222", otorgado=False, motivo="ya no quiere seguimiento",
        )
        self.assertTrue(resultado["ok"])
        self.assertFalse(tiene_consentimiento("56911112222"))

    def test_sin_wa_id_falla_sin_registrar_nada(self):
        from bot.business.compliance import _registrar_consentimiento_impl
        from bot.models import Consentimiento
        resultado = _registrar_consentimiento_impl(wa_id="", otorgado=True)
        self.assertFalse(resultado["ok"])
        self.assertEqual(Consentimiento.objects.count(), 0)


class GeocodificarDireccionTest(TestCase):
    # Sin cobertura directa hasta ahora (docs/PENDIENTES.md, misma nota que
    # RegistrarConsentimientoImplTest) -- solo se mockeaba como dependencia
    # de _buscar_sucursales_cercanas_impl, nunca se ejercito su propia
    # logica (guard de API key, parseo de la respuesta de Google, manejo de
    # status distinto de "OK" y de errores de red).
    def test_sin_direccion_devuelve_none(self):
        from bot.business.geocoding import geocodificar_direccion
        self.assertIsNone(geocodificar_direccion(""))

    def test_sin_api_key_devuelve_none_sin_llamar_a_la_api(self):
        from bot.business.geocoding import geocodificar_direccion
        with self.settings(GOOGLE_MAPS_API_KEY=""):
            with patch("bot.business.geocoding.httpx.get") as mock_get:
                self.assertIsNone(geocodificar_direccion("Las Condes"))
                mock_get.assert_not_called()

    def test_status_ok_devuelve_lat_lng(self):
        from bot.business.geocoding import geocodificar_direccion
        respuesta = {"status": "OK", "results": [{"geometry": {"location": {"lat": -33.45, "lng": -70.66}}}]}
        with self.settings(GOOGLE_MAPS_API_KEY="fake-key"):
            with patch("bot.business.geocoding.httpx.get") as mock_get:
                mock_get.return_value.json.return_value = respuesta
                self.assertEqual(geocodificar_direccion("Las Condes"), (-33.45, -70.66))

    def test_status_distinto_de_ok_devuelve_none(self):
        from bot.business.geocoding import geocodificar_direccion
        with self.settings(GOOGLE_MAPS_API_KEY="fake-key"):
            with patch("bot.business.geocoding.httpx.get") as mock_get:
                mock_get.return_value.json.return_value = {"status": "ZERO_RESULTS", "results": []}
                self.assertIsNone(geocodificar_direccion("direccion inexistente"))

    def test_error_de_red_devuelve_none(self):
        import httpx
        from bot.business.geocoding import geocodificar_direccion
        with self.settings(GOOGLE_MAPS_API_KEY="fake-key"):
            with patch("bot.business.geocoding.httpx.get", side_effect=httpx.ConnectError("boom")):
                self.assertIsNone(geocodificar_direccion("Las Condes"))


class CrearCasoImplTest(TestCase):
    # _crear_caso_impl no tenia ningun test directo (solo indirecto via
    # BusinessActionNodeCrearCasoTest en test_graph.py, que no ejercita la
    # idempotencia). Mismo patron de dedup que _crear_lead_impl
    # (bot/business/ventas.py): bot/flow/graph.py::_filtrar_acciones_repetidas
    # dedupea por args EXACTOS -- un reintento del LLM en el mismo turno con
    # "resumen" reformulado ya no matchea ese dedup y llega hasta aca. Sin
    # esto, un reclamo mencionado en dos turnos puede abrir dos tickets
    # identicos para postventa.
    def setUp(self):
        from bot.models import Conversation
        self.conv = Conversation.objects.create(wa_id="56911112222")

    def test_crea_el_caso_y_devuelve_ok_y_caso_id(self):
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident
        resultado = _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="motor con ruido raro")
        self.assertTrue(resultado["ok"])
        caso = Incident.objects.get(pk=resultado["caso_id"])
        self.assertEqual(caso.kind, "garantia")
        self.assertEqual(caso.conversation_id, self.conv.id)

    def test_reintento_en_el_mismo_turno_con_resumen_reformulado_no_duplica(self):
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident
        primero = _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="motor con ruido raro")
        segundo = _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="el motor hace un ruido extranio al arrancar")
        self.assertTrue(primero["ok"])
        self.assertTrue(segundo["ok"])
        self.assertEqual(primero["caso_id"], segundo["caso_id"])
        self.assertEqual(Incident.objects.filter(conversation=self.conv, kind="garantia").count(), 1)

    def test_tipo_distinto_igual_crea_un_caso_nuevo(self):
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident
        _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="motor con ruido raro")
        _crear_caso_impl(wa_id="56911112222", tipo="repuesto", resumen="necesita parachoques")
        self.assertEqual(Incident.objects.filter(conversation=self.conv).count(), 2)

    def test_otro_conversacion_distinta_igual_crea_un_caso_nuevo(self):
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Conversation, Incident
        Conversation.objects.create(wa_id="56933334444")
        _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="motor con ruido raro")
        _crear_caso_impl(wa_id="56933334444", tipo="garantia", resumen="motor con ruido raro")
        self.assertEqual(Incident.objects.filter(kind="garantia").count(), 2)

    def test_fuera_de_la_ventana_de_dedup_crea_un_caso_nuevo(self):
        from datetime import timedelta
        from django.utils import timezone
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident
        primero = _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="motor con ruido raro")
        Incident.objects.filter(pk=primero["caso_id"]).update(created_at=timezone.now() - timedelta(minutes=10))
        segundo = _crear_caso_impl(wa_id="56911112222", tipo="garantia", resumen="volvio a preguntar dias despues")
        self.assertNotEqual(primero["caso_id"], segundo["caso_id"])
        self.assertEqual(Incident.objects.filter(conversation=self.conv, kind="garantia").count(), 2)

    def test_tipo_invalido_distinto_no_deduplica_entre_si(self):
        # Dos tipos invalidos distintos caen ambos en kind="otro" -- no deben
        # deduplicarse entre si solo por compartir ese bucket.
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident
        _crear_caso_impl(wa_id="56911112222", tipo="factura", resumen="pide factura")
        _crear_caso_impl(wa_id="56911112222", tipo="devolucion", resumen="pide devolucion")
        self.assertEqual(Incident.objects.filter(conversation=self.conv, kind="otro").count(), 2)

    def test_sin_wa_id_falla(self):
        from bot.business.compliance import _crear_caso_impl
        resultado = _crear_caso_impl(wa_id="", tipo="garantia", resumen="motor con ruido raro")
        self.assertFalse(resultado["ok"])


class RegistrarRespuestaEncuestaServicioTecnicoTest(TestCase):
    def _crear_campaign_send(self, wa_id="56911112222"):
        from bot.models import CampaignSend
        return CampaignSend.objects.create(contacto=wa_id, campaign_type="encuesta_servicio_tecnico", template="x")

    def test_numero_pregunta_invalido_no_persiste_nada(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=5, respuesta="8",
        )
        self.assertFalse(resultado["ok"])

    def test_sin_campaign_send_activo_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56900000000", numero_pregunta=1, respuesta="8",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_1_fuera_de_rango_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="11",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_1_no_numerica_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="buena",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_1_valida_persiste_y_no_marca_completa(self):
        from bot.models import CampaignSend, EncuestaServicioTecnico
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="9",
        )
        self.assertTrue(resultado["ok"])
        self.assertFalse(resultado["encuesta_completa"])
        encuesta = EncuestaServicioTecnico.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p1_satisfaccion_ejecutivo, 9)
        self.assertFalse(CampaignSend.objects.get(pk=send.pk).respondido)

    def test_pregunta_3_acepta_si_no_no_se_insensible_a_mayusculas_y_acentos(self):
        from bot.models import EncuestaServicioTecnico
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="No Sé",
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(EncuestaServicioTecnico.objects.get(campaign_send=send).p3_trabajos_correctos, "no_se")

    def test_pregunta_3_con_valor_invalido_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="tal vez",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_4_valida_marca_completed_at_y_campaign_send_respondido(self):
        from django.utils import timezone
        from bot.models import CampaignSend, EncuestaServicioTecnico
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        send = self._crear_campaign_send()
        antes = timezone.now()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=4, respuesta="10",
        )
        self.assertTrue(resultado["ok"])
        self.assertTrue(resultado["encuesta_completa"])
        encuesta = EncuestaServicioTecnico.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p4_recomendaria, 10)
        self.assertGreaterEqual(encuesta.completed_at, antes)
        self.assertTrue(CampaignSend.objects.get(pk=send.pk).respondido)

    def test_llamadas_repetidas_para_preguntas_distintas_acumulan_en_la_misma_fila(self):
        from bot.models import EncuestaServicioTecnico
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        send = self._crear_campaign_send()
        _registrar_respuesta_encuesta_servicio_tecnico_impl(wa_id="56911112222", numero_pregunta=1, respuesta="7")
        _registrar_respuesta_encuesta_servicio_tecnico_impl(wa_id="56911112222", numero_pregunta=2, respuesta="8")
        self.assertEqual(EncuestaServicioTecnico.objects.filter(campaign_send=send).count(), 1)
        encuesta = EncuestaServicioTecnico.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p1_satisfaccion_ejecutivo, 7)
        self.assertEqual(encuesta.p2_satisfaccion_visita, 8)

    def test_tool_expone_solo_numero_pregunta_y_respuesta(self):
        from bot.business.encuestas import registrar_respuesta_encuesta_servicio_tecnico
        self.assertEqual(
            set(registrar_respuesta_encuesta_servicio_tecnico.args.keys()),
            {"numero_pregunta", "respuesta"},
        )

    def test_pregunta_invalida_no_crea_fila_en_la_bd(self):
        from bot.models import EncuestaServicioTecnico
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        self._crear_campaign_send()
        _registrar_respuesta_encuesta_servicio_tecnico_impl(wa_id="56911112222", numero_pregunta=1, respuesta="99")
        self.assertFalse(EncuestaServicioTecnico.objects.exists())

    def test_pregunta_1_acepta_numero_dentro_de_una_frase(self):
        from bot.models import EncuestaServicioTecnico
        from bot.business.encuestas import _registrar_respuesta_encuesta_servicio_tecnico_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_servicio_tecnico_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="yo diria que un 8",
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(EncuestaServicioTecnico.objects.get(campaign_send=send).p1_satisfaccion_ejecutivo, 8)


class RegistrarRespuestaEncuestaVentaAutoNuevoTest(TestCase):
    def _crear_campaign_send(self, wa_id="56911112222"):
        from bot.models import CampaignSend
        return CampaignSend.objects.create(contacto=wa_id, campaign_type="encuesta_venta_auto_nuevo", template="x")

    def test_numero_pregunta_invalido_no_persiste_nada(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=6, respuesta="si",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_1_nota_alta_no_requiere_motivo(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="9",
        )
        self.assertTrue(resultado["ok"])
        encuesta = EncuestaVentaAutoNuevo.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p1_nota_general, 9)
        self.assertEqual(encuesta.p1_motivo_nota_baja, "")

    def test_pregunta_1_nota_baja_sin_motivo_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="4",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_1_nota_baja_con_motivo_persiste_ambos(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="4",
            motivo_texto_libre="demoraron mucho en la entrega",
        )
        self.assertTrue(resultado["ok"])
        encuesta = EncuestaVentaAutoNuevo.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p1_nota_general, 4)
        self.assertEqual(encuesta.p1_motivo_nota_baja, "demoraron mucho en la entrega")

    def test_pregunta_2_si_no_requiere_motivo(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=2, respuesta="si",
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(EncuestaVentaAutoNuevo.objects.get(campaign_send=send).p2_dejo_parte_pago, "si")

    def test_pregunta_2_no_sin_motivo_codigo_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=2, respuesta="no",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_2_no_con_motivo_codigo_invalido_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=2, respuesta="no", motivo_codigo="no_le_gusto",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_2_no_otro_sin_texto_libre_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=2, respuesta="no", motivo_codigo="otro",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_2_no_otro_con_texto_libre_persiste_ambos(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=2, respuesta="no",
            motivo_codigo="otro", motivo_texto_libre="lo doné a un familiar",
        )
        self.assertTrue(resultado["ok"])
        encuesta = EncuestaVentaAutoNuevo.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p2_motivo_no, "otro")
        self.assertEqual(encuesta.p2_motivo_otro_texto, "lo doné a un familiar")

    def test_pregunta_2_no_con_codigo_cerrado_valido_no_requiere_texto_libre(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=2, respuesta="no", motivo_codigo="precio_tasacion",
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(EncuestaVentaAutoNuevo.objects.get(campaign_send=send).p2_motivo_no, "precio_tasacion")

    def test_pregunta_3_observaciones_se_guardan_independiente_de_la_respuesta(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="si", observaciones="todo excelente",
        )
        self.assertTrue(resultado["ok"])
        encuesta = EncuestaVentaAutoNuevo.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p3_firmo_checklist, "si")
        self.assertEqual(encuesta.p3_observaciones, "todo excelente")
        self.assertEqual(encuesta.p3_motivo_no, "")

    def test_pregunta_3_no_sin_motivo_codigo_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="no",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_3_no_con_motivo_codigo_valido_persiste(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="no", motivo_codigo="no_quiso_o_sin_tiempo",
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(EncuestaVentaAutoNuevo.objects.get(campaign_send=send).p3_motivo_no, "no_quiso_o_sin_tiempo")

    def test_pregunta_4_y_5_no_requieren_campos_auxiliares(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        r4 = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(wa_id="56911112222", numero_pregunta=4, respuesta="no")
        self.assertTrue(r4["ok"])
        self.assertFalse(r4["encuesta_completa"])
        r5 = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(wa_id="56911112222", numero_pregunta=5, respuesta="si")
        self.assertTrue(r5["ok"])
        self.assertTrue(r5["encuesta_completa"])
        encuesta = EncuestaVentaAutoNuevo.objects.get(campaign_send=send)
        self.assertEqual(encuesta.p4_informaron_garantia, "no")
        self.assertEqual(encuesta.p5_informaron_mantenciones, "si")

    def test_pregunta_5_valida_marca_campaign_send_respondido(self):
        from bot.models import CampaignSend
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        _registrar_respuesta_encuesta_venta_auto_nuevo_impl(wa_id="56911112222", numero_pregunta=5, respuesta="si")
        self.assertTrue(CampaignSend.objects.get(pk=send.pk).respondido)

    def test_respuesta_invalida_en_pregunta_2_a_5_devuelve_ok_false(self):
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=4, respuesta="tal vez",
        )
        self.assertFalse(resultado["ok"])

    def test_pregunta_invalida_no_crea_fila_en_la_bd(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        self._crear_campaign_send()
        _registrar_respuesta_encuesta_venta_auto_nuevo_impl(wa_id="56911112222", numero_pregunta=1, respuesta="99")
        self.assertFalse(EncuestaVentaAutoNuevo.objects.exists())

    def test_tool_expone_los_5_parametros_esperados(self):
        from bot.business.encuestas import registrar_respuesta_encuesta_venta_auto_nuevo
        self.assertEqual(
            set(registrar_respuesta_encuesta_venta_auto_nuevo.args.keys()),
            {"numero_pregunta", "respuesta", "motivo_codigo", "motivo_texto_libre", "observaciones"},
        )

    def test_pregunta_1_acepta_numero_dentro_de_una_frase(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        resultado = _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=1, respuesta="8 de 10 diria yo",
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(EncuestaVentaAutoNuevo.objects.get(campaign_send=send).p1_nota_general, 8)

    def test_pregunta_3_no_borra_observacion_previa_si_se_reenvia_sin_observacion_nueva(self):
        from bot.models import EncuestaVentaAutoNuevo
        from bot.business.encuestas import _registrar_respuesta_encuesta_venta_auto_nuevo_impl
        send = self._crear_campaign_send()
        _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="si", observaciones="todo excelente",
        )
        _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
            wa_id="56911112222", numero_pregunta=3, respuesta="si",
        )
        self.assertEqual(EncuestaVentaAutoNuevo.objects.get(campaign_send=send).p3_observaciones, "todo excelente")
