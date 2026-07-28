-- The agent's auditable decision log (Phase 2.2). Append-only, one row per
-- judgment: a tender can be re-judged (re-ingest, prompt change), and each
-- judgment is its own record — the same reasoning as ingestao_execucao
-- never overwriting a past run. This is also the raw material Phase 3
-- (observability) reads for cost/latency dashboards and regression checks.
CREATE TABLE IF NOT EXISTS decisao_agente (
    id                    bigserial PRIMARY KEY,
    numero_controle_pncp  text NOT NULL REFERENCES contratacao (numero_controle_pncp) ON DELETE CASCADE,
    classe                text NOT NULL CHECK (classe IN ('relevante', 'nao_relevante', 'indeterminado')),
    justificativa         text NOT NULL,
    lote_misto            boolean NOT NULL DEFAULT false,
    ferramentas_chamadas  text[] NOT NULL DEFAULT '{}',
    anexo_baixado         boolean NOT NULL DEFAULT false,
    custo_usd             numeric NOT NULL,
    latencia_s            numeric NOT NULL,
    modelo                text NOT NULL,
    decidido_em           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decisao_agente_numero_idx ON decisao_agente (numero_controle_pncp);
CREATE INDEX IF NOT EXISTS decisao_agente_decidido_idx ON decisao_agente (decidido_em DESC);
