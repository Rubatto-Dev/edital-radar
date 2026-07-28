"""Notification here is a deterministic rule over a decision, not a real
send — see app/notificacao.py. Only that rule and the write are tested;
there is no channel to mock.
"""

from app.agente import DecisaoAgente
from app.notificacao import deve_notificar, notificar_se_relevante


def _decisao(classe, lote_misto=False):
    return DecisaoAgente(
        classe=classe,
        justificativa="justificativa",
        lote_misto=lote_misto,
        ferramentas_chamadas=["calcular_prazo"],
        anexo_baixado=False,
        custo_usd=0.001,
        latencia_s=1.0,
        modelo="claude-haiku-4-5",
    )


class ConexaoFalsa:
    def __init__(self):
        self.chamadas = []

    def execute(self, sql, params=None):
        self.chamadas.append((sql, params))
        return self


class TestDeveNotificar:
    def test_relevante_notifica(self):
        assert deve_notificar(_decisao("relevante")) is True

    def test_nao_relevante_nao_notifica(self):
        assert deve_notificar(_decisao("nao_relevante")) is False

    def test_indeterminado_nao_notifica(self):
        assert deve_notificar(_decisao("indeterminado")) is False

    def test_lote_misto_nao_impede_notificacao(self):
        # A caveat on a relevant alert, not a reason to withhold it — same
        # reasoning as app/filtros.py's soft caveats.
        assert deve_notificar(_decisao("relevante", lote_misto=True)) is True


class TestNotificarSeRelevante:
    def test_relevante_grava_notificado_em(self):
        conn = ConexaoFalsa()
        notificou = notificar_se_relevante(conn, 1, _decisao("relevante"))

        assert notificou is True
        assert len(conn.chamadas) == 1
        sql, params = conn.chamadas[0]
        assert "notificado_em" in sql
        assert params == (1,)

    def test_nao_relevante_nao_toca_o_banco(self):
        conn = ConexaoFalsa()
        notificou = notificar_se_relevante(conn, 1, _decisao("nao_relevante"))

        assert notificou is False
        assert conn.chamadas == []
