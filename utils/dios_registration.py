"""DIOS registration client with retry/backoff logic.

Ported from /home/admincrm/orquestador/dios_registration_template.py.
Implements fire-and-forget background registration with exponential backoff.
"""
import json
import os
import threading
import urllib.request

_DELAYS = [0, 10, 30, 60]


def _load_config():
    config_path = os.environ.get("DIOS_CONFIG_PATH", "/app/dios.json")
    with open(config_path, encoding="utf-8-sig") as f:
        return json.load(f)


def _post_json(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read()


def _con_retry(fn, *args, **kwargs):
    def _run():
        for delay in _DELAYS:
            if delay:
                threading.Event().wait(delay)
            try:
                fn(*args, **kwargs)
                return
            except Exception:
                pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def register_with_dios():
    dios_url = os.environ.get("DIOS_URL", "http://orquestador:9000")
    try:
        config = _load_config()
    except Exception:
        return
    _con_retry(_post_json, f"{dios_url}/internal/register-app/", config)


def notify_schema_updated():
    dios_url = os.environ.get("DIOS_URL", "http://orquestador:9000")
    try:
        config = _load_config()
    except Exception:
        return
    _con_retry(
        _post_json,
        f"{dios_url}/internal/schema-updated/",
        {"secret": config["secret"], "nombre": config["nombre"]},
    )
