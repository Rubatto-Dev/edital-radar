-- Log of every daily agent run — same reasoning as ingestao_execucao: a
-- killed process must leave a record that says it failed, and "found
-- nothing relevant" must be distinguishable from "the run never finished".
CREATE TABLE IF NOT EXISTS agente_execucao (
    id                    bigserial PRIMARY KEY,
    data_inicial          date NOT NULL,
    data_final            date NOT NULL,
    status                text NOT NULL CHECK (status IN ('ok', 'parcial', 'falha')),
    candidatos_avaliados  integer NOT NULL DEFAULT 0,
    decisoes_relevantes   integer NOT NULL DEFAULT 0,
    erro                  text,
    iniciado_em           timestamptz NOT NULL DEFAULT now(),
    terminado_em          timestamptz
);

CREATE INDEX IF NOT EXISTS agente_execucao_iniciado_idx ON agente_execucao (iniciado_em DESC);
