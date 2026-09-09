import json
from pathlib import Path

from django.utils import timezone


def actualizar_env_docker(path: Path, valores: dict) -> None:
    """Reemplaza (o agrega si no existe) cada KEY=valor del archivo .env dado.
    Preserva el resto del archivo tal cual, linea por linea -- este archivo
    tiene secretos reales (tokens de WhatsApp, etc.), nunca se reescribe
    completo desde una estructura en memoria."""
    lineas = path.read_text().splitlines(keepends=True) if path.exists() else []
    pendientes = dict(valores)
    resultado = []
    for linea in lineas:
        sin_comentario = not linea.lstrip().startswith("#")
        clave = linea.split("=", 1)[0].strip() if "=" in linea and sin_comentario else None
        if clave in pendientes:
            resultado.append(f"{clave}={pendientes.pop(clave)}\n")
        else:
            resultado.append(linea)
    for clave, valor in pendientes.items():
        resultado.append(f"{clave}={valor}\n")
    path.write_text("".join(resultado))


def escribir_flip_pendiente(path: Path, target: str, solicitado_por: str) -> None:
    path.write_text(json.dumps({
        "target_cliente": target,
        "solicitado_por": solicitado_por,
        "solicitado_en": timezone.now().isoformat(),
    }))


def leer_flip_pendiente(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
