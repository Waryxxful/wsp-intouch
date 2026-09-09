from django.core.management.base import BaseCommand, CommandError

from bot.simulator.runner import ejecutar_corrida, iniciar_corrida


class Command(BaseCommand):
    help = "Corre el simulador de conversaciones de prueba contra el bot real de wsp_demo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--scenario", dest="scenario", default=None,
            help="Nombre de un escenario puntual (por defecto corre todos los activos).",
        )

    def handle(self, *args, **options):
        try:
            corrida = iniciar_corrida(nombre_escenario=options["scenario"], disparada_por="consola")
        except ValueError as exc:
            raise CommandError(str(exc))

        ejecutar_corrida(corrida)

        resultados = list(corrida.resultados.select_related("escenario").all())
        pasaron = sum(1 for r in resultados if r.paso)
        self.stdout.write(f"corrida {corrida.pk}: {pasaron}/{len(resultados)} escenarios pasaron ({corrida.estado})")
        for r in resultados:
            estado = "OK" if r.paso else "FALLO"
            self.stdout.write(f"  [{estado}] {r.escenario.nombre}")
            if r.error:
                self.stdout.write(f"    error: {r.error}")
            for fallo in r.fallos:
                self.stdout.write(f"    - {fallo}")
