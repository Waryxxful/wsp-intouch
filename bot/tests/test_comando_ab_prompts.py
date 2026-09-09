import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from bot.simulator.models import EscenarioDePrueba


class ComandoABPromptsTest(TestCase):
    def setUp(self):
        EscenarioDePrueba.objects.update(activo=False)
        EscenarioDePrueba.objects.create(
            nombre="objecion-precio", persona="p", objetivo="o", criterios=[], activo=True,
        )
        self.archivo = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        self.archivo.write("PROMPT CANDIDATO")
        self.archivo.close()

    def test_corre_baseline_y_candidato_la_misma_cantidad_de_veces(self):
        llamadas = []

        def fake_correr(item_input):
            llamadas.append(item_input.get("prompts_override"))
            return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=2, stdout=StringIO())

        self.assertEqual(len(llamadas), 4)  # 1 escenario x 2 corridas x 2 lados
        self.assertEqual(llamadas.count(None), 2)
        self.assertEqual(
            llamadas.count({"custom:ventas": "PROMPT CANDIDATO"}), 2,
        )

    def test_reporta_una_regresion_cuando_el_candidato_falla_y_el_baseline_pasa(self):
        def fake_correr(item_input):
            paso = item_input.get("prompts_override") is None
            return {"paso": paso, "num_turnos": 3,
                    "fallos_de_codigo": [] if paso else ["json_valido_cada_turno"],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        salida = StringIO()
        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=1, stdout=salida)

        texto = salida.getvalue()
        self.assertIn("objecion-precio", texto)
        self.assertIn("REGRESION", texto)

    def test_sin_regresion_cuando_ambos_lados_pasan(self):
        def fake_correr(item_input):
            return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        salida = StringIO()
        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=1, stdout=salida)

        self.assertNotIn("REGRESION", salida.getvalue())

    def test_ignora_los_escenarios_inactivos(self):
        EscenarioDePrueba.objects.create(
            nombre="apagado", persona="p", objetivo="o", criterios=[], activo=False,
        )
        llamadas = []

        def fake_correr(item_input):
            llamadas.append(item_input["nombre"])
            return {"paso": True, "num_turnos": 1, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=1, stdout=StringIO())

        self.assertNotIn("apagado", llamadas)

    def test_falla_aislada_del_candidato_con_2_corridas_es_inconsistente_no_regresion(self):
        contador = {"candidato": 0}

        def fake_correr(item_input):
            if item_input.get("prompts_override") is None:
                return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                        "fallos_de_juez": [], "juez": None, "turnos": []}
            contador["candidato"] += 1
            paso = contador["candidato"] != 1  # falla solo en la primera corrida del candidato
            return {"paso": paso, "num_turnos": 3,
                    "fallos_de_codigo": [] if paso else ["json_valido_cada_turno"],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        salida = StringIO()
        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=2, stdout=salida)

        texto = salida.getvalue()
        self.assertIn("INCONSISTENTE", texto)
        self.assertNotIn("REGRESION", texto)

    def test_falla_repetida_del_candidato_con_2_corridas_es_regresion(self):
        def fake_correr(item_input):
            paso = item_input.get("prompts_override") is None
            return {"paso": paso, "num_turnos": 3,
                    "fallos_de_codigo": [] if paso else ["json_valido_cada_turno"],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        salida = StringIO()
        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=2, stdout=salida)

        self.assertIn("REGRESION", salida.getvalue())

    def test_con_una_corrida_una_falla_del_candidato_sigue_siendo_regresion_conservadora(self):
        def fake_correr(item_input):
            paso = item_input.get("prompts_override") is None
            return {"paso": paso, "num_turnos": 3,
                    "fallos_de_codigo": [] if paso else ["json_valido_cada_turno"],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        salida = StringIO()
        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=1, stdout=salida)

        texto = salida.getvalue()
        self.assertIn("REGRESION", texto)
        self.assertIn("corridas >= 2", texto)

    def test_candidato_inexistente_levanta_command_error(self):
        def fake_correr_no_debe_llamarse(item_input):
            raise AssertionError(
                "correr_escenario no debe llamarse nunca cuando --candidato apunta "
                "a una ruta inexistente -- el guard debe cortar antes"
            )

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario",
                   side_effect=fake_correr_no_debe_llamarse):
            with self.assertRaises(CommandError):
                call_command("correr_ab_prompts", agente="custom:ventas",
                             candidato="/no/existe/candidato.txt", corridas=1, stdout=StringIO())

    def test_candidato_vacio_levanta_command_error(self):
        archivo_vacio = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        archivo_vacio.write("   \n")
        archivo_vacio.close()

        def fake_correr_no_debe_llamarse(item_input):
            raise AssertionError(
                "correr_escenario no debe llamarse nunca cuando --candidato apunta "
                "a un archivo vacio/solo whitespace -- el guard debe cortar antes"
            )

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario",
                   side_effect=fake_correr_no_debe_llamarse):
            with self.assertRaises(CommandError):
                call_command("correr_ab_prompts", agente="custom:ventas",
                             candidato=archivo_vacio.name, corridas=1, stdout=StringIO())

    def test_call_command_con_agente_y_candidato_como_strings_sueltos_sigue_funcionando(self):
        # Gotcha: call_command con kwargs NO pasa por argparse, asi que con
        # action="append" un kwarg string (no lista) llega tal cual como str.
        # El handle() tiene que normalizar a lista de 1 elemento antes de
        # zippear -- si no, este test rompe (y con el los 9 tests viejos que
        # llaman igual, con agente=<str>, candidato=<str>).
        llamadas = []

        def fake_correr(item_input):
            llamadas.append(item_input.get("prompts_override"))
            return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=1, stdout=StringIO())

        overrides_candidato = [l for l in llamadas if l is not None]
        self.assertEqual(overrides_candidato, [{"custom:ventas": "PROMPT CANDIDATO"}])

    def test_dos_pares_arma_un_solo_prompts_override_apareado_por_posicion(self):
        archivo_global = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        archivo_global.write("PROMPT GLOBAL")
        archivo_global.close()

        llamadas = []

        def fake_correr(item_input):
            llamadas.append(item_input.get("prompts_override"))
            return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command(
                "correr_ab_prompts",
                agente=["custom:ventas", "global"],
                candidato=[self.archivo.name, archivo_global.name],
                corridas=1, stdout=StringIO(),
            )

        overrides_candidato = [l for l in llamadas if l is not None]
        self.assertEqual(len(overrides_candidato), 1)
        self.assertEqual(
            overrides_candidato[0],
            {"custom:ventas": "PROMPT CANDIDATO", "global": "PROMPT GLOBAL"},
        )

    def test_dos_pares_el_lado_baseline_sigue_sin_prompts_override(self):
        archivo_global = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        archivo_global.write("PROMPT GLOBAL")
        archivo_global.close()

        llamadas = []

        def fake_correr(item_input):
            llamadas.append(item_input.get("prompts_override"))
            return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command(
                "correr_ab_prompts",
                agente=["custom:ventas", "global"],
                candidato=[self.archivo.name, archivo_global.name],
                corridas=1, stdout=StringIO(),
            )

        # 1 escenario x 1 corrida x 2 lados = 2 llamadas; el lado baseline
        # (uno de las dos) no debe llevar prompts_override.
        self.assertEqual(llamadas.count(None), 1)

    def test_distinta_cantidad_de_agente_y_candidato_levanta_command_error(self):
        archivo_global = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        archivo_global.write("PROMPT GLOBAL")
        archivo_global.close()

        def fake_correr_no_debe_llamarse(item_input):
            raise AssertionError(
                "correr_escenario no debe llamarse nunca cuando --agente y --candidato "
                "no aparean 1 a 1 -- el guard debe cortar antes"
            )

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario",
                   side_effect=fake_correr_no_debe_llamarse):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "correr_ab_prompts",
                    agente=["custom:ventas", "global"],
                    candidato=[self.archivo.name],
                    corridas=1, stdout=StringIO(),
                )
        # el mensaje tiene que ser el guard de cantidades, no un error generico
        # de argparse ("unrecognized arguments") ni ningun otro accidente.
        self.assertIn("2", str(ctx.exception))
        self.assertIn("1", str(ctx.exception))
        self.assertNotIn("unrecognized", str(ctx.exception))

    def test_escenario_que_revienta_no_corta_la_corrida_y_la_tabla_final_sale_completa(self):
        # Pasó de verdad: la corrida murio en el escenario 11 de 48 y se
        # perdieron los 10 anteriores (ya pagos en llamadas a LLM) porque
        # correr_escenario() se llamaba sin proteccion. El precedente a
        # seguir es bot/simulator/runner.py::_guardar_resultado, que envuelve
        # la llamada en try/except Exception, loguea y persiste el resultado
        # como fallido en vez de propagar.
        EscenarioDePrueba.objects.create(
            nombre="segundo-escenario", persona="p", objetivo="o", criterios=[], activo=True,
        )
        llamadas = []

        def fake_correr(item_input):
            llamadas.append((item_input["nombre"], item_input.get("prompts_override")))
            if item_input["nombre"] == "objecion-precio" and item_input.get("prompts_override") is None:
                raise RuntimeError("boom: API de Gemini no responde")
            return {"paso": True, "num_turnos": 3, "fallos_de_codigo": [],
                    "fallos_de_juez": [], "juez": None, "turnos": []}

        salida = StringIO()
        with patch("bot.management.commands.correr_ab_prompts.correr_escenario", side_effect=fake_correr):
            call_command("correr_ab_prompts", agente="custom:ventas",
                         candidato=self.archivo.name, corridas=1, stdout=salida)

        # Los 2 escenarios x 1 corrida x 2 lados se intentaron los 4 -- el
        # que reventó no impidió que se llamara al resto.
        self.assertEqual(len(llamadas), 4)
        texto = salida.getvalue()
        # La tabla comparativa final sigue saliendo completa, con ambos
        # escenarios (el que reventó incluido).
        self.assertIn("objecion-precio", texto)
        self.assertIn("segundo-escenario", texto)
        # Visible que ESE intento reventó con una excepcion, no confundible
        # con un "FALLO" normal del juez.
        self.assertIn("EXCEPCION", texto)
        self.assertIn("boom: API de Gemini no responde", texto)

    def test_agente_repetido_levanta_command_error(self):
        archivo_2 = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        archivo_2.write("OTRO PROMPT")
        archivo_2.close()

        def fake_correr_no_debe_llamarse(item_input):
            raise AssertionError(
                "correr_escenario no debe llamarse nunca cuando --agente esta repetido "
                "-- el guard debe cortar antes (seria ambiguo cual candidato gana)"
            )

        with patch("bot.management.commands.correr_ab_prompts.correr_escenario",
                   side_effect=fake_correr_no_debe_llamarse):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "correr_ab_prompts",
                    agente=["custom:ventas", "custom:ventas"],
                    candidato=[self.archivo.name, archivo_2.name],
                    corridas=1, stdout=StringIO(),
                )
        # el mensaje tiene que ser el guard de agente repetido, no un error
        # generico de argparse ("unrecognized arguments") ni ningun otro accidente.
        self.assertIn("custom:ventas", str(ctx.exception))
        self.assertNotIn("unrecognized", str(ctx.exception))
