"""The scoring rules, especially the ones that keep the three classes apart."""

from evals.run import avaliar, carregar
from evals import classificadores

PERFIL = {}


def _casos(*pares):
    return [
        {"id": f"c{i}", "objeto": objeto, "rotulo": rotulo}
        for i, (objeto, rotulo) in enumerate(pares)
    ]


def _fixo(resposta):
    return lambda caso, perfil=None: resposta


class TestSeparacaoDasTresClasses:
    def test_abstencao_em_relevante_nao_conta_como_perdido(self):
        # Declining to decide and confidently saying no are different
        # failures. Only the second one loses the tender for good; the
        # first one sends it to document retrieval.
        casos = _casos(("sistema de gestao", "relevante"))
        r = avaliar(_fixo("indeterminado"), casos, PERFIL)
        assert r["abstencoes_em_relevantes"] == ["c0"]
        assert r["perdidos"] == []

    def test_negativa_em_relevante_conta_como_perdido(self):
        casos = _casos(("sistema de gestao", "relevante"))
        r = avaliar(_fixo("nao_relevante"), casos, PERFIL)
        assert r["perdidos"] == ["c0"]
        assert r["abstencoes_em_relevantes"] == []

    def test_chute_onde_devia_se_abster_e_contado(self):
        # Answering yes or no on an undecidable case is wrong even when the
        # coin lands right. Without this counter the class measures nothing.
        casos = _casos(("aquisicao de itens de informatica", "indeterminado"))
        r = avaliar(_fixo("relevante"), casos, PERFIL)
        assert r["chutes_em_indeterminado"] == ["c0"]
        assert r["abstencao_correta"] == 0

    def test_abstencao_correta_e_creditada(self):
        casos = _casos(("aquisicao de itens de informatica", "indeterminado"))
        r = avaliar(_fixo("indeterminado"), casos, PERFIL)
        assert r["abstencao_correta"] == 1
        assert r["chutes_em_indeterminado"] == []


class TestMetricas:
    def test_recall_e_precisao(self):
        casos = _casos(
            ("a", "relevante"),
            ("b", "relevante"),
            ("c", "nao_relevante"),
        )
        # says relevante to everything
        r = avaliar(_fixo("relevante"), casos, PERFIL)
        assert r["recall"] == 1.0
        assert r["precisao"] == round(2 / 3, 3)

    def test_passa_exige_as_duas_metas(self):
        casos = _casos(("a", "relevante"), ("b", "nao_relevante"))
        r = avaliar(_fixo("relevante"), casos, PERFIL)
        assert r["recall"] == 1.0
        assert r["precisao"] == 0.5  # below the 0.60 target
        assert r["passa"] is False


class TestBaselinesContraOSetReal:
    def test_alerta_tudo_tem_recall_perfeito_e_precisao_ruim(self):
        casos, perfil = carregar()
        r = avaliar(classificadores.alerta_tudo, casos, perfil)
        assert r["recall"] == 1.0
        assert r["precisao"] < 0.60  # the floor a real system must beat
        assert r["passa"] is False

    def test_keyword_encontra_todos_os_positivos_do_set(self):
        # Not a good result — a symptom. Positives were sourced by keyword
        # search, so the set cannot contain a positive that keyword misses.
        # The recall figure here is an artefact of collection, documented in
        # run.RESSALVAS_DE_METODO, and the reason Phase 3 needs blind
        # random sampling.
        casos, perfil = carregar()
        r = avaliar(classificadores.keyword, casos, perfil)
        assert r["recall"] == 1.0
        assert r["perdidos"] == []

    def test_nenhum_baseline_passa(self):
        # If a baseline ever passes, the target is too easy or the set is
        # too kind. Either way it needs looking at, not celebrating.
        casos, perfil = carregar()
        for c in (classificadores.alerta_tudo, classificadores.keyword):
            assert avaliar(c, casos, perfil)["passa"] is False
