from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG

from .agendamiento import AgendamientoAgent
from .confirmacion import ConfirmacionAgent
from .custom import CustomPromptAgent
from .encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent
from .encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent
from .faq import FaqAgent

# Registro simple, sin auto-discovery: cada especialista de codigo nuevo se
# agrega como una linea aqui. Ver /home/admincrm/docs-repo/biblia_bots.md, Parte I paso 9.
AGENTS = {
    "agendamiento": AgendamientoAgent,
    "confirmacion": ConfirmacionAgent,
    "faq": FaqAgent,
}

# Los dos especialistas de encuesta de wsp_demo (Renault) NO se registran en
# Cavem. El codigo, los modelos y las tools quedan en el repo por linaje, pero
# fuera de AGENTS: build_agent_registry alimenta la lista de opciones que ve el
# supervisor, y un especialista visible es un especialista al que el LLM puede
# rutear. Cavem no tiene esas campanas salientes, asi que la unica forma de
# llegar ahi seria un ruteo equivocado -- y el modo de falla es feo y muy
# visible: un cliente que se queja del taller ("me atendieron pesimo") calza
# semanticamente con "encuesta de satisfaccion de Servicio Tecnico" y el bot le
# empezaria a pedir notas del 1 al 10 en vez de atender el reclamo.
# Para reactivarlos hace falta ademas darlos de alta como campana con su
# plantilla de Meta.
AGENTES_NO_REGISTRADOS = {
    "encuesta_servicio_tecnico": EncuestaServicioTecnicoAgent,
    "encuesta_venta_auto_nuevo": EncuestaVentaAutoNuevoAgent,
}

# Un CustomSpecialist (creado desde el panel) no puede pisar a un especialista
# de codigo real ni al prompt global de comportamiento -- se valida en
# admin_panel/views.py::api_specialists.
RESERVED_SLUGS = frozenset(AGENTS.keys()) | {GLOBAL_PROMPT_SLUG}


def build_agent_registry() -> dict:
    """Registro completo de especialistas disponibles PARA ESTE TURNO: los
    estaticos de codigo + los CustomSpecialist guardados en BD. Se llama de
    nuevo en cada turno (bot/flow/graph.py), sin cachear -- asi un
    especialista creado/editado/borrado desde el panel queda disponible sin
    reiniciar el proceso. Hace una query ORM real: debe llamarse via
    sync_to_async desde un nodo async."""
    from bot.models import CustomSpecialist
    from ._common import refrescar_sucursal_unica

    # Corre aca porque build_agent_registry() se invoca bajo sync_to_async una
    # vez por turno: es el punto sincrono natural para refrescar los bloques de
    # prompt que necesitan la BD. Ver _common.bloque_sucursal_unica.
    refrescar_sucursal_unica()
    registry = {slug: cls() for slug, cls in AGENTS.items()}
    for row in CustomSpecialist.objects.all():
        registry[row.slug] = CustomPromptAgent(row)
    return registry
