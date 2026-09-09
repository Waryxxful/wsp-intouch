from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG

from .agendamiento import AgendamientoAgent
from .comercial import ComercialAgent
from .confirmacion import ConfirmacionAgent
from .custom import CustomPromptAgent
from .encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent
from .encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent
from .faq import FaqAgent

# Registro simple, sin auto-discovery: cada especialista de código nuevo se
# agrega como una línea acá. Ver /home/admincrm/docs-repo/biblia_bots.md, Parte I paso 9.
#
# UNO SOLO, a propósito (spec §2.2): un especialista visible es un especialista
# al que el LLM puede rutear MAL, y "soporte" contra "comercial" es un límite
# semántico difuso -- un contacto que se queja de un servicio calza con los
# dos. El prompt de `comercial` ya distingue consulta comercial de soporte,
# empleo y proveedores, y las deriva con `crear_caso`.
AGENTS = {
    "comercial": ComercialAgent,
}

# Los especialistas heredados del bot automotriz NO se registran. Su código,
# sus modelos y sus tools quedan en el repo por linaje y -- sobre todo -- para
# conservar la cobertura de tests que prueba las defensas de la biblia §IV.1,
# pero fuera de AGENTS: `build_agent_registry` alimenta la lista de opciones
# que ve el supervisor, así que un especialista fuera de este dict es invisible
# para el ruteo. El modo de falla que se evita es concreto: un contacto de
# InTouch preguntando por atención al cliente calza semánticamente con
# "agendar una hora de taller".
#
# Ver el CLAUDE.md de este repo: no agregarles datos ni chequeos del doctor.
AGENTES_NO_REGISTRADOS = {
    "agendamiento": AgendamientoAgent,
    "confirmacion": ConfirmacionAgent,
    "faq": FaqAgent,
    "encuesta_servicio_tecnico": EncuestaServicioTecnicoAgent,
    "encuesta_venta_auto_nuevo": EncuestaVentaAutoNuevoAgent,
}

# Un CustomSpecialist (creado desde el panel) no puede pisar a un especialista
# de código real ni al prompt global de comportamiento -- se valida en
# admin_panel/views.py::api_specialists. Se reservan también los slugs de los
# desregistrados: reusar uno reactivaría un prompt de otro dominio.
RESERVED_SLUGS = (frozenset(AGENTS.keys()) | frozenset(AGENTES_NO_REGISTRADOS.keys())
                  | {GLOBAL_PROMPT_SLUG})


def build_agent_registry() -> dict:
    """Registro completo de especialistas disponibles PARA ESTE TURNO: los
    estáticos de código + los CustomSpecialist guardados en BD. Se llama de
    nuevo en cada turno (bot/flow/graph.py), sin cachear -- así un especialista
    creado/editado/borrado desde el panel queda disponible sin reiniciar el
    proceso. Hace una query ORM real: debe llamarse vía sync_to_async desde un
    nodo async."""
    from bot.models import CustomSpecialist

    registry = {slug: cls() for slug, cls in AGENTS.items()}
    for row in CustomSpecialist.objects.all():
        registry[row.slug] = CustomPromptAgent(row)
    return registry
