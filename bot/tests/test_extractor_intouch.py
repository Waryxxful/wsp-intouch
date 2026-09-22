"""El schema del extractor tiene que calzar con lo que escribe la BD.

Un nombre distinto acá no da error: el campo simplemente no se escribe y el
dato se pierde en silencio. Es el modo de falla más caro de este módulo, y por
eso se prueba la correspondencia y no sólo la forma.
"""
from django.test import SimpleTestCase

from bot.business.lead_intouch import CAMPOS_ESCRIBIBLES
from bot.flow.extractor_metadatos import LEAD_PROPIEDADES, SCHEMA_METADATOS
from bot.flow.respuesta import campos_de
from bot.models import SenalesLead


class SchemaDelLeadTest(SimpleTestCase):
    def test_el_extractor_pide_el_lead(self):
        props = SCHEMA_METADATOS["schema"]["properties"]
        self.assertIn("lead", props)
        self.assertIn("lead", SCHEMA_METADATOS["schema"]["required"])

    def test_los_nombres_son_los_mismos_que_escribe_la_bd(self):
        campos = set(LEAD_PROPIEDADES) - {"senales"}
        self.assertTrue(campos <= CAMPOS_ESCRIBIBLES, campos - CAMPOS_ESCRIBIBLES)

    def test_estan_los_22_campos_del_contrato(self):
        # El contrato del prompt §8. Si falta uno, el bot lo recoge en la
        # conversación y no llega nunca al equipo comercial.
        #
        # `preferencia_horaria` se sumó el 2026-09-10, del §15 del documento
        # comercial: qué día u horario le acomoda al contacto, MIENTRAS no
        # exista agenda integrada. No es una hora reservada.
        esperados = {
            "nombre_completo", "correo", "empresa", "industria", "subtipo_automotriz",
            "cargo", "pais_ciudad", "situacion_contact_center", "tipo_contact_center",
            "usa_ia_actualmente", "canales_actuales", "volumen_interacciones",
            "necesidad_principal", "soluciones_interes", "intencion", "plazo_proyecto",
            "preferencia_horaria",
            "solicita_consultoria", "solicita_contacto_humano",
            "resumen_conversacion", "siguiente_accion_recomendada",
        }
        self.assertEqual(set(LEAD_PROPIEDADES) - {"senales"}, esperados)

    def test_no_se_pide_lead_score_al_modelo(self):
        # Spec §7.3: el veredicto lo calcula el código. Pedírselo al modelo
        # además de calcularlo es tener dos escritores del mismo dato.
        self.assertNotIn("lead_score", LEAD_PROPIEDADES)

    def test_no_se_pide_el_telefono(self):
        # Llega de los metadatos de WhatsApp; el prompt prohíbe pedirlo.
        self.assertNotIn("telefono", LEAD_PROPIEDADES)

    def test_strict_exige_todos_los_campos(self):
        lead = SCHEMA_METADATOS["schema"]["properties"]["lead"]
        self.assertEqual(set(lead["required"]), set(lead["properties"]))
        self.assertFalse(lead["additionalProperties"])

    def test_los_enums_del_contact_center_incluyen_mixto(self):
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        self.assertIn("mixto", props["tipo_contact_center"]["enum"])
        self.assertIn("no_tiene", props["tipo_contact_center"]["enum"])

    def test_los_siete_subtipos_automotrices(self):
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        enum = set(props["subtipo_automotriz"]["enum"]) - {""}
        self.assertEqual(len(enum), 7, enum)

    def test_las_listas_se_piden_como_array(self):
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        for campo in ("canales_actuales", "soluciones_interes"):
            self.assertEqual(props[campo]["type"], "array", campo)


class SchemaDeLasSenalesTest(SimpleTestCase):
    def test_las_senales_estan_en_el_lead(self):
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        self.assertIn("senales", props)

    def test_las_senales_son_las_de_la_dataclass(self):
        # Una señal en el schema que la dataclass no tiene se descarta al
        # escribir, y una de la dataclass que el schema no pide queda siempre
        # en false: el score saldría mal sin que nada avise.
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        del_schema = set(props["senales"]["properties"])
        de_la_dataclass = {f.name for f in SenalesLead.__dataclass_fields__.values()}
        self.assertEqual(del_schema, de_la_dataclass)

    def test_las_senales_son_booleanas(self):
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        for campo, definicion in props["senales"]["properties"].items():
            self.assertEqual(definicion["type"], "boolean", campo)


class CamposDelEspecialistaTest(SimpleTestCase):
    def test_comercial_declara_el_lead(self):
        self.assertIn("lead", campos_de("comercial"))

    def test_no_declara_campos_de_autos(self):
        campos = campos_de("comercial")
        self.assertNotIn("modelo_imagen", campos)
        self.assertNotIn("sucursal_direccion_ids", campos)


class PromptDelExtractorTest(SimpleTestCase):
    def test_el_prompt_no_habla_de_una_concesionaria(self):
        from bot.flow.extractor_metadatos import PROMPT_EXTRACTOR

        self.assertNotIn("concesionaria", PROMPT_EXTRACTOR.lower())
        self.assertNotIn("vehículo", PROMPT_EXTRACTOR.lower())

    def test_el_prompt_prohibe_deducir(self):
        from bot.flow.extractor_metadatos import PROMPT_EXTRACTOR

        self.assertIn("No deduzcas", PROMPT_EXTRACTOR)
