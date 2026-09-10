"""Manda al CRM los leads que quedaron sin despachar.

POR QUÉ EXISTE: `_despachar_si_corresponde` corre dentro del turno, o sea sólo
cuando el contacto vuelve a escribir. Si el envío falla y la conversación
termina ahí, el lead se queda en la tabla y no llega nunca -- y el chat salió
igual de bien, así que nadie se entera. Este barrido es la red.

Reintenta con el MISMO `evento_id`: si el receptor ya había hecho commit y se
perdió la respuesta, devuelve el mismo id en vez de crear otra oportunidad.

UN JOB PERIÓDICO NO PUEDE FALLAR EN SILENCIO: si la consulta de pendientes
revienta (BD caída, columna que no existe), eso NO puede leerse como "no hay
nada pendiente" -- por eso se distingue con un mensaje propio y una
`CommandError` (código de salida != 0), en vez de dejar que el bucle imprima
"Leads despachados: 0" como si todo estuviera en orden.

UN LEAD EN CONFLICTO (409) NO SE REINTENTA ACÁ: la ambigüedad de identidad que
señaló el receptor no la resuelve un reintento, la resuelve una persona del
lado del CRM. Este barrido lo excluye de "pendientes" -- si no, reintentaría
el mismo 409 cada 15 minutos para siempre -- pero lo REPORTA en la salida:
un conflicto silencioso es peor que uno ruidoso, porque el barrido diría
"0 pendientes" mientras hay leads que nadie está trabajando. La única vía de
vuelta al circuito es el reintento manual del panel.

OJO CON EL IMPORT: se importa el MÓDULO `lead_intouch`, no sus nombres sueltos
(`from bot.business.lead_intouch import _enviar_al_sink`). Un `import` directo
del nombre se resuelve UNA vez, en el primer import de este archivo -- y en
los tests, ese primer import puede caer dentro del `with patch(...)` de un
test cualquiera. Los tests que patchean después parchan el atributo del
MÓDULO `lead_intouch`, y esa referencia vieja ya capturada en este módulo
queda sorda al patch: llama para siempre a lo que sea que haya visto la
primera vez. Referenciar `lead_intouch._enviar_al_sink` en cada llamada
evita el problema porque la resolución del atributo pasa por el módulo recién
en el momento de la llamada.
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bot.business import lead_intouch
from bot.models import LeadInTouch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Despacha al CRM los leads pendientes o con contenido nuevo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite", type=int, default=100,
            help="Cuántos leads mirar por corrida. Acota el trabajo de un "
                 "barrido que corre cada hora.")

    def handle(self, *args, **opciones):
        sink = getattr(settings, "LEAD_SINK", "none")
        if sink == "none":
            self.stdout.write("LEAD_SINK=none: no hay destino, no hago nada.")
            return

        try:
            # `list(...)` fuerza la consulta ACÁ, adentro del try: si revienta
            # (BD caída, columna que no existe), la excepción se distingue de
            # un lote vacío en vez de perderse en la primera vuelta del `for`.
            #
            # `conflicto_en__isnull=True` saca del barrido los leads que ya
            # tienen un conflicto marcado: reintentarlos sólo repite el mismo
            # 409 cada vez, y lo que hace falta es que una persona los
            # resuelva del lado del CRM.
            pendientes = list(
                LeadInTouch.objects
                .select_related("conversation")
                .filter(despachado_en__isnull=True, conflicto_en__isnull=True)
                .order_by("actualizado")[:opciones["limite"]]
            )
            en_conflicto = (
                LeadInTouch.objects
                .filter(despachado_en__isnull=True, conflicto_en__isnull=False)
                .count()
            )
        except Exception as error:
            logger.error("[lead] no pude consultar los leads pendientes de despacho",
                         exc_info=True)
            raise CommandError(
                f"No pude consultar los leads pendientes de despacho: {error}") from error

        if not pendientes:
            mensaje = "No hay leads pendientes de despachar."
            if en_conflicto:
                # Un conflicto silencioso es peor que uno ruidoso: sin esto,
                # el barrido diría "no hay nada pendiente" mientras hay leads
                # que nadie está trabajando.
                mensaje += (f" {en_conflicto} lead(s) con conflicto de identidad "
                           "esperando resolución manual en el CRM.")
            self.stdout.write(mensaje)
            return

        despachados = 0
        fallidos = 0
        conflictos_nuevos = 0
        for lead in pendientes:
            if not self._vale_la_pena(lead):
                continue
            try:
                # `marcar_evento` NO se llama acá si el lead ya tiene un
                # evento abierto: hay que reintentar ESE, no abrir otro. Sólo
                # se abre uno si nunca tuvo.
                if not lead.evento_id:
                    lead_intouch.marcar_evento(lead)

                resultado = lead_intouch._enviar_al_sink(lead_intouch.payload_del_lead(lead))

                if resultado["resultado"] == "ok":
                    cuerpo = resultado["cuerpo"]
                    lead.despachado_en = timezone.now()
                    lead.crm_contact_id = cuerpo.get("contactId") or ""
                    lead.crm_deal_id = cuerpo.get("dealId") or ""
                    lead.save(update_fields=[
                        "despachado_en", "crm_contact_id", "crm_deal_id"])
                    despachados += 1
                elif resultado["resultado"] == "conflicto":
                    lead.conflicto_en = timezone.now()
                    lead.conflicto_motivo = resultado["motivo"]
                    lead.save(update_fields=["conflicto_en", "conflicto_motivo"])
                    conflictos_nuevos += 1
                    logger.warning(
                        "[lead] conflicto de identidad despachando %s: %s",
                        lead.conversation.wa_id, resultado["motivo"])
                else:
                    fallidos += 1
                    logger.warning(
                        "[lead] el destino externo no aceptó el reintento de %s",
                        lead.conversation.wa_id)
            except Exception:
                # Un lead que revienta no puede detener a los demás -- pero
                # queda su rastro en el log, con el número para poder ubicarlo.
                fallidos += 1
                logger.warning("[lead] falló el despacho diferido de %s",
                               lead.conversation.wa_id, exc_info=True)

        self.stdout.write(
            f"Leads despachados: {despachados}. Con fallo: {fallidos}. "
            f"Con conflicto de identidad (requieren intervención manual): "
            f"{en_conflicto + conflictos_nuevos}.")

    def _vale_la_pena(self, lead) -> bool:
        """Una fila sin ningún antecedente no es un lead que mandar.

        Mismo criterio que la guarda de apertura de `registrar_lead_del_turno`:
        el extractor puede haber creado la fila y no haber capturado nada útil.
        """
        return any(getattr(lead, campo, None)
                   for campo in lead_intouch.ANTECEDENTES_QUE_ABREN_LEAD)
