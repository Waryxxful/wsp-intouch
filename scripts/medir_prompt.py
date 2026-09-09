"""Mide tokens de un prompt (archivo o PromptVersion activo) y, opcionalmente,
de una seccion delimitada por dos marcadores de texto.

Uso:
    python scripts/medir_prompt.py /tmp/candidato.txt
    python scripts/medir_prompt.py /tmp/candidato.txt "8. MOTOR DE VENTA" "9. OBJECIONES"
"""
import sys

import tiktoken


def main():
    ruta = sys.argv[1]
    with open(ruta) as f:
        texto = f.read()
    enc = tiktoken.get_encoding("cl100k_base")
    print(f"{ruta}: {len(texto)} chars, {len(enc.encode(texto))} tokens")

    if len(sys.argv) >= 4:
        inicio, fin = sys.argv[2], sys.argv[3]
        i, j = texto.find(inicio), texto.find(fin)
        if i == -1 or j == -1 or j <= i:
            print(f"ERROR: no encontre la seccion entre {inicio!r} y {fin!r}")
            sys.exit(1)
        seccion = texto[i:j]
        print(f"  seccion {inicio!r} -> {fin!r}: {len(enc.encode(seccion))} tokens")


if __name__ == "__main__":
    main()
