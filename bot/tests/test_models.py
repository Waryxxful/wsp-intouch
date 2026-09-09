from datetime import date, time, timedelta
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from bot.models import (
    Conversation, Message, Setting, Incident, CampaignSend, get_setting, ImagenConvertida,
    ScrapedPage, ScrapeRun, ScrapingSource, OptOut, esta_optout, registrar_optout,
    Consentimiento, registrar_consentimiento, tiene_consentimiento, registrar_incidente,
    crear_caso, Servicio, Sucursal, Reserva,
)


class ConversationModelTest(TestCase):
    def test_get_flow_devuelve_dict_vacio_por_default(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self.assertEqual(conv.get_flow(), {})

    def test_set_flow_y_get_flow_hacen_roundtrip(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        conv.set_flow({"paso": "confirmando"})
        conv.save()
        conv.refresh_from_db()
        self.assertEqual(conv.get_flow(), {"paso": "confirmando"})

    def test_set_flow_normaliza_antes_de_guardar(self):
        # Ultima red antes de persistir: los merges de flow_data viven en
        # graph.py y cola_envio.py, pero los dos terminan aca.
        conv = Conversation.objects.create(wa_id="56911112222")
        conv.set_flow({"comuna": "", "monto_pie": "$8.000.000", "plazo_actual": 24,
                       "plazo": "12"})
        conv.save()
        conv.refresh_from_db()
        self.assertEqual(conv.get_flow(), {"pie_disponible": 8000000, "plazo": 12})

    def test_get_flow_limpia_lo_que_ya_estaba_guardado_sucio(self):
        # Las conversaciones anteriores a la normalizacion (la 29 en
        # produccion) ya tienen basura en la BD; sin esto se la seguirian
        # inyectando al system prompt del especialista hasta el proximo write.
        conv = Conversation.objects.create(wa_id="56911112222")
        Conversation.objects.filter(pk=conv.pk).update(
            flow_data={"plazo_actual": 24, "plazo": "12", "comuna": None})
        conv.refresh_from_db()
        self.assertEqual(conv.get_flow(), {"plazo": 12})

    def test_active_agent_default_vacio(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self.assertEqual(conv.active_agent, "")


class MessageModelTest(TestCase):
    def test_mensaje_queda_ligado_a_la_conversacion(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        msg = Message.objects.create(conversation=conv, role="user", content="hola", wa_msg_id="wamid.1")
        self.assertEqual(conv.messages.count(), 1)
        self.assertEqual(msg.role, "user")

    def test_wa_msg_id_duplicado_no_vacio_viola_el_constraint(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="hola", wa_msg_id="wamid.dup")
        with self.assertRaises(IntegrityError):
            Message.objects.create(conversation=conv, role="user", content="hola de nuevo", wa_msg_id="wamid.dup")

    def test_varios_mensajes_con_wa_msg_id_vacio_no_violan_el_constraint(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="assistant", content="uno")
        Message.objects.create(conversation=conv, role="assistant", content="dos")
        self.assertEqual(conv.messages.count(), 2)


class SettingModelTest(TestCase):
    def test_get_setting_devuelve_default_si_no_existe(self):
        self.assertEqual(get_setting("bot_global_on", "true"), "true")

    def test_get_setting_devuelve_valor_guardado(self):
        Setting.objects.create(key="welcome_message", value="hola!")
        self.assertEqual(get_setting("welcome_message", "default"), "hola!")


class IncidentModelTest(TestCase):
    def test_incident_default_status_abierto(self):
        inc = Incident.objects.create(kind="llm_sin_business_action", context={"turno": 3})
        self.assertEqual(inc.status, "abierto")


class CampaignSendModelTest(TestCase):
    def test_campaign_send_default_no_respondido(self):
        send = CampaignSend.objects.create(contacto="56911112222", campaign_type="confirmar_agenda", template="tpl_confirmar")
        self.assertFalse(send.respondido)


class OptOutModelTest(TestCase):
    def test_esta_optout_false_por_defecto(self):
        self.assertFalse(esta_optout("56911112222"))

    def test_registrar_optout_queda_marcado(self):
        registrar_optout("56911112222", motivo="no quiere mensajes comerciales")
        self.assertTrue(esta_optout("56911112222"))

    def test_registrar_optout_enlaza_la_conversacion_si_existe(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        optout = registrar_optout("56911112222", motivo="no me contacten mas")
        self.assertEqual(optout.conversation_id, conv.id)

    def test_registrar_optout_es_idempotente(self):
        registrar_optout("56911112222", motivo="motivo 1")
        registrar_optout("56911112222", motivo="motivo 2")
        self.assertEqual(OptOut.objects.filter(wa_id="56911112222").count(), 1)


class ReservaModelTest(TestCase):
    def setUp(self):
        self.servicio = Servicio.objects.create(nombre="Corte", duracion_min=60)
        self.sucursal = Sucursal.objects.create(nombre="Centro", horario_texto="9:00-18:00")

    def test_unique_constraint_evita_doble_reserva_activa_del_mismo_cupo(self):
        Reserva.objects.create(
            codigo="AAA111", contacto="56911112222", servicio=self.servicio, sucursal=self.sucursal,
            fecha=date(2026, 8, 1), hora=time(9, 0), estado="activa",
        )
        with self.assertRaises(IntegrityError):
            Reserva.objects.create(
                codigo="BBB222", contacto="56933334444", servicio=self.servicio, sucursal=self.sucursal,
                fecha=date(2026, 8, 1), hora=time(9, 0), estado="activa",
            )

    def test_unique_constraint_no_aplica_a_reservas_canceladas(self):
        # La constraint es condicional a estado="activa" a proposito -- un
        # cupo cancelado debe poder volver a ocuparse.
        Reserva.objects.create(
            codigo="AAA111", contacto="56911112222", servicio=self.servicio, sucursal=self.sucursal,
            fecha=date(2026, 8, 1), hora=time(9, 0), estado="cancelada",
        )
        Reserva.objects.create(
            codigo="BBB222", contacto="56933334444", servicio=self.servicio, sucursal=self.sucursal,
            fecha=date(2026, 8, 1), hora=time(9, 0), estado="activa",
        )
        self.assertEqual(Reserva.objects.filter(fecha=date(2026, 8, 1), hora=time(9, 0)).count(), 2)


class CustomSpecialistModelTest(TestCase):
    def test_crear_y_representar_como_string(self):
        from bot.models import CustomSpecialist
        s = CustomSpecialist.objects.create(
            slug="envios", label="Envíos", descripcion="Responde sobre el estado de un envio.",
        )
        self.assertEqual(str(s), "Envíos")

    def test_slug_es_unico(self):
        from bot.models import CustomSpecialist
        from django.db import IntegrityError
        CustomSpecialist.objects.create(slug="envios", label="Envíos", descripcion="d")
        with self.assertRaises(IntegrityError):
            CustomSpecialist.objects.create(slug="envios", label="Envíos 2", descripcion="d2")

    def test_ordering_por_label(self):
        from bot.models import CustomSpecialist
        CustomSpecialist.objects.create(slug="z", label="Zeta", descripcion="d")
        CustomSpecialist.objects.create(slug="a", label="Alfa", descripcion="d")
        labels = list(CustomSpecialist.objects.values_list("label", flat=True))
        self.assertEqual(labels, ["Alfa", "Zeta"])


class PromptVersionHelpersTest(TestCase):
    def test_get_active_prompt_sin_versiones_devuelve_vacio(self):
        from bot.models import get_active_prompt
        self.assertEqual(get_active_prompt("agendamiento"), "")

    def test_save_prompt_version_crea_version_activa(self):
        from bot.models import save_prompt_version, get_active_prompt, PromptVersion
        save_prompt_version("agendamiento", "prompt v1")
        self.assertEqual(get_active_prompt("agendamiento"), "prompt v1")
        self.assertEqual(PromptVersion.objects.filter(agente="agendamiento").count(), 1)

    def test_save_prompt_version_desactiva_la_version_anterior(self):
        from bot.models import save_prompt_version, get_active_prompt, PromptVersion
        save_prompt_version("agendamiento", "prompt v1")
        save_prompt_version("agendamiento", "prompt v2")
        self.assertEqual(get_active_prompt("agendamiento"), "prompt v2")
        self.assertEqual(PromptVersion.objects.filter(agente="agendamiento", activa=True).count(), 1)
        self.assertEqual(PromptVersion.objects.filter(agente="agendamiento").count(), 2)

    def test_deactivate_prompt_deja_sin_version_activa(self):
        from bot.models import save_prompt_version, deactivate_prompt, get_active_prompt
        save_prompt_version("faq", "prompt custom")
        deactivate_prompt("faq")
        self.assertEqual(get_active_prompt("faq"), "")

    def test_agentes_distintos_no_se_pisan_entre_si(self):
        from bot.models import save_prompt_version, get_active_prompt
        save_prompt_version("agendamiento", "prompt agendamiento")
        save_prompt_version("custom:envios", "prompt envios")
        self.assertEqual(get_active_prompt("agendamiento"), "prompt agendamiento")
        self.assertEqual(get_active_prompt("custom:envios"), "prompt envios")

    def test_ordering_mas_reciente_primero(self):
        from bot.models import save_prompt_version, PromptVersion
        save_prompt_version("faq", "v1")
        save_prompt_version("faq", "v2")
        prompts = list(PromptVersion.objects.filter(agente="faq").values_list("prompt", flat=True))
        self.assertEqual(prompts, ["v2", "v1"])


class ScrapedPageModelTest(TestCase):
    def test_imagenes_default_lista_vacia(self):
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        pagina = ScrapedPage.objects.create(run=run, url="https://renault.cl/modelo/koleos/", texto="koleos")
        self.assertEqual(pagina.imagenes, [])

    def test_url_acepta_mas_de_200_caracteres(self):
        # Bug real 2026-08-31 (docs/PENDIENTES.md): un slug de blog largo
        # descubierto al subir max_pages abortaba la corrida ENTERA con un
        # error de SQL Server ("String or binary data would be truncated")
        # a mitad del loop de ScrapedPage, antes de llegar a actualizar
        # VehiculoCatalogo -- max_length subido de 200 (default) a 500.
        source = ScrapingSource.objects.create(url="https://astararetail.cl/")
        run = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        url_larga = "https://astararetail.cl/2022/09/29/" + "a" * 250 + "/"
        pagina = ScrapedPage.objects.create(run=run, url=url_larga, texto="hola")
        pagina.refresh_from_db()
        self.assertEqual(pagina.url, url_larga)

    def test_imagenes_guarda_lista_de_dicts_url_alt(self):
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        pagina = ScrapedPage.objects.create(
            run=run, url="https://renault.cl/modelo/koleos/", texto="koleos",
            imagenes=[{"url": "https://renault.cl/koleos.webp", "alt": "techno"}],
        )
        pagina.refresh_from_db()
        self.assertEqual(pagina.imagenes, [{"url": "https://renault.cl/koleos.webp", "alt": "techno"}])


class ImagenConvertidaModelTest(TestCase):
    def test_url_original_es_unica(self):
        ImagenConvertida.objects.create(url_original="https://renault.cl/koleos.webp")
        with self.assertRaises(Exception):
            ImagenConvertida.objects.create(url_original="https://renault.cl/koleos.webp")

    def test_creado_en_se_autocompleta(self):
        imagen = ImagenConvertida.objects.create(url_original="https://renault.cl/koleos.webp")
        self.assertIsNotNone(imagen.creado_en)


class ConversationCamposNuevosTest(TestCase):
    def test_rut_lead_class_stage_default_vacio(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self.assertEqual(conv.rut, "")
        self.assertEqual(conv.lead_class, "")
        self.assertEqual(conv.stage, "")

    def test_lead_class_acepta_valores_del_choices(self):
        conv = Conversation.objects.create(wa_id="56911112222", lead_class="HOT")
        conv.refresh_from_db()
        self.assertEqual(conv.lead_class, "HOT")

    def test_stage_acepta_valores_del_choices(self):
        conv = Conversation.objects.create(wa_id="56911112222", stage="cotizacion")
        conv.refresh_from_db()
        self.assertEqual(conv.stage, "cotizacion")


class ConsentimientoModelTest(TestCase):
    def test_tiene_consentimiento_none_por_defecto(self):
        self.assertIsNone(tiene_consentimiento("56911112222"))

    def test_registrar_consentimiento_otorgado_queda_reflejado(self):
        registrar_consentimiento("56911112222", True, motivo="acepto seguimiento comercial")
        self.assertTrue(tiene_consentimiento("56911112222"))

    def test_registrar_consentimiento_revocado_despues_de_otorgado(self):
        registrar_consentimiento("56911112222", True, motivo="acepto")
        registrar_consentimiento("56911112222", False, motivo="ahora no quiere")
        self.assertFalse(tiene_consentimiento("56911112222"))

    def test_es_historial_no_se_pisa(self):
        registrar_consentimiento("56911112222", True)
        registrar_consentimiento("56911112222", False)
        self.assertEqual(Consentimiento.objects.filter(wa_id="56911112222").count(), 2)

    def test_registrar_consentimiento_enlaza_la_conversacion_si_existe(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        c = registrar_consentimiento("56911112222", True)
        self.assertEqual(c.conversation_id, conv.id)


class RegistrarIncidenteTest(TestCase):
    def test_registrar_incidente_crea_fila_abierta_con_contexto(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        inc = registrar_incidente(conv, "handoff", context={"reason": "pide hablar con humano"})
        self.assertEqual(inc.status, "abierto")
        self.assertEqual(inc.context["reason"], "pide hablar con humano")
        self.assertEqual(inc.conversation_id, conv.id)

    def test_dedupea_mismo_kind_dentro_de_la_ventana(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        primero = registrar_incidente(conv, "handoff", context={"reason": "test drive"})
        segundo = registrar_incidente(conv, "handoff", context={"reason": "test drive reformulado"})
        self.assertEqual(primero.id, segundo.id)
        self.assertEqual(Incident.objects.filter(conversation=conv, kind="handoff").count(), 1)

    def test_no_dedupea_kinds_distintos(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        registrar_incidente(conv, "handoff", context={})
        registrar_incidente(conv, "revision_requerida", context={})
        self.assertEqual(Incident.objects.filter(conversation=conv).count(), 2)

    def test_dedupea_mientras_siga_abierto_sin_importar_el_tiempo_transcurrido(self):
        # Bug real confirmado en produccion 2026-08-31 (docs/PENDIENTES.md,
        # auditoria conversacion 14): una sola conversacion real genero 6
        # Incident de kind=handoff en 52 minutos -- la ventana de 5 min
        # (fix anterior, pensado para reintentos casi inmediatos del LLM)
        # no cubre un lead evolucionando mas lento durante una conversacion
        # larga, y el equipo comercial termina con 6 notificaciones
        # separadas para un solo cliente en curso. Mientras el Incident
        # anterior siga status="abierto", se reusa la misma fila sin
        # importar cuanto tiempo haya pasado -- el contexto se actualiza al
        # motivo mas reciente.
        conv = Conversation.objects.create(wa_id="56911112222")
        primero = registrar_incidente(conv, "handoff", context={"reason": "test drive"})
        Incident.objects.filter(pk=primero.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        segundo = registrar_incidente(conv, "handoff", context={"reason": "coordinar llamada"})
        self.assertEqual(primero.id, segundo.id)
        self.assertEqual(Incident.objects.filter(conversation=conv, kind="handoff").count(), 1)
        primero.refresh_from_db()
        self.assertEqual(primero.context["reason"], "coordinar llamada")

    def test_crea_uno_nuevo_si_el_anterior_ya_fue_cerrado(self):
        # Contraparte del test de arriba: una vez que el equipo comercial
        # marca el caso anterior revisado/cerrado, un nuevo handoff para la
        # misma conversacion SI debe abrir un Incident nuevo -- no seguir
        # deduplicando contra un caso que ya se resolvio.
        conv = Conversation.objects.create(wa_id="56911112222")
        primero = registrar_incidente(conv, "handoff", context={})
        primero.status = "cerrado"
        primero.save(update_fields=["status"])
        segundo = registrar_incidente(conv, "handoff", context={})
        self.assertNotEqual(primero.id, segundo.id)
        self.assertEqual(Incident.objects.filter(conversation=conv, kind="handoff").count(), 2)

    def test_no_dedupea_entre_conversaciones_distintas(self):
        conv1 = Conversation.objects.create(wa_id="56911112222")
        conv2 = Conversation.objects.create(wa_id="56933334444")
        registrar_incidente(conv1, "handoff", context={})
        registrar_incidente(conv2, "handoff", context={})
        self.assertEqual(Incident.objects.filter(kind="handoff").count(), 2)


class CrearCasoTest(TestCase):
    def test_tipo_valido_queda_como_kind(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        caso = crear_caso(conv, "garantia", "motor con ruido raro")
        self.assertEqual(caso.kind, "garantia")
        self.assertEqual(caso.context["resumen"], "motor con ruido raro")
        self.assertEqual(caso.conversation_id, conv.id)
        self.assertEqual(caso.status, "abierto")

    def test_tipo_invalido_cae_a_otro_sin_perder_el_dato(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        caso = crear_caso(conv, "no_existe_este_tipo", "algo raro")
        self.assertEqual(caso.kind, "otro")
        self.assertEqual(caso.context["tipo_original"], "no_existe_este_tipo")

    def test_tipo_valido_no_guarda_tipo_original(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        caso = crear_caso(conv, "repuesto", "necesita parachoques")
        self.assertNotIn("tipo_original", caso.context)


class EncuestaServicioTecnicoTest(TestCase):
    def test_se_crea_ligado_a_un_campaign_send_y_arranca_sin_respuestas(self):
        from bot.models import CampaignSend, EncuestaServicioTecnico
        send = CampaignSend.objects.create(contacto="56911112222", campaign_type="encuesta_servicio_tecnico", template="x")
        encuesta = EncuestaServicioTecnico.objects.create(campaign_send=send)
        self.assertIsNone(encuesta.p1_satisfaccion_ejecutivo)
        self.assertEqual(encuesta.p3_trabajos_correctos, "")
        self.assertIsNone(encuesta.completed_at)

    def test_un_campaign_send_no_puede_tener_dos_encuestas(self):
        from django.db import IntegrityError
        from bot.models import CampaignSend, EncuestaServicioTecnico
        send = CampaignSend.objects.create(contacto="56911112222", campaign_type="encuesta_servicio_tecnico", template="x")
        EncuestaServicioTecnico.objects.create(campaign_send=send)
        with self.assertRaises(IntegrityError):
            EncuestaServicioTecnico.objects.create(campaign_send=send)


class EncuestaVentaAutoNuevoTest(TestCase):
    def test_se_crea_ligado_a_un_campaign_send_y_arranca_sin_respuestas(self):
        from bot.models import CampaignSend, EncuestaVentaAutoNuevo
        send = CampaignSend.objects.create(contacto="56911112222", campaign_type="encuesta_venta_auto_nuevo", template="x")
        encuesta = EncuestaVentaAutoNuevo.objects.create(campaign_send=send)
        self.assertIsNone(encuesta.p1_nota_general)
        self.assertEqual(encuesta.p2_dejo_parte_pago, "")
        self.assertEqual(encuesta.p2_motivo_no, "")
        self.assertIsNone(encuesta.completed_at)


class SucursalCategoriasTest(TestCase):
    def test_categorias_default_es_lista_vacia(self):
        s = Sucursal.objects.create(nombre="Centro")
        self.assertEqual(s.categorias, [])
        self.assertIsNone(s.categorias_actualizado_en)

    def test_categorias_acepta_lista_de_strings(self):
        s = Sucursal.objects.create(nombre="Cantagallo", categorias=["ventas", "servicio_tecnico"])
        s.refresh_from_db()
        self.assertEqual(s.categorias, ["ventas", "servicio_tecnico"])
