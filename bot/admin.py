from django.conf import settings
from django.contrib import admin
from .models import (
    Conversation, Message, Setting, Incident, CampaignSend, Servicio, Sucursal, Reserva,
    ScrapingSource, ScrapeRun, ScrapedPage, VehiculoCatalogo, OptOut,
    EncuestaServicioTecnico, EncuestaVentaAutoNuevo, ContactoInstitucional,
)


class ClienteActivoAdminMixin(admin.ModelAdmin):
    """Fuerza cliente=CLIENTE_ACTIVO en las altas nuevas desde el admin de
    Django. Sin esto, el formulario de alta guarda la fila con el default del
    campo del modelo ("renault") salvo que el operador lo cambie a mano en el
    select de cliente -- sin relacion con el CLIENTE_ACTIVO real del proceso.
    El manager filtrado (_ClienteActivoManager) ya protege las lecturas
    (changelist/detail), esto cierra el mismo gap para la escritura. Ver
    docs/PENDIENTES.md, checklist multi-cliente."""

    def save_model(self, request, obj, form, change):
        if not change:
            obj.cliente = settings.CLIENTE_ACTIVO
        super().save_model(request, obj, form, change)


admin.site.register(Conversation)
admin.site.register(Message)
admin.site.register(Setting)
admin.site.register(Incident)
admin.site.register(OptOut)
admin.site.register(CampaignSend)
admin.site.register(Servicio, ClienteActivoAdminMixin)
admin.site.register(Sucursal, ClienteActivoAdminMixin)
admin.site.register(Reserva)
admin.site.register(ScrapingSource, ClienteActivoAdminMixin)
admin.site.register(ScrapeRun)
admin.site.register(ScrapedPage)


@admin.register(ContactoInstitucional)
class ContactoInstitucionalAdmin(ClienteActivoAdminMixin):
    # Solo lectura: el próximo scrapeo pisa cualquier edición a mano. Lo que
    # se corrige es el sitio.
    list_display = ("tipo", "valor", "etiqueta", "fuente_url", "actualizado")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(VehiculoCatalogo, ClienteActivoAdminMixin)
admin.site.register(EncuestaServicioTecnico)
admin.site.register(EncuestaVentaAutoNuevo)
