"""The `llm` classifier must never bypass the spend cap.

No network here — app.llm.julgar is monkeypatched. The real cap enforcement
is tested against app.custo.Orcamento directly in tests/test_custo.py; this
file only checks the wiring, which is the part that broke once already (a
real eval run spent real money with no cap applied because `orcamento` was
never passed through).
"""

from evals import classificadores


def test_llm_passa_o_orcamento_compartilhado_pro_julgar(monkeypatch):
    chamadas = []

    def julgar_falso(objeto, perfil, *, orcamento=None, client=None):
        chamadas.append(orcamento)

        class Resultado:
            classe = "relevante"

        return Resultado()

    monkeypatch.setattr("app.llm.julgar", julgar_falso)

    classificadores.llm({"objeto": "qualquer coisa"}, perfil={})

    assert len(chamadas) == 1
    assert chamadas[0] is not None  # orcamento foi passado, não None


def test_llm_reusa_o_mesmo_orcamento_entre_chamadas():
    # A single shared instance is what makes the ledger and the cap
    # meaningful across an eval run — a fresh Orcamento per call would
    # never see the accumulated spend.
    assert classificadores._orcamento() is classificadores._orcamento()
