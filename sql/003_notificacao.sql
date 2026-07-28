-- Notification is not a judgment call — it is a deterministic rule over a
-- judgment already made (classe == relevante), so it stays out of the
-- agent's tool-calling loop entirely (no LLM turn spent deciding it) and
-- lives as a column on the decision it notifies about, not a new table.
ALTER TABLE decisao_agente
    ADD COLUMN IF NOT EXISTS notificado_em timestamptz;
