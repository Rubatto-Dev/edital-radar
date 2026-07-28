"""Notification, Phase 2 style: an auditable record, not a real send.

No company is using this yet — that is Phase 4. Wiring up SMTP or a Slack
webhook now would be infrastructure with nobody on the other end. What the
issue actually needs (an alert a user could eventually see, and a trail
proving the system noticed it) is satisfied by marking the decision as
notified and logging it. Swap in a real channel in Phase 4, when there is
a real company to notify.

Whether to notify is a deterministic rule over a judgment already made, not
another judgment call — so it does not cost an LLM turn and is not one of
the agent's tools in app/agente.py.
"""

import logging

from app.agente import DecisaoAgente

log = logging.getLogger(__name__)


def deve_notificar(decisao: DecisaoAgente) -> bool:
    """Only a `relevante` verdict notifies. `lote_misto` is a caveat on a
    relevant alert (see app/filtros.py's reasoning for soft caveats), not a
    reason to withhold it — the user decides, the system does not filter
    silently."""
    return decisao.classe == "relevante"


NOTIFICAR_SQL = """
UPDATE decisao_agente SET notificado_em = now()
 WHERE id = %s
RETURNING notificado_em
"""


def notificar(conn, decisao_id: int) -> None:
    """Marks a decision as notified. Idempotent in effect — calling it again
    just moves the timestamp, there is nothing to double-send."""
    conn.execute(NOTIFICAR_SQL, (decisao_id,))


def notificar_se_relevante(conn, decisao_id: int, decisao: DecisaoAgente) -> bool:
    """The entry point callers use: decides, writes if warranted, logs
    either way. Returns whether it notified."""
    if not deve_notificar(decisao):
        log.info("decisao %s (%s) nao notifica", decisao_id, decisao.classe)
        return False

    notificar(conn, decisao_id)
    log.info(
        "decisao %s notificada: %s (lote_misto=%s)",
        decisao_id,
        decisao.justificativa,
        decisao.lote_misto,
    )
    return True
