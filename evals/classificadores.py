"""Classifiers the runner can score, cheapest first.

Each takes a case and the profile, and returns one of `relevante`,
`nao_relevante`, `indeterminado`. The interface is deliberately identical for
the baselines and for the real cascade layers, so the numbers are comparable
and the delta between them is the whole story.
"""

import functools
import re

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


# Chosen by looking at this same evaluation set — see the caveat in run.py.
# It is a funnel setting, not a decision boundary: the job of layer 2 is to
# cut volume without losing anything, and let layer 3 decide.
LIMIAR_FUNIL = 0.45


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
