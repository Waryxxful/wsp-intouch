#!/usr/bin/env bash
# Emite por stdout el DDL de Supabase para el schema RAG de un cliente, a
# partir del template bot/rag/schema.sql (placeholder __CLIENTE__).
#
#   scripts/rag_schema_para.sh cavem | less        # revisar
#   scripts/rag_schema_para.sh cavem > /tmp/cavem.sql
#
# Existe para que el nombre de schema no se reemplace a mano: ese reemplazo
# manual es el mismo tipo de error que mando 297 chunks de Astara al schema
# de Renault (docs/PENDIENTES.md, 2026-08-27/28).
#
# El SQL se pega en el SQL editor de Supabase. Despues hay UN paso que no es
# SQL y se olvida: agregar el schema en Settings -> API -> Exposed schemas,
# si no PostgREST responde PGRST106 aunque la tabla exista.
set -euo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$raiz/bot/rag/schema.sql"

cliente="${1:-}"
if [[ -z "$cliente" ]]; then
    echo "uso: $(basename "$0") <cliente>" >&2
    exit 64
fi

# La lista valida se lee de bot/models.py para no duplicarla acá y que se
# desincronice al agregar una marca. El awk (en vez de un grep de una sola
# linea) tolera que CLIENTE_CHOICES este partido en varias lineas -- como
# quedo al agregar "intouch" -- ademas del formato de una sola linea.
mapfile -t validos < <(awk '
    /^CLIENTE_CHOICES = \[/ { found=1 }
    found { print }
    found && /\]/ { exit }
' "$raiz/bot/models.py" | grep -oE '\("[a-z_]+"' | tr -d '("')
if [[ ${#validos[@]} -eq 0 ]]; then
    echo "error: no se pudo leer CLIENTE_CHOICES de bot/models.py" >&2
    exit 70
fi
for valido in "${validos[@]}"; do
    if [[ "$cliente" == "$valido" ]]; then
        exec sed "s/__CLIENTE__/$cliente/g" "$template"
    fi
done

echo "error: cliente '$cliente' no esta en CLIENTE_CHOICES (${validos[*]})" >&2
exit 64
