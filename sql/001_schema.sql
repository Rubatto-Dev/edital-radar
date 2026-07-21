CREATE EXTENSION IF NOT EXISTS vector;

-- Tenders pulled from the PNCP consultation API.
-- Column names are snake_case renderings of the API's camelCase fields.
CREATE TABLE IF NOT EXISTS contratacao (
    numero_controle_pncp        text PRIMARY KEY,
    objeto_compra               text NOT NULL,

    -- Never trust this as a contract total. The API returns 0.0 for
    -- confidential values (never NULL) and sometimes a unit price instead
    -- of a total. Normalised on ingest: 0.0 becomes NULL here, so
    -- "unknown" and "free" stop being the same value.
    valor_total_estimado        numeric,

    uf_sigla                    char(2),
    modalidade_id               integer,
    modalidade_nome             text,
    situacao_compra_nome        text,
    orgao_cnpj                  text,
    orgao_razao_social          text,
    data_publicacao_pncp        timestamptz,
    data_encerramento_proposta  timestamptz,

    -- objeto_compra with the publishing-platform prefix stripped
    -- ("[LICITANET] - ", "[Portal de Compras Públicas] - "). This is what
    -- gets embedded; the raw text stays in objeto_compra for display.
    objeto_limpo                text,

    -- 384 dims = paraphrase-multilingual-MiniLM-L12-v2, the local model.
    -- Changing models means changing this and re-embedding.
    objeto_embedding            vector(384),

    payload                     jsonb NOT NULL,
    ingerido_em                 timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contratacao_uf_idx ON contratacao (uf_sigla);
CREATE INDEX IF NOT EXISTS contratacao_encerramento_idx ON contratacao (data_encerramento_proposta);
CREATE INDEX IF NOT EXISTS contratacao_orgao_idx ON contratacao (orgao_cnpj);

-- Log of every ingestion attempt.
--
-- This table exists because of a specific failure mode: if the PNCP API is
-- down and the ingester swallows it, the product shows an empty result and
-- the user reads that as "no relevant tenders today" — then misses a
-- deadline. A run that failed and a run that legitimately found nothing must
-- be distinguishable after the fact, so both are recorded.
CREATE TABLE IF NOT EXISTS ingestao_execucao (
    id              bigserial PRIMARY KEY,
    data_inicial    date NOT NULL,
    data_final      date NOT NULL,
    modalidade_id   integer NOT NULL,
    status          text NOT NULL CHECK (status IN ('ok', 'parcial', 'falha')),
    paginas_lidas   integer NOT NULL DEFAULT 0,
    registros_novos integer NOT NULL DEFAULT 0,
    erro            text,
    iniciado_em     timestamptz NOT NULL DEFAULT now(),
    terminado_em    timestamptz
);

CREATE INDEX IF NOT EXISTS ingestao_execucao_iniciado_idx ON ingestao_execucao (iniciado_em DESC);
