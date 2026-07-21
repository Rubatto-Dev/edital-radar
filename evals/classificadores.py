"""Classifiers the runner can score, cheapest first.

Each takes a case and the profile, and returns one of `relevante`,
`nao_relevante`, `indeterminado`. The interface is deliberately identical for
the baselines and for the real cascade layers, so the numbers are comparable
and the delta between them is the whole story.
"""

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
