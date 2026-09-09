from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bot.models import Conversation, VehiculoPartePago
from bot.simulator.marking import LEAD_RAZON_PREFIJO, TEST_WA_ID_PREFIJO


class Command(BaseCommand):
    help = "Borra las conversaciones, mensajes y leads generados por el simulador de pruebas (prefijo TEST)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days", type=int, default=None,
            help="Solo borra datos de test creados hace mas de N dias. Sin este flag, borra todos.",
        )

    def handle(self, *args, **options):
        from leads.models import Lead

        conversaciones = Conversation.objects.filter(wa_id__startswith=TEST_WA_ID_PREFIJO)
        leads = Lead.objects.filter(razon_interes__startswith=LEAD_RAZON_PREFIJO)

        older_than_days = options["older_than_days"]
        if older_than_days is not None:
            corte = timezone.now() - timedelta(days=older_than_days)
            conversaciones = conversaciones.filter(created_at__lt=corte)
            leads = leads.filter(created_at__lt=corte)

        # VehiculoPartePago cuelga de Conversation con SET_NULL, no CASCADE
        # (la solicitud de tasacion tiene valor por si sola aunque se pierda el
        # chat). Eso significa que borrar la conversacion de test dejaria la
        # tasacion viva y SIN forma de rastrearla: el equipo comercial veria una
        # solicitud inventada por el simulador como si fuera de un cliente real.
        # Se borran explicitamente ANTES, mientras todavia se sabe cuales son.
        partes_pago = VehiculoPartePago.objects.filter(conversation__in=conversaciones)
        total_partes_pago = partes_pago.count()
        total_conversaciones = conversaciones.count()
        total_leads = leads.count()
        partes_pago.delete()
        conversaciones.delete()
        leads.delete()

        self.stdout.write(self.style.SUCCESS(
            f"Borradas {total_conversaciones} conversaciones de test, {total_leads} leads de test "
            f"y {total_partes_pago} solicitudes de parte de pago de test."
        ))
