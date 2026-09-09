-- TEMPLATE, no se corre tal cual: el nombre de schema es el placeholder
-- intouch. Generar el SQL de una marca con
--     scripts/rag_schema_para.sh <cliente>
-- y pegar la salida en el SQL editor de Supabase (o pasarla a su CLI de
-- migraciones). NO se ejecuta via Django.
--
-- Antes decia "repetir este archivo con el nombre de schema cambiado": ese
-- reemplazo a mano es el mismo tipo de error que ya mando 297 chunks de
-- Astara al schema de Renault (docs/PENDIENTES.md, 2026-08-27/28). El script
-- lo hace determinista y valida que el cliente sea uno de CLIENTE_CHOICES.
--
-- Ver docs/superpowers/specs/2026-08-25-rag-hibrido-rerank-multimarca-design.md
-- Parte 1. Reemplaza al schema `public.documentos_conocimiento` original
-- (docs/superpowers/specs/2026-08-17-rag-agentico-design.md) -- la migracion
-- de las filas existentes es un paso manual, ver Task 9 de este plan.

create extension if not exists vector;

-- Una marca = un schema -- ver Global Constraints del spec.
create schema if not exists intouch;

create table intouch.documentos_conocimiento (
    id bigint generated always as identity primary key,
    fuente_url text not null,
    scraped_page_id bigint,  -- referencia informativa al ScrapedPage.id de Django, sin FK real
    contenido text not null,
    embedding vector(1536),  -- reducido de 3072 (default de gemini-embedding-2) via output_dimensionality:
                              -- pgvector no permite indexar (hnsw/ivfflat) columnas de mas de 2000 dimensiones
    categoria text,
    clasificacion_fallo boolean,
    -- Columna generada para busqueda lexica (full-text). 'spanish' usa el
    -- diccionario de stemming en español de Postgres (no el default 'english').
    contenido_fts tsvector generated always as (to_tsvector('spanish', contenido)) stored,
    creado_en timestamptz not null default now()
);

create index on intouch.documentos_conocimiento using hnsw (embedding vector_cosine_ops);
create index on intouch.documentos_conocimiento using gin (contenido_fts);

-- Retrieval hibrido: fusiona el ranking semantico (coseno) y el lexico
-- (ts_rank) por Reciprocal Rank Fusion (RRF) en vez de devolver solo uno de
-- los dos. k=60 es la constante de suavizado estandar de RRF. Sin filtro de
-- similitud minima aca a proposito -- el filtro de relevancia real vive
-- despues del rerank (bot/rag/tool.py), no aca (ver "Hallazgo de esta
-- investigacion" al inicio de este plan).
create or replace function intouch.match_documentos(
    query_embedding vector(1536),
    query_texto text,
    match_count int default 20,
    filtro_categoria text default null
)
returns table (id bigint, fuente_url text, contenido text, categoria text, similarity float, rrf_score float)
language sql stable
as $$
    with semantico as (
        select id, row_number() over (order by embedding <=> query_embedding) as rank,
               1 - (embedding <=> query_embedding) as similarity
        from intouch.documentos_conocimiento
        where filtro_categoria is null or categoria = filtro_categoria
        order by embedding <=> query_embedding
        limit match_count * 2
    ),
    lexico as (
        select id, row_number() over (
            order by ts_rank(contenido_fts, websearch_to_tsquery('spanish', query_texto)) desc
        ) as rank
        from intouch.documentos_conocimiento
        where (filtro_categoria is null or categoria = filtro_categoria)
          and contenido_fts @@ websearch_to_tsquery('spanish', query_texto)
        order by ts_rank(contenido_fts, websearch_to_tsquery('spanish', query_texto)) desc
        limit match_count * 2
    )
    select d.id, d.fuente_url, d.contenido, d.categoria,
           coalesce(s.similarity, 0) as similarity,
           coalesce(1.0 / (60 + s.rank), 0) + coalesce(1.0 / (60 + l.rank), 0) as rrf_score
    from intouch.documentos_conocimiento d
    left join semantico s on s.id = d.id
    left join lexico l on l.id = d.id
    where s.id is not null or l.id is not null
    order by rrf_score desc
    limit match_count;
$$;

-- Grants. NO estaban en este archivo hasta el 2026-09-02: los schemas
-- renault/astara funcionan porque en su momento se corrieron a mano, asi que
-- una marca nueva creada siguiendo solo el DDL de arriba quedaba con una
-- tabla que PostgREST no puede leer (PGRST106 / permission denied) y el
-- sintoma aparecia recien al indexar. Replica exactamente los ACL que hoy
-- tiene renault en el proyecto real.
--
-- El bot usa la service_role key (bot/rag/cliente.py), que bypassa RLS; anon
-- y authenticated reciben solo usage del schema, sin privilegios sobre la
-- tabla, igual que renault.
grant usage on schema intouch to anon, authenticated, service_role;
grant all privileges on table intouch.documentos_conocimiento to service_role;
grant execute on function intouch.match_documentos(vector, text, int, text) to anon, authenticated, service_role;

-- RLS activa sin ninguna policy = nadie lee salvo service_role (que la
-- bypassa). Es el estado de renault y astara hoy; dejarla desactivada
-- expondria la base de conocimiento al rol anon del proyecto.
alter table intouch.documentos_conocimiento enable row level security;
