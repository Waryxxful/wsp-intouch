"""Cobertura del comando `doctor`.

El comando existe para que los fallos silenciosos dejen de ser silenciosos
(ver el docstring de bot/management/commands/doctor.py). Estos tests cubren
sobre todo el chequeo que mas valor tiene y mas facil se rompe: el cruce entre
lo que el prompt NOMBRA y lo que el especialista tiene BINDEADO.

Ninguno sale a la red: los chequeos de red se saltean con --sin-red o se
mockean. El de Supabase se saltea solo, porque get_supabase_client() se niega a
correr con Django apuntando a sqlite.
"""
import os
import re
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from bot.management.commands.doctor import (
    AVISO, ESFUERZOS_QUE_PASA_EL_CODIGO, FALLA, OK, SECCIONES,
    chequear_catalogo_openrouter, chequear_cliente_activo, chequear_dimension_embeddings,
    chequear_rag_schema, chequear_tools_del_prompt, chequear_variables_obligatorias,
    _universo_de_tools,
)
from bot.models import CustomSpecialist, Sucursal, save_prompt_version

_RE_EFFORT = re.compile(r'"effort"\s*:\s*"(\w+)"')

# Ficha minima con la forma real del catalogo de OpenRouter (verificada contra
# la API el 2026-09-08): `supported_parameters` es una lista plana y
# `reasoning` trae `supported_efforts` + `mandatory`.
def _ficha(model_id, *, parametros=("tools", "structured_outputs"),
           efforts=("high", "low"), mandatory=False):
    return {
        "id": model_id,
        "supported_parameters": list(parametros),
        "reasoning": {"supported_efforts": list(efforts), "mandatory": mandatory},
    }


def _niveles(hallazgos):
    return [h.nivel for h in hallazgos]


class UniversoDeToolsTest(TestCase):
    def test_escanea_las_tools_reales_del_repo(self):
        universo = _universo_de_tools()
        # Una de negocio, una del RAG y el canal de salida: si el escaneo por
        # tipo se rompe, las tres desaparecen juntas.
        self.assertIn("buscar_sucursales_cercanas", universo)
        self.assertIn("consultar_base_conocimiento", universo)
        self.assertIn("responder", universo)


class ToolsDelPromptTest(TestCase):
    """El bug real que este chequeo cierra: `registrar_datos_lead` se desbindeo
    del especialista de ventas, se corrigio el fixture en git, y el prompt
    activo en la BD siguio diciendo "llama a registrar_datos_lead". Nada lo
    detectaba y bloqueo un deploy."""

    def _crear_ventas(self, prompt):
        # cliente=CLIENTE_ACTIVO y no "cavem" fijo: el manager filtra por
        # cliente activo y la suite corre como renault (ver CLAUDE.md).
        CustomSpecialist.objects.create(
            slug="ventas", label="Ventas", descripcion="vende",
            cliente=settings.CLIENTE_ACTIVO,
        )
        save_prompt_version("custom:ventas", prompt)

    def test_falla_si_el_prompt_nombra_una_tool_no_bindeada(self):
        self._crear_ventas(
            "Cuando tengas los datos del cliente, llama a registrar_datos_lead "
            "para dejarlos anotados."
        )
        hallazgos = list(chequear_tools_del_prompt({}))
        fallas = [h for h in hallazgos if h.nivel == FALLA]
        self.assertEqual(len(fallas), 1, [h.titulo for h in hallazgos])
        self.assertIn("ventas", fallas[0].titulo)
        self.assertIn("registrar_datos_lead", fallas[0].detalle)

    def test_no_falla_cuando_el_prompt_solo_nombra_tools_bindeadas(self):
        self._crear_ventas(
            "Usa buscar_vehiculos para encontrar stock y simular_financiamiento "
            "para la cuota. Entrega tu respuesta con responder."
        )
        self.assertNotIn(FALLA, _niveles(chequear_tools_del_prompt({})))

    def test_responder_no_cuenta_como_no_bindeada(self):
        """`responder` se bindea en graph.py::_specialist_node_con_tools, no en
        business_actions() -- si el chequeo no lo supiera, TODOS los prompts
        darian falla."""
        self._crear_ventas("Entrega tu respuesta llamando a responder.")
        self.assertNotIn(FALLA, _niveles(chequear_tools_del_prompt({})))

    def test_avisa_de_una_tool_bindeada_que_el_prompt_nunca_nombra(self):
        self._crear_ventas("Se amable con el cliente.")
        avisos = [h for h in chequear_tools_del_prompt({}) if h.nivel == AVISO]
        self.assertTrue(any("ventas" in h.titulo for h in avisos), avisos)

    def test_el_repo_tal_como_esta_no_tiene_prompts_con_tools_fantasma(self):
        """Regresion sobre los especialistas de codigo (faq, agendamiento,
        confirmacion), que usan su SYSTEM_PROMPT del repo."""
        self.assertNotIn(FALLA, _niveles(chequear_tools_del_prompt({})))


class ConfigTest(TestCase):
    @override_settings(WHATSAPP_TOKEN="", WHATSAPP_PHONE_ID="tel", WHATSAPP_VERIFY_TOKEN="v",
                       OPENROUTER_API_KEY="k", GOOGLE_API_KEY="g", PUBLIC_BASE_URL="u")
    def test_falla_si_falta_una_variable_obligatoria(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "u", "SUPABASE_KEY": "k"}):
            hallazgos = list(chequear_variables_obligatorias({}))
        falla = next(h for h in hallazgos if h.nivel == FALLA)
        self.assertIn("WHATSAPP_TOKEN", falla.detalle)

    @override_settings(WHATSAPP_TOKEN="t", WHATSAPP_PHONE_ID="tel", WHATSAPP_VERIFY_TOKEN="CHANGEME",
                       OPENROUTER_API_KEY="k", GOOGLE_API_KEY="g", PUBLIC_BASE_URL="u")
    def test_falla_si_el_verify_token_quedo_en_el_default(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "u", "SUPABASE_KEY": "k"}):
            hallazgos = list(chequear_variables_obligatorias({}))
        self.assertTrue(any("CHANGEME" in h.titulo for h in hallazgos if h.nivel == FALLA))

    @override_settings(CLIENTE_ACTIVO="marca-que-no-existe")
    def test_falla_con_un_cliente_activo_fuera_de_choices(self):
        self.assertEqual(_niveles(chequear_cliente_activo({})), [FALLA])

    def test_falla_si_el_rag_schema_no_es_el_del_cliente_activo(self):
        """El bug de los 297 chunks: escribir/leer la base de conocimiento de
        otra marca."""
        with patch.dict(os.environ, {"RAG_SCHEMA": "otra_marca"}):
            hallazgo = next(iter(chequear_rag_schema({})))
        self.assertEqual(hallazgo.nivel, FALLA)
        self.assertIn("otra_marca", hallazgo.titulo)

    def test_ok_cuando_el_rag_schema_coincide(self):
        with patch.dict(os.environ, {"RAG_SCHEMA": settings.CLIENTE_ACTIVO}):
            self.assertEqual(_niveles(chequear_rag_schema({})), [OK])


class CatalogoOpenrouterTest(TestCase):
    def _correr(self, catalogo):
        with patch("bot.management.commands.doctor._traer_catalogo_openrouter",
                   return_value=catalogo):
            return list(chequear_catalogo_openrouter({}))

    @override_settings(OPENROUTER_MODEL="modelo/inexistente", OPENROUTER_ROUTING_MODEL="",
                       OPENROUTER_MEDIA_MODEL="", OPENROUTER_SCRAPING_MODEL="")
    def test_falla_si_el_modelo_no_existe_en_el_catalogo(self):
        hallazgos = self._correr({"otro/modelo": _ficha("otro/modelo")})
        self.assertEqual(_niveles(hallazgos), [FALLA])
        self.assertIn("no existe en OpenRouter", hallazgos[0].titulo)

    @override_settings(OPENROUTER_MODEL="m/sin-tools", OPENROUTER_ROUTING_MODEL="",
                       OPENROUTER_MEDIA_MODEL="", OPENROUTER_SCRAPING_MODEL="")
    def test_falla_si_el_modelo_conversacional_no_soporta_tools(self):
        hallazgos = self._correr({
            "m/sin-tools": _ficha("m/sin-tools", parametros=("structured_outputs",),
                                  efforts=("medium", "high")),
        })
        self.assertTrue(any(h.nivel == FALLA and "tools" in h.titulo for h in hallazgos), hallazgos)

    @override_settings(OPENROUTER_MODEL="m/conv", OPENROUTER_ROUTING_MODEL="",
                       OPENROUTER_MEDIA_MODEL="", OPENROUTER_SCRAPING_MODEL="")
    def test_avisa_del_remapeo_silencioso_del_effort(self):
        """El caso REAL: el codigo pasa effort=medium y el modelo solo soporta
        max/high/low, asi que OpenRouter lo sube a high sin decir nada."""
        hallazgos = self._correr({"m/conv": _ficha("m/conv", efforts=("max", "high", "low"))})
        aviso = next(h for h in hallazgos if h.nivel == AVISO)
        self.assertIn("medium", aviso.titulo)

    @override_settings(OPENROUTER_MODEL="m/conv", OPENROUTER_ROUTING_MODEL="",
                       OPENROUTER_MEDIA_MODEL="", OPENROUTER_SCRAPING_MODEL="")
    def test_none_no_es_un_effort_de_la_escala_y_no_avisa(self):
        """`none` pide APAGAR el razonamiento: lo acepta cualquier modelo que no
        lo tenga obligatorio, aunque no aparezca en supported_efforts."""
        hallazgos = self._correr({
            "m/conv": _ficha("m/conv", efforts=("medium", "high"), mandatory=False),
        })
        self.assertNotIn(AVISO, _niveles(hallazgos))

    @override_settings(OPENROUTER_MODEL="m/conv", OPENROUTER_ROUTING_MODEL="",
                       OPENROUTER_MEDIA_MODEL="", OPENROUTER_SCRAPING_MODEL="")
    def test_avisa_de_none_si_el_modelo_obliga_a_razonar(self):
        hallazgos = self._correr({
            "m/conv": _ficha("m/conv", efforts=("medium", "high"), mandatory=True),
        })
        self.assertTrue(any(h.nivel == AVISO and "none" in h.titulo for h in hallazgos), hallazgos)

    def test_un_catalogo_inalcanzable_avisa_pero_no_falla(self):
        with patch("bot.management.commands.doctor._traer_catalogo_openrouter",
                   side_effect=RuntimeError("sin red")):
            self.assertEqual(_niveles(list(chequear_catalogo_openrouter({}))), [AVISO])


class TablaDeEsfuerzosTest(TestCase):
    """Anti-drift: la tabla del comando declara que effort le pasa el codigo a
    cada modelo. Si alguien cambia un nivel en el codigo y no toca la tabla, el
    chequeo del catalogo mide algo que ya no es cierto -- y eso es peor que no
    tenerlo. Mismo patron que la regresion del orden de los `migrate` del
    Dockerfile (leads/tests.py)."""

    CASOS = {
        # archivo -> settings de modelo cuyos efforts pueden aparecer ahi
        "bot/flow/graph.py": ["OPENROUTER_MODEL", "OPENROUTER_ROUTING_MODEL"],
        "bot/flow/media_processing.py": ["OPENROUTER_MEDIA_MODEL"],
        "bot/flow/extractor_metadatos.py": ["OPENROUTER_ROUTING_MODEL"],
    }

    def test_la_tabla_cubre_todos_los_efforts_del_codigo(self):
        for archivo, roles in self.CASOS.items():
            with self.subTest(archivo=archivo):
                fuente = (settings.BASE_DIR / archivo).read_text(encoding="utf-8")
                en_el_codigo = set(_RE_EFFORT.findall(fuente))
                declarados = set().union(*(ESFUERZOS_QUE_PASA_EL_CODIGO[r] for r in roles))
                self.assertEqual(
                    en_el_codigo, declarados,
                    f"{archivo} pasa {sorted(en_el_codigo)} pero la tabla de doctor.py "
                    f"declara {sorted(declarados)} para {roles}. Actualizar "
                    "ESFUERZOS_QUE_PASA_EL_CODIGO.",
                )


class DimensionEmbeddingsTest(TestCase):
    def test_el_repo_tal_como_esta_es_coherente(self):
        self.assertEqual(_niveles(chequear_dimension_embeddings({})), [OK])


class ComandoTest(TestCase):
    def test_corre_entero_sin_red_y_sin_reventar(self):
        salida = StringIO()
        try:
            call_command("doctor", "--sin-red", stdout=salida, stderr=StringIO())
        except CommandError:
            pass  # puede haber fallas legitimas de datos en la BD de test
        texto = salida.getvalue()
        for seccion in SECCIONES:
            self.assertIn(seccion, texto)

    def test_una_sola_seccion(self):
        salida = StringIO()
        try:
            call_command("doctor", "--seccion", "modelos", "--sin-red",
                         stdout=salida, stderr=StringIO())
        except CommandError:
            pass
        self.assertIn("modelos", salida.getvalue())
        self.assertNotIn("whatsapp", salida.getvalue())

    def test_sale_con_error_si_hay_una_falla(self):
        """La BD de test no tiene sucursales, asi que la seccion `datos` falla:
        sirve como caso real de puerta de deploy."""
        self.assertFalse(Sucursal.objects.exists())
        with self.assertRaises(CommandError):
            call_command("doctor", "--seccion", "datos", "--sin-red",
                         stdout=StringIO(), stderr=StringIO())

    def test_un_chequeo_que_revienta_no_tumba_al_resto(self):
        """El valor del comando es correrlos TODOS: una excepcion en uno se
        reporta como falla y la seccion sigue."""
        def boom(_opciones):
            raise RuntimeError("boom")

        salida = StringIO()
        # Se pisa la entrada de SECCIONES y no el atributo del modulo: la lista
        # guarda la referencia a la funcion, asi que un patch del nombre no la
        # alcanzaria (misma trampa que documenta feedback_wsp_demo_mock_patch).
        with patch.dict(SECCIONES, {"config": [boom, chequear_rag_schema]}):
            with self.assertRaises(CommandError):
                call_command("doctor", "--seccion", "config", "--sin-red",
                             stdout=salida, stderr=StringIO())
        self.assertIn("boom", salida.getvalue())
        # el chequeo siguiente de la misma seccion igual corrio
        self.assertIn("RAG_SCHEMA", salida.getvalue())
