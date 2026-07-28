"""No result may be returned without a citation and a way to verify it.

link_pncp is checked against real PNCP control-number/URL pairs, sourced from
live pncp.gov.br links (see app/consulta.py) — not invented.
"""

import datetime

from app.consulta import buscar, link_pncp


class ConexaoFalsa:
    def __init__(self, linhas):
        self.linhas = linhas
        self.chamadas = []

    def execute(self, sql, params=None):
        self.chamadas.append((sql, params))
        return self

    def fetchall(self):
        return self.linhas


class TestLinkPncp:
    def test_constroi_link_publico_a_partir_do_numero_de_controle(self):
        # Real pair, confirmed against a live pncp.gov.br/app/editais URL.
        assert (
            link_pncp("07753868000101-1-000003/2026")
            == "https://pncp.gov.br/app/editais/07753868000101/2026/3"
        )

    def test_zeros_a_esquerda_do_sequencial_sao_removidos(self):
        assert link_pncp("47018676000176-1-000111/2026") == (
            "https://pncp.gov.br/app/editais/47018676000176/2026/111"
        )

    def test_numero_de_controle_fora_do_formato_esperado_nao_quebra(self):
        assert link_pncp("formato-inesperado") is None


class TestBuscar:
    def test_todo_resultado_carrega_trecho_e_link(self):
        conn = ConexaoFalsa(
            [
                (
                    "07753868000101-1-000003/2026",
                    "Locação de sistema de gestão municipal",
                    "Prefeitura de Exemplo",
                    "SC",
                    120000.0,
                    None,
                    0.812345,
                )
            ]
        )

        resultados = buscar(conn, [0.1] * 384, datetime.datetime.now(), limite=5)

        assert len(resultados) == 1
        r = resultados[0]
        assert r["trecho"] == "Locação de sistema de gestão municipal"
        assert r["link"] == "https://pncp.gov.br/app/editais/07753868000101/2026/3"
        assert r["similaridade"] == 0.812

    def test_prazo_ausente_vira_none_nao_erro(self):
        conn = ConexaoFalsa(
            [("num", "objeto", "orgao", "SC", None, None, 0.5)]
        )
        resultados = buscar(conn, [0.0] * 384, datetime.datetime.now())
        assert resultados[0]["prazo"] is None

    def test_passa_o_vetor_e_o_limite_pra_query(self):
        conn = ConexaoFalsa([])
        buscar(conn, [0.2] * 384, datetime.datetime.now(), limite=3)

        sql, params = conn.chamadas[0]
        assert "objeto_embedding <=>" in sql
        assert params["limite"] == 3
