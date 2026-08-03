"""The scoring rules, especially the ones that keep the three classes apart."""

from typing import ClassVar

from evals import classificadores
from evals.run import (
    GRATUITOS,
    avaliar,
    carregar,
    carregar_baseline,
    comparar_com_baseline,
)

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


class TestGratuitosNaoGastamDinheiro:
    """CI runs `evals.run` with no `--classificador`, so GRATUITOS is exactly
    what every push executes. A paid classifier landing in that list turns each
    commit into an API bill — silently, since the run still succeeds.

    Checked behaviourally rather than by name: any call into app.llm.julgar
    fails the test, so a new paid classifier is caught even if nobody thought
    to add it to a list of forbidden names.
    """

    def test_nenhum_classificador_gratuito_chama_a_api(self, monkeypatch):
        def julgar_proibido(*args, **kwargs):
            raise AssertionError("classificador em GRATUITOS chamou a API paga")

        monkeypatch.setattr("app.llm.julgar", julgar_proibido)
        # Layer 2's model load is the other cost CI pays, in time rather than
        # money. Stubbed so this stays a wiring test, not an embedding benchmark.
        monkeypatch.setattr(
            "app.embeddings.embedar", lambda textos: [[0.1] * 384 for _ in textos]
        )

        perfil = {
            "descricao_curta": "software de gestao publica",
            "fornece": ["sistema web de gestao"],
        }
        caso = {"objeto": "sistema de gestao para prefeitura"}

        classificadores._vetor.cache_clear()
        classificadores._vetor_do_perfil.cache_clear()
        try:
            for nome in GRATUITOS:
                getattr(classificadores, nome)(caso, perfil)
        finally:
            # The stubbed vectors must not outlive the test in the shared cache.
            classificadores._vetor.cache_clear()
            classificadores._vetor_do_perfil.cache_clear()

    def test_classificadores_pagos_ficam_fora_da_lista(self):
        for nome in ("llm", "llm_sonnet", "cascata", "cascata_sonnet"):
            assert nome not in GRATUITOS


class TestGateDeRegressao:
    """The gate protects what was achieved, not what is still wanted.

    METAS (recall 0.85) is unmet by every classifier, so gating on it would
    keep the pipeline red forever and teach everyone to ignore it.
    """

    BASE: ClassVar[dict] = {
        "medido_em": "2026-07-27",
        "eval_set": {"casos": 34, "relevantes": 13},
        "classificadores": {
            "fixo": {"recall": 1.0, "precisao": 0.542, "deterministico": True},
            "com_llm": {"recall": 0.692, "precisao": 0.9, "deterministico": False},
        },
    }

    def test_repetir_o_baseline_nao_e_regressao(self):
        atual = {"recall": 1.0, "precisao": 0.542}
        assert comparar_com_baseline("fixo", atual, self.BASE) == []

    def test_melhorar_nao_e_regressao(self):
        atual = {"recall": 1.0, "precisao": 0.700}
        assert comparar_com_baseline("fixo", atual, self.BASE) == []

    def test_qualquer_queda_reprova_o_deterministico(self):
        # Same input, same output — a drop is changed behaviour, never noise.
        atual = {"recall": 1.0, "precisao": 0.541}
        quedas = comparar_com_baseline("fixo", atual, self.BASE)
        assert len(quedas) == 1
        assert "precisao" in quedas[0]

    def test_ruido_de_um_caso_e_tolerado_quando_ha_llm(self):
        # cascata measured 0.900-1.000 precision across runs on an unchanged
        # pipeline. Failing on that spread would be failing on sampling noise.
        atual = {"recall": 0.692 - 0.05, "precisao": 0.9}
        assert comparar_com_baseline("com_llm", atual, self.BASE) == []

    def test_queda_maior_que_um_caso_reprova_mesmo_com_llm(self):
        atual = {"recall": 0.692 - 0.20, "precisao": 0.9}
        quedas = comparar_com_baseline("com_llm", atual, self.BASE)
        assert len(quedas) == 1
        assert "recall" in quedas[0]

    def test_classificador_sem_baseline_reprova(self):
        # Never measured means nothing to protect and nothing to trust.
        # Recording a baseline has to be a deliberate act, not a default.
        quedas = comparar_com_baseline("novo", {"recall": 1.0, "precisao": 1.0}, self.BASE)
        assert quedas and "sem baseline" in quedas[0]

    def test_o_baseline_do_repo_descreve_este_pipeline(self):
        # The file is worth nothing if it drifts from the code. `vetorial` is
        # left out on purpose: it would load the encoder and turn a unit suite
        # into a model benchmark — CI covers it with the extra installed.
        casos, perfil = carregar()
        baseline = carregar_baseline()
        for nome in ("alerta_tudo", "keyword"):
            atual = avaliar(getattr(classificadores, nome), casos, perfil)
            assert comparar_com_baseline(nome, atual, baseline) == []
