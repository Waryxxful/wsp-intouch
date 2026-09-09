"""A/B de un prompt candidato contra el prompt activo, con el simulador.

NO publica nada: el candidato se inyecta via prompts_override
(bot/simulator/prompt_override.py). Cada corrida golpea LLMs reales --
correr solo cuando el usuario lo pida explicitamente.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from bot.simulator.models import EscenarioDePrueba
from bot.simulator.runner import correr_escenario

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Corre los escenarios activos con el prompt activo (baseline) y con un candidato, y compara."

    def add_arguments(self, parser):
        # Nota: NO se marca required=True aca a proposito. call_command()
        # simula el parseo de argparse para los argumentos required, y ese
        # simulacro no soporta pasar varios valores a un action="append"
        # (solo repite el ultimo). La obligatoriedad de --agente/--candidato
        # se valida a mano abajo, que si funciona igual desde CLI real y
        # desde call_command(kwarg=lista).
        parser.add_argument(
            "--agente", action="append",
            help='ej. "custom:ventas" o "global"; repetible (obligatorio al menos una vez) '
                 'para probar varios agentes a la vez, apareado por posicion con '
                 '--candidato (los prompts se publican juntos y pueden depender entre si)',
        )
        parser.add_argument(
            "--candidato", action="append",
            help="ruta a un .txt con el prompt candidato completo; repetible "
                 "(obligatorio al menos una vez), uno por --agente",
        )
        parser.add_argument("--corridas", type=int, default=2, help="corridas por lado (default 2)")

    def handle(self, *args, **options):
        agentes = options["agente"]
        candidatos = options["candidato"]
        # call_command con kwargs no pasa por argparse: un kwarg string suelto
        # (agente="x" en vez de agente=["x"]) llega tal cual, no envuelto en
        # lista, aunque el argumento sea action="append".
        if isinstance(agentes, str):
            agentes = [agentes]
        if isinstance(candidatos, str):
            candidatos = [candidatos]
        corridas = options["corridas"]

        if not agentes:
            raise CommandError("--agente es obligatorio (al menos uno)")
        if not candidatos:
            raise CommandError("--candidato es obligatorio (al menos uno)")
        if len(agentes) != len(candidatos):
            raise CommandError(
                f"cantidad de --agente ({len(agentes)}) no coincide con la cantidad de "
                f"--candidato ({len(candidatos)}): se aparean 1 a 1 por posicion"
            )
        if len(set(agentes)) != len(agentes):
            raise CommandError(
                f"--agente repetido en {agentes!r}: seria ambiguo cual --candidato gana"
            )

        override = {}
        for agente, ruta_candidato in zip(agentes, candidatos):
            try:
                with open(ruta_candidato) as f:
                    texto_candidato = f.read()
            except OSError as exc:
                raise CommandError(f"no se pudo leer --candidato {ruta_candidato!r}: {exc}")
            if not texto_candidato.strip():
                raise CommandError(f"--candidato {ruta_candidato!r} esta vacio (o solo whitespace): nada que probar")
            override[agente] = texto_candidato

        escenarios = list(EscenarioDePrueba.objects.filter(activo=True))

        # {nombre_escenario: {"baseline": [bool, ...], "candidato": [bool, ...]}}
        resultados = {e.nombre: {"baseline": [], "candidato": []} for e in escenarios}

        for i in range(corridas):
            for lado, prompts_override in (("baseline", None), ("candidato", override)):
                for escenario in escenarios:
                    item_input = {
                        "nombre": escenario.nombre, "persona": escenario.persona,
                        "objetivo": escenario.objetivo, "criterios": escenario.criterios,
                        "max_turns": escenario.max_turns,
                    }
                    if prompts_override is not None:
                        item_input["prompts_override"] = prompts_override
                    # Un escenario que revienta con una excepcion no
                    # controlada (ej. la API real del LLM falla) no debe
                    # tumbar el comando entero -- paso de verdad: la corrida
                    # murio en el escenario 11 de 48 y se perdieron los 10
                    # anteriores, ya pagos en llamadas a LLM. Mismo criterio
                    # que bot/simulator/runner.py::_guardar_resultado: loguear
                    # con logger.exception y contar el intento como no
                    # pasado en vez de propagar.
                    try:
                        resultado = correr_escenario(item_input)
                    except Exception as exc:
                        logger.exception(
                            "[ab_prompts] el escenario %r (%s) revento con una excepcion no controlada",
                            escenario.nombre, lado,
                        )
                        resultados[escenario.nombre][lado].append(False)
                        self.stdout.write(self.style.ERROR(
                            f"[corrida {i + 1}/{corridas}] {lado:9s} {escenario.nombre}: "
                            f"EXCEPCION ({exc})"
                        ))
                        continue
                    resultados[escenario.nombre][lado].append(bool(resultado["paso"]))
                    self.stdout.write(
                        f"[corrida {i + 1}/{corridas}] {lado:9s} {escenario.nombre}: "
                        f"{'paso' if resultado['paso'] else 'FALLO'} "
                        f"{resultado['fallos_de_codigo'] + resultado['fallos_de_juez']}"
                    )

        self.stdout.write("")
        self.stdout.write(f"=== Comparacion ({corridas} corrida(s) por lado) ===")
        hay_regresion = False
        for nombre, lados in resultados.items():
            base_ok = sum(lados["baseline"])
            cand_ok = sum(lados["candidato"])
            caida = base_ok - cand_ok
            fallos_cand = corridas - cand_ok
            marca = ""
            if caida > 0:
                if corridas == 1 or fallos_cand >= 2:
                    # Con una sola corrida no hay evidencia de repeticion: se
                    # marca regresion por conservador. Con >=2, solo cuenta
                    # si la falla se repite -- una caida aislada es ruido del
                    # juez LLM (spec, seccion "Validacion").
                    marca = "  <-- REGRESION"
                    hay_regresion = True
                else:
                    marca = "  <-- INCONSISTENTE (fallo 1 de N, revisar a mano, no bloquea)"
            self.stdout.write(f"{nombre:45s} baseline {base_ok}/{corridas}   candidato {cand_ok}/{corridas}{marca}")

        self.stdout.write("")
        if corridas == 1:
            self.stdout.write(self.style.WARNING(
                "Corriste con --corridas 1: con una sola corrida no hay evidencia de "
                "repeticion, asi que no se puede distinguir un problema real del ruido "
                "propio del juez LLM (no determinista) -- cualquier caida se trata como "
                "posible regresion por conservador. Para confirmar, corré de nuevo con "
                "--corridas >= 2."
            ))
        if hay_regresion:
            self.stdout.write(self.style.ERROR("Hay al menos una REGRESION: no publicar el candidato asi."))
        else:
            self.stdout.write(self.style.SUCCESS("Sin regresiones respecto del baseline."))
