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

UN LEAD EN CONFLICTO (409) NO SE REINTENTA CADA CORRIDA: la ambigüedad de
identidad que señaló el receptor no la resuelve un reintento, la resuelve una
persona del lado del CRM -- reintentar cada 15 minutos sólo repetiría el mismo
409 para siempre. Pero excluirlo PARA SIEMPRE tiene el problema inverso: si esa
persona ya resolvió el conflicto del lado del CRM, el bot no tiene forma de
enterarse sola, y el lead queda marcado "conflicto" y fuera del barrido hasta
que alguien toque el reintento manual del panel.

La solución es una cadencia lenta en vez de nunca: `conflicto_en` es la marca
de cuándo se detectó, y este barrido reintenta los conflictos cuya marca es
más vieja que `--umbral-conflicto-horas`. Sale barato reintentar uno que sigue
sin resolverse -- medido y probado del lado del receptor, un 409 de identidad
es una consulta indexada, sin escrituras ni registros nuevos -- y si alguien
ya lo resolvió, el receptor devuelve los ids correctos y el lead se sella
solo, sin paso humano. Si el 409 se repite, se actualiza `conflicto_en` a
ahora (el reloj vuelve a correr) junto con el motivo, para no requintentarlo
en la corrida siguiente.

Por eso la consulta de pendientes queda en DOS grupos con trato distinto: los
pendientes sin conflicto (todos, como siempre) y los conflictos VENCIDOS (más
viejos que el umbral). Los conflictos que todavía no vencieron se cuentan pero
no se tocan, y ese número importa tanto como los otros dos: es lo que dice
cuántos leads siguen esperando una persona del lado del CRM. Un conflicto
silencioso es peor que uno ruidoso.

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
from datetime import timedelta

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
        parser.add_argument(
            "--umbral-conflicto-horas", type=float, default=12,
            help="Antigüedad mínima, en horas, de un conflicto de identidad "
                 "(409) para que este barrido lo vuelva a intentar. Tiene que "
                 "ser mucho más lento que la cadencia normal del barrido "
                 "(cada 15 minutos): reintentar un conflicto sale casi gratis "
                 "del lado del receptor -- una consulta indexada, sin "
                 "escrituras -- pero lo que resuelve la ambigüedad es una "
                 "persona en el CRM, no el reloj. 12 horas es un punto medio "
                 "en el rango recomendado (6-24h): más de un ciclo laboral "
                 "para que alguien lo revise, pero no un día entero de espera "
                 "si ya lo resolvió temprano.")

    def handle(self, *args, **opciones):
        sink = getattr(settings, "LEAD_SINK", "none")
        if sink == "none":
            self.stdout.write("LEAD_SINK=none: no hay destino, no hago nada.")
            return

        umbral = timezone.now() - timedelta(hours=opciones["umbral_conflicto_horas"])

        try:
            # `list(...)` fuerza la consulta ACÁ, adentro del try: si revienta
            # (BD caída, columna que no existe), la excepción se distingue de
            # un lote vacío en vez de perderse en la primera vuelta del `for`.
            #
            # Dos grupos con trato distinto:
            # - `pendientes`: nunca tuvieron conflicto. Como siempre.
            # - `vencidos`: tuvieron un conflicto (409), pero la marca es más
            #   vieja que el umbral -- se reintentan porque ya pasó tiempo
            #   suficiente para que una persona lo haya resuelto del lado del
            #   CRM, y el costo de estar equivocados es bajo (ver el docstring
            #   del módulo).
            pendientes = list(
                LeadInTouch.objects
                .select_related("conversation")
                .filter(despachado_en__isnull=True, conflicto_en__isnull=True)
                .order_by("actualizado")[:opciones["limite"]]
            )
            vencidos = list(
                LeadInTouch.objects
                .select_related("conversation")
                .filter(despachado_en__isnull=True, conflicto_en__isnull=False,
                        conflicto_en__lt=umbral)
                .order_by("conflicto_en")[:opciones["limite"]]
            )
            # Los que todavía no vencieron: no se tocan, pero se cuentan --
            # es el número que dice cuántos leads siguen esperando una
            # persona del lado del CRM.
            vigentes = (
                LeadInTouch.objects
                .filter(despachado_en__isnull=True, conflicto_en__isnull=False,
                        conflicto_en__gte=umbral)
                .count()
            )
        except Exception as error:
            logger.error("[lead] no pude consultar los leads pendientes de despacho",
                         exc_info=True)
            raise CommandError(
                f"No pude consultar los leads pendientes de despacho: {error}") from error

        if not pendientes and not vencidos:
            mensaje = "No hay leads pendientes de despachar."
            if vigentes:
                # Un conflicto silencioso es peor que uno ruidoso: sin esto,
                # el barrido diría "no hay nada pendiente" mientras hay leads
                # que nadie está trabajando.
                mensaje += (f" {vigentes} lead(s) con conflicto de identidad "
                           "esperando resolución manual en el CRM.")
            self.stdout.write(mensaje)
            return

        procesados = despachados = fallidos = conflictos_nuevos = 0
        for lead in pendientes:
            if not self._vale_la_pena(lead):
                continue
            procesados += 1
            try:
                resultado = self._procesar_lead(lead, viene_de_conflicto=False)
            except Exception:
                # Un lead que revienta no puede detener a los demás -- pero
                # queda su rastro en el log, con el número para poder ubicarlo.
                fallidos += 1
                logger.warning("[lead] falló el despacho diferido de %s",
                               lead.conversation.wa_id, exc_info=True)
                continue
            if resultado == "ok":
                despachados += 1
            elif resultado == "conflicto":
                conflictos_nuevos += 1
            else:
                fallidos += 1

        reintentados = resueltos = persistentes = fallidos_vencidos = 0
        for lead in vencidos:
            if not self._vale_la_pena(lead):
                continue
            reintentados += 1
            try:
                resultado = self._procesar_lead(lead, viene_de_conflicto=True)
            except Exception:
                fallidos_vencidos += 1
                logger.warning(
                    "[lead] falló el reintento del conflicto vencido de %s",
                    lead.conversation.wa_id, exc_info=True)
                continue
            if resultado == "ok":
                resueltos += 1
            elif resultado == "conflicto":
                persistentes += 1
            else:
                fallidos_vencidos += 1

        self.stdout.write(
            f"Pendientes procesados: {procesados} (despachados: {despachados}, "
            f"con fallo: {fallidos}, con conflicto nuevo: {conflictos_nuevos}). "
            f"Conflictos vencidos reintentados: {reintentados} "
            f"(resueltos: {resueltos}, siguen en conflicto: {persistentes}, "
            f"con fallo: {fallidos_vencidos}). "
            f"Conflictos sin vencer (esperando resolución manual en el CRM): "
            f"{vigentes}.")

    def _procesar_lead(self, lead, *, viene_de_conflicto: bool) -> str:
        """Envía (o reenvía) un lead al sink y aplica el efecto sobre la fila.

        Devuelve el resultado de `_enviar_al_sink`: "ok", "conflicto" o
        "fallo". `viene_de_conflicto` sólo cambia qué pasa ante un "ok": si el
        lead venía de un conflicto vencido, un "ok" significa que el receptor
        aceptó el mismo evento que antes rechazaba -- la ambigüedad ya se
        resolvió del lado del CRM -- así que acá se limpian `conflicto_en` y
        `conflicto_motivo`, el mismo patrón que ya usa `marcar_evento` cuando
        un cambio de contenido reabre un lead en conflicto.
        """
        # `marcar_evento` NO se llama acá si el lead ya tiene un evento
        # abierto: hay que reintentar ESE, no abrir otro. Sólo se abre uno si
        # nunca tuvo -- y un lead que viene de un conflicto vencido ya tiene
        # el suyo desde el intento que lo marcó, así que este reintento
        # manda EXACTAMENTE el mismo evento que el receptor rechazó.
        if not lead.evento_id:
            lead_intouch.marcar_evento(lead)

        resultado = lead_intouch._enviar_al_sink(lead_intouch.payload_del_lead(lead))

        if resultado["resultado"] == "ok":
            cuerpo = resultado["cuerpo"]
            lead.despachado_en = timezone.now()
            lead.crm_contact_id = cuerpo.get("contactId") or ""
            lead.crm_deal_id = cuerpo.get("dealId") or ""
            campos = ["despachado_en", "crm_contact_id", "crm_deal_id"]
            if viene_de_conflicto:
                lead.conflicto_en = None
                lead.conflicto_motivo = ""
                campos += ["conflicto_en", "conflicto_motivo"]
            lead.save(update_fields=campos)
            return "ok"

        if resultado["resultado"] == "conflicto":
            # Vencido o nuevo, el trato es el mismo: se marca (o remarca)
            # ahora. Para uno vencido esto es lo que hace que el reloj vuelva
            # a correr -- sin esto, el mismo lead se reintentaría en TODAS las
            # corridas siguientes, que es justo lo que este diseño evita.
            lead.conflicto_en = timezone.now()
            lead.conflicto_motivo = resultado["motivo"]
            lead.save(update_fields=["conflicto_en", "conflicto_motivo"])
            logger.warning(
                "[lead] conflicto de identidad despachando %s: %s",
                lead.conversation.wa_id, resultado["motivo"])
            return "conflicto"

        logger.warning(
            "[lead] el destino externo no aceptó el reintento de %s",
            lead.conversation.wa_id)
        return "fallo"

    def _vale_la_pena(self, lead) -> bool:
        """Una fila sin ningún antecedente no es un lead que mandar.

        Mismo criterio que la guarda de apertura de `registrar_lead_del_turno`:
        el extractor puede haber creado la fila y no haber capturado nada útil.
        """
        return any(getattr(lead, campo, None)
                   for campo in lead_intouch.ANTECEDENTES_QUE_ABREN_LEAD)
