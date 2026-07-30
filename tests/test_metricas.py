"""The panel has to answer honestly when there is nothing to report.

`data/custo-ledger.jsonl` is gitignored, so a fresh clone, the container and
CI all start with no ledger. "No calls yet" and "the panel is broken" must not
look the same, and neither may crash the endpoint.
"""

from app.metricas import (
    ler_ledger,
    percentil,
    resumir_baseline,
    resumir_ledger,
)


class TestLedgerAusente:
    def test_arquivo_inexistente_e_lista_vazia(self, tmp_path):
        assert ler_ledger(tmp_path / "nao-existe.jsonl") == []

    def test_resumo_vazio_nao_inventa_numero(self):
        r = resumir_ledger([])
        assert r["chamadas"] == 0
        assert r["custo_total_usd"] == 0
        # None, not zero: zero cost per call is a claim, absence is the truth.
        assert r["custo_medio_usd"] is None
        assert r["latencia_p50_s"] is None
        assert r["por_dia"] == []

    def test_linhas_em_branco_sao_ignoradas(self, tmp_path):
        caminho = tmp_path / "ledger.jsonl"
        caminho.write_text(
            '{"data": "2026-07-29", "custo_usd": 0.001, "latencia_s": 1.0}\n\n\n',
            encoding="utf-8",
        )
        assert len(ler_ledger(caminho)) == 1


class TestPercentil:
    def test_lista_vazia(self):
        assert percentil([], 50) is None

    def test_p50_em_amostra_par_pega_o_menor_dos_centrais(self):
        # The bug this exists to prevent: round() uses banker's rounding, so
        # round(1.5) == 2 and p50 of two samples would return the larger one.
        assert percentil([1.0, 2.0], 50) == 1.0

    def test_p50_e_p95(self):
        valores = [1.0, 2.0, 3.0, 4.0]
        assert percentil(valores, 50) == 2.0
        assert percentil(valores, 95) == 4.0

    def test_sempre_devolve_um_valor_observado(self):
        # Nearest-rank never interpolates, so the p95 of a small sample is a
        # latency that actually happened, not one the maths made up.
        valores = [0.5, 10.0]
        assert percentil(valores, 95) in valores

    def test_amostra_unica(self):
        assert percentil([7.0], 95) == 7.0


class TestResumoDoLedger:
    REGISTROS = [
        {"data": "2026-07-28", "custo_usd": 0.010, "latencia_s": 2.0},
        {"data": "2026-07-29", "custo_usd": 0.002, "latencia_s": 4.0},
        {"data": "2026-07-29", "custo_usd": 0.004, "latencia_s": 6.0},
    ]

    def test_agrega_por_dia_em_ordem(self):
        r = resumir_ledger(self.REGISTROS)
        assert [d["data"] for d in r["por_dia"]] == ["2026-07-28", "2026-07-29"]
        assert r["por_dia"][1]["chamadas"] == 2
        assert r["por_dia"][1]["custo_usd"] == 0.006

    def test_gasto_do_ultimo_dia_e_do_dia_mais_recente(self):
        # Not the total, and not the first line of the file: the daily cap in
        # app.custo is a per-day rule, so the panel has to compare against the
        # same window the cap uses.
        assert resumir_ledger(self.REGISTROS)["gasto_no_ultimo_dia_usd"] == 0.006

    def test_custo_medio_e_por_chamada(self):
        r = resumir_ledger(self.REGISTROS)
        assert r["chamadas"] == 3
        assert r["custo_medio_usd"] == round(0.016 / 3, 6)


class TestQualidade:
    def test_le_o_baseline_do_repo(self):
        q = resumir_baseline()
        assert q["disponivel"] is True
        nomes = {c["nome"] for c in q["classificadores"]}
        assert {"alerta_tudo", "keyword", "vetorial", "cascata"} <= nomes

    def test_nenhum_classificador_atinge_as_metas(self):
        # If this ever fails, the panel is not wrong — the project moved, and
        # the README's headline numbers need rewriting.
        q = resumir_baseline()
        assert all(not c["atinge_metas"] for c in q["classificadores"])
