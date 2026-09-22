"""Las tres garantías de la escritura del lead, y la guarda que la abre.

Las garantías (spec §5.2), heredadas del patrón LeadComercial de Cavem:
  1. Un valor vacío NO pisa lo ya capturado. El extractor manda el objeto
     completo en cada turno -- `strict: true` se lo exige -- así que sin esta
     guarda cada turno pisaría con vacío lo de los turnos anteriores y el lead
     terminaría la conversación más pobre que a la mitad.
  2. El correo se valida sólo de formato, y nunca se afirma que existe.
  3. Los textos se recortan al max_length de su columna: Django no trunca, y
     SQL Server levanta "String or binary data would be truncated" -- que en
     SQLite no pasa, o sea que el test pasa y producción cae.

La guarda (spec §5.3): la fila se abre SÓLO con un antecedente que el contacto
entregó. El extractor clasifica TODOS los turnos, así que sin esto un "hola"
abriría un lead NO_CALIFICADO y el panel se llenaría de filas vacías.
"""
from django.test import SimpleTestCase, TestCase

from bot.business.lead_intouch import (
    ANTECEDENTES_QUE_ABREN_LEAD, CAMPOS_ESCRIBIBLES, _registrar_lead_impl,
    registrar_lead_del_turno,
)
from bot.models import Conversation, LeadInTouch


class GuardaQueAbreElLeadTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911111111")

    def test_un_saludo_no_abre_un_lead(self):
        registrar_lead_del_turno("56911111111", {
            "resumen_conversacion": "El contacto saluda.",
            "siguiente_accion_recomendada": "Preguntar qué necesita.",
        })
        self.assertEqual(LeadInTouch.objects.count(), 0)

    def test_el_nombre_solo_no_abre_un_lead(self):
        # WhatsApp entrega el nombre del perfil sin que el contacto lo haya
        # dado: no es evidencia de nada.
        registrar_lead_del_turno("56911111111", {"nombre_completo": "Ana Pérez"})
        self.assertEqual(LeadInTouch.objects.count(), 0)

    def test_la_empresa_si_abre_un_lead(self):
        registrar_lead_del_turno("56911111111", {"empresa": "Acme SpA"})
        self.assertEqual(LeadInTouch.objects.count(), 1)
        self.assertEqual(LeadInTouch.objects.get().empresa, "Acme SpA")

    def test_pedir_contacto_humano_abre_un_lead_sin_ningun_otro_dato(self):
        # Caso B del prompt §7: registro parcial por solicitud explícita. El
        # prompt es taxativo en no bloquearlo por falta de correo.
        registrar_lead_del_turno("56911111111", {"solicita_contacto_humano": True})
        lead = LeadInTouch.objects.get()
        self.assertTrue(lead.solicita_contacto_humano)
        self.assertEqual(lead.correo, "")

    def test_sobre_un_lead_que_ya_existe_si_se_escribe_el_resumen(self):
        # Mantener el resumen fresco es justamente para lo que sirve.
        LeadInTouch.objects.create(conversation=self.conv, empresa="Acme SpA")
        registrar_lead_del_turno("56911111111", {
            "resumen_conversacion": "Necesita ordenar su atención en WhatsApp.",
        })
        self.assertEqual(
            LeadInTouch.objects.get().resumen_conversacion,
            "Necesita ordenar su atención en WhatsApp.",
        )

    def test_un_lead_vacio_no_hace_nada(self):
        registrar_lead_del_turno("56911111111", {})
        registrar_lead_del_turno("56911111111", None)
        registrar_lead_del_turno("56911111111", "no soy un dict")
        self.assertEqual(LeadInTouch.objects.count(), 0)

    def test_un_fallo_escribiendo_no_propaga(self):
        # Es lo último que pasa en el turno y ya nadie lo espera: un fallo acá
        # no puede tumbar lo que ya venía hecho.
        from unittest.mock import patch

        with patch("bot.business.lead_intouch._registrar_lead_impl",
                   side_effect=RuntimeError("BD caída")):
            registrar_lead_del_turno("56911111111", {"empresa": "Acme SpA"})

    def test_la_guarda_cubre_todos_los_antecedentes_declarados(self):
        for campo in sorted(ANTECEDENTES_QUE_ABREN_LEAD):
            with self.subTest(campo=campo):
                LeadInTouch.objects.all().delete()
                valor = True if campo.startswith("solicita_") else "un valor"
                registrar_lead_del_turno("56911111111", {campo: valor})
                self.assertEqual(LeadInTouch.objects.count(), 1, campo)


class UnVacioNoPisaTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56922222222")

    def test_el_turno_siguiente_no_borra_lo_capturado(self):
        _registrar_lead_impl("56922222222", {"empresa": "Acme SpA", "cargo": "Gerente"})
        _registrar_lead_impl("56922222222", {"empresa": "", "cargo": None,
                                            "industria": "Retail"})
        lead = LeadInTouch.objects.get()
        self.assertEqual(lead.empresa, "Acme SpA")
        self.assertEqual(lead.cargo, "Gerente")
        self.assertEqual(lead.industria, "Retail")

    def test_una_lista_vacia_tampoco_pisa(self):
        _registrar_lead_impl("56922222222", {"canales_actuales": ["whatsapp", "voz"]})
        _registrar_lead_impl("56922222222", {"canales_actuales": []})
        self.assertEqual(LeadInTouch.objects.get().canales_actuales, ["whatsapp", "voz"])

    def test_una_correccion_del_contacto_si_pisa(self):
        # Prompt §4: prevalece la corrección más reciente.
        _registrar_lead_impl("56922222222", {"empresa": "Acme SpA"})
        _registrar_lead_impl("56922222222", {"empresa": "Acme Chile SpA"})
        self.assertEqual(LeadInTouch.objects.get().empresa, "Acme Chile SpA")

    def test_un_campo_desconocido_se_ignora_con_ruido(self):
        # Sin la lista blanca, un nombre alucinado por el LLM se escribiría
        # como atributo suelto y se perdería sin error visible.
        resultado = _registrar_lead_impl(
            "56922222222", {"empresa": "Acme SpA", "presupuesto_mensual": "999"})
        self.assertEqual(resultado["campos_ignorados"], ["presupuesto_mensual"])
        self.assertNotIn("presupuesto_mensual", CAMPOS_ESCRIBIBLES)


class BooleanosDeTresEstadosTest(TestCase):
    """Un booleano de dos estados no puede representar tres.

    El defecto que estos tests anclan estuvo en producción: el extractor manda
    el objeto completo en cada turno (`strict: true`) y el prompt le pedía
    `false` en todo lo que no se hubiera dicho. `False` no cae en el centinela
    de vacío, así que un `solicita_contacto_humano` capturado en el turno N
    volvía a `False` en el N+1 -- se perdía justamente la señal de que el
    contacto pidió hablar con una persona.

    `UnVacioNoPisaTest` no lo veía porque sólo prueba strings y listas.

    Agregar `False` al centinela habría borrado el caso legítimo "el contacto
    dijo que no usa IA", así que los tres campos son enum de tres valores:
    "si", "no" y "" (no hay evidencia).
    """

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56922222222")

    def test_un_si_se_escribe_como_true(self):
        _registrar_lead_impl("56922222222", {"solicita_contacto_humano": "si"})
        self.assertIs(LeadInTouch.objects.get().solicita_contacto_humano, True)

    def test_un_no_se_escribe_como_false_y_no_es_lo_mismo_que_vacio(self):
        # El caso legítimo que el arreglo fácil (meter False en el centinela)
        # habría borrado: que el contacto NO use IA es un antecedente que el
        # ejecutivo necesita, no un dato faltante.
        _registrar_lead_impl("56922222222", {"usa_ia_actualmente": "no"})
        self.assertIs(LeadInTouch.objects.get().usa_ia_actualmente, False)

    def test_el_turno_siguiente_no_borra_un_true(self):
        # LA REGRESIÓN. Turno N: el contacto pide hablar con una persona.
        # Turno N+1: el tema no se toca, el extractor manda el campo vacío.
        _registrar_lead_impl("56922222222", {"solicita_contacto_humano": "si",
                                            "solicita_consultoria": "si"})
        _registrar_lead_impl("56922222222", {"solicita_contacto_humano": "",
                                            "solicita_consultoria": "",
                                            "empresa": "Acme SpA"})
        lead = LeadInTouch.objects.get()
        self.assertIs(lead.solicita_contacto_humano, True)
        self.assertIs(lead.solicita_consultoria, True)
        self.assertEqual(lead.empresa, "Acme SpA")

    def test_el_turno_siguiente_tampoco_borra_un_no(self):
        _registrar_lead_impl("56922222222", {"usa_ia_actualmente": "no"})
        _registrar_lead_impl("56922222222", {"usa_ia_actualmente": "",
                                            "empresa": "Acme SpA"})
        self.assertIs(LeadInTouch.objects.get().usa_ia_actualmente, False)

    def test_una_correccion_del_contacto_si_pisa_un_booleano(self):
        # Simétrico a `test_una_correccion_del_contacto_si_pisa`: lo que no
        # puede pisar es el VACÍO, no una respuesta contraria explícita.
        _registrar_lead_impl("56922222222", {"usa_ia_actualmente": "no"})
        _registrar_lead_impl("56922222222", {"usa_ia_actualmente": "si"})
        self.assertIs(LeadInTouch.objects.get().usa_ia_actualmente, True)

    def test_no_se_sabe_deja_usa_ia_en_null(self):
        # La columna es `null=True` para distinguir "no se sabe" de "no usa".
        # Con un booleano puro el NULL era inalcanzable y el panel le mostraba
        # "no usa IA" al ejecutivo sobre empresas a las que nadie preguntó.
        _registrar_lead_impl("56922222222", {"empresa": "Acme SpA",
                                            "usa_ia_actualmente": ""})
        self.assertIsNone(LeadInTouch.objects.get().usa_ia_actualmente)

    def test_un_false_suelto_no_pisa_lo_capturado(self):
        # El schema ya no permite un booleano, pero si llega uno (una llamada
        # vieja, o el modelo saliéndose del enum) se lee conservador: un False
        # es indistinguible del relleno que causó el defecto.
        _registrar_lead_impl("56922222222", {"solicita_contacto_humano": "si"})
        _registrar_lead_impl("56922222222", {"solicita_contacto_humano": False})
        self.assertIs(LeadInTouch.objects.get().solicita_contacto_humano, True)

    def test_un_valor_que_no_es_del_enum_no_afirma_nada(self):
        _registrar_lead_impl("56922222222", {"empresa": "Acme SpA",
                                             "usa_ia_actualmente": "quizás"})
        self.assertIsNone(LeadInTouch.objects.get().usa_ia_actualmente)

    def test_el_schema_del_extractor_pide_los_tres_estados(self):
        # Las dos puntas tienen que estar de acuerdo: si el schema volviera a
        # `boolean`, el writer nunca vería un "no" y el defecto vuelve.
        from bot.flow.extractor_metadatos import LEAD_PROPIEDADES

        for campo in ("usa_ia_actualmente", "solicita_consultoria",
                      "solicita_contacto_humano"):
            with self.subTest(campo=campo):
                self.assertEqual(LEAD_PROPIEDADES[campo],
                                 {"type": "string", "enum": ["si", "no", ""]})


class CorreoTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56933333333")

    def test_un_correo_con_formato_plausible_se_guarda(self):
        _registrar_lead_impl("56933333333", {"correo": "ana@acme.cl"})
        self.assertEqual(LeadInTouch.objects.get().correo, "ana@acme.cl")

    def test_un_correo_sin_arroba_se_rechaza_con_motivo(self):
        resultado = _registrar_lead_impl(
            "56933333333", {"empresa": "Acme SpA", "correo": "ana.acme.cl"})
        self.assertEqual(LeadInTouch.objects.get().correo, "")
        self.assertIn("correo", resultado["campos_rechazados"])

    def test_se_acepta_un_correo_personal(self):
        # Prompt §4: no se exige dominio corporativo.
        _registrar_lead_impl("56933333333", {"correo": "ana@gmail.com"})
        self.assertEqual(LeadInTouch.objects.get().correo, "ana@gmail.com")


class RecorteTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56944444444")

    def test_un_texto_largo_se_recorta_al_max_length(self):
        _registrar_lead_impl("56944444444", {"empresa": "A" * 500})
        lead = LeadInTouch.objects.get()
        self.assertEqual(len(lead.empresa), 200)


class ScoreEscritoTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56955555555")

    def test_las_senales_se_traducen_a_score(self):
        _registrar_lead_impl(
            "56955555555",
            {"empresa": "Acme SpA", "necesidad_principal": "Ordenar la atención."},
            senales={"encaje_con_oferta": True, "necesidad_concreta": True,
                     "solicita_siguiente_paso": True,
                     "intencion_avanzar_declarada": True},
        )
        self.assertEqual(LeadInTouch.objects.get().lead_score, "HOT")

    def test_sin_senales_el_score_no_se_pisa(self):
        # Un turno posterior sin señales no puede degradar un lead que ya
        # calificó: sería perder la calificación por un turno de cortesía.
        _registrar_lead_impl("56955555555", {"empresa": "Acme SpA"},
                             senales={"encaje_con_oferta": True,
                                      "interes_exploratorio": True})
        self.assertEqual(LeadInTouch.objects.get().lead_score, "COLD")
        _registrar_lead_impl("56955555555", {"cargo": "Gerente"}, senales=None)
        self.assertEqual(LeadInTouch.objects.get().lead_score, "COLD")

    def test_una_senal_desconocida_no_revienta(self):
        _registrar_lead_impl(
            "56955555555", {"empresa": "Acme SpA"},
            senales={"encaje_con_oferta": True, "interes_exploratorio": True,
                     "senal_que_el_modelo_invento": True},
        )
        self.assertEqual(LeadInTouch.objects.get().lead_score, "COLD")


class SinConversacionTest(TestCase):
    def test_devuelve_un_motivo_y_no_crea_nada(self):
        resultado = _registrar_lead_impl("56999999999", {"empresa": "Acme SpA"})
        self.assertFalse(resultado["ok"])
        self.assertEqual(LeadInTouch.objects.count(), 0)


class SinksTest(SimpleTestCase):
    def test_los_sinks_validos_no_cambiaron(self):
        from bot.business.lead_intouch import SINKS_VALIDOS

        self.assertEqual(SINKS_VALIDOS, frozenset({"none", "http"}))


class PreferenciaHorariaTest(TestCase):
    """El documento comercial (§15) pide capturar qué día u horario le acomoda
    al prospecto, MIENTRAS no exista una agenda integrada -- explícitamente sin
    comprometer una hora. Hasta ahora no había dónde guardarlo, así que el dato
    se perdía en la conversación.

    NO entra en ANTECEDENTES_QUE_ABREN_LEAD a propósito: una preferencia de
    horario no justifica por sí sola abrir un lead, y nunca llega sola.
    """

    def test_el_extractor_puede_escribir_preferencia_horaria(self):
        from bot.business.lead_intouch import CAMPOS_ESCRIBIBLES
        self.assertIn("preferencia_horaria", CAMPOS_ESCRIBIBLES)

    def test_no_abre_un_lead_por_si_sola(self):
        from bot.business.lead_intouch import ANTECEDENTES_QUE_ABREN_LEAD
        self.assertNotIn("preferencia_horaria", ANTECEDENTES_QUE_ABREN_LEAD)

    def test_el_esquema_del_extractor_la_declara(self):
        from bot.flow.extractor_metadatos import LEAD_PROPIEDADES
        self.assertIn("preferencia_horaria", LEAD_PROPIEDADES)

    def test_se_persiste_en_el_lead(self):
        from bot.models import Conversation, LeadInTouch
        conv = Conversation.objects.create(wa_id="56900000001")
        lead = LeadInTouch.objects.create(
            conversation=conv, empresa="Empresa Demo",
            preferencia_horaria="martes por la mañana")
        lead.refresh_from_db()
        self.assertEqual(lead.preferencia_horaria, "martes por la mañana")
