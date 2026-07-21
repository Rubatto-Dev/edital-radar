"""Cascade layer 2: local embeddings. Free.

Keyword matching compares letters. `sistema` appears both in "Sistema de
Registro de Preços" — a statutory procurement modality attached to purchases
of medicine and concrete — and in "sistema web de gestão". No regex separates
those, because the difference is not in the characters.

An embedding model maps a whole phrase to a vector positioned so that texts
meaning similar things land near each other. The word `sistema` then decides
nothing; the rest of the phrase does.

Runs locally: zero marginal cost is a budget requirement here, not an
optimisation. Using a pretrained model is not training one — this stays on the
AI-engineering side of the line, deliberately.
"""

import functools

# 384 dimensions, multilingual, good Portuguese. Matches the vector(384)
# column in sql/001_schema.sql — changing the model means changing that
# column and re-embedding everything.
MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@functools.lru_cache(maxsize=1)
def _modelo():
    # Imported lazily so that anything not doing vector work — the API
    # healthcheck, the ingester, the keyword baseline — never pays the load.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODELO)


def embedar(textos: list[str]) -> list[list[float]]:
    vetores = _modelo().encode(
        textos, normalize_embeddings=True, show_progress_bar=False
    )
    return [v.tolist() for v in vetores]


def texto_do_perfil(perfil: dict) -> str:
    """The profile as one string to embed.

    Only what the company *does* supply. `nao_fornece` is deliberately left
    out: adding it would pull the profile vector toward hardware and staffing
    vocabulary, which is the opposite of what we want it to sit near.
    Exclusions are handled by the LLM in layer 3, which can reason about
    negation — something a single averaged vector cannot do.
    """
    partes = [perfil["descricao_curta"], *perfil["fornece"]]
    return " ".join(" ".join(p.split()) for p in partes)


def similaridade(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Vectors are already normalised, so it is a dot product."""
    return sum(x * y for x, y in zip(a, b))
