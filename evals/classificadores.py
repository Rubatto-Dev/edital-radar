"""Classifiers the runner can score, cheapest first.

Each takes a case and the profile, and returns one of `relevante`,
`nao_relevante`, `indeterminado`. The interface is deliberately identical for
the baselines and for the real cascade layers, so the numbers are comparable
and the delta between them is the whole story.
"""

import functools
import re

# Production parameter, not an eval-only concept — lives in app/embeddings.py
# so app/agente.py's daily driver uses the same funnel this module measures.
from app.embeddings import LIMIAR_FUNIL

# What an off-the-shelf keyword alert looks for. This is not a strawman: it is
# the incumbent approach in this market, and the reason the project exists is
# the claim that it fails here. Scoring it on labeled data turns that claim
# into a number.
TERMOS_KEYWORD = re.compile(
    r"\b(sistema|software|licen[çc]a|tecnologia da informa[çc][aã]o|ti|saas|"
    r"aplicativo|plataforma|inform[aá]tica|nuvem|cloud|web)\b",
    re.I,
)


def alerta_tudo(caso, perfil=None):
    """Floor: alert on everything. Recall 1.0 by construction.

    Worth scoring because it sets the precision a useful system must beat.
    Anything that does not beat this is costing the user attention for
    nothing.
    """
    return "relevante"


def keyword(caso, perfil=None):
    """The incumbent: substring match on IT vocabulary."""
    return "relevante" if TERMOS_KEYWORD.search(caso["objeto"]) else "nao_relevante"


@functools.lru_cache(maxsize=1)
def _vetor_do_perfil(texto):
    from app.embeddings import embedar

    return tuple(embedar([texto])[0])


@functools.lru_cache(maxsize=512)
def _vetor(texto):
    from app.embeddings import embedar

    return tuple(embedar([texto])[0])


def vetorial(caso, perfil, limiar=LIMIAR_FUNIL):
    """Layer 2: cosine similarity between the tender and the company profile.

    Deliberately not a good classifier — a funnel. At the threshold in use it
    keeps every relevant case in the evaluation set and still discards 69% of
    the live corpus, which is the trade it exists to make.
    """
    from app.embeddings import similaridade, texto_do_perfil

    p = _vetor_do_perfil(texto_do_perfil(perfil))
    o = _vetor(" ".join(caso["objeto"].split()))
    return "relevante" if similaridade(p, o) >= limiar else "nao_relevante"


@functools.lru_cache(maxsize=1)
def _orcamento():
    from app.custo import Orcamento

    return Orcamento()


def llm(caso, perfil):
    """Layer 3: LLM relevance judgment. Costs money — calls the real API.

    Goes through the shared daily Orcamento so this classifier can never
    bypass the spend cap that app/custo.py exists to enforce, and so every
    call lands in the cost/latency ledger regardless of caller.
    """
    from app.llm import julgar

    return julgar(caso["objeto"], perfil, orcamento=_orcamento()).classe


def llm_sonnet(caso, perfil):
    """Layer 3 with Sonnet 5 instead of Haiku 4.5 — the A/B the issue asked
    for if Haiku underperforms on recall: same prompt and schema, priced and
    logged the same way, so the delta is comparable and not a guess."""
    from app.llm import julgar

    return julgar(
        caso["objeto"], perfil, modelo="claude-sonnet-5", orcamento=_orcamento()
    ).classe


def cascata(caso, perfil, limiar=LIMIAR_FUNIL):
    """The full cascade: layer 2 funnels, layer 3 decides the survivors.

    Layer 1 (SQL) never drops a case — it only attaches caveats — so it has
    no scoring role here. This is the number that matters: the delta between
    `vetorial` alone and this is what layer 3 buys.
    """
    if vetorial(caso, perfil, limiar) == "nao_relevante":
        return "nao_relevante"
    return llm(caso, perfil)


def cascata_sonnet(caso, perfil, limiar=LIMIAR_FUNIL):
    if vetorial(caso, perfil, limiar) == "nao_relevante":
        return "nao_relevante"
    return llm_sonnet(caso, perfil)
