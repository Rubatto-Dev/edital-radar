"""Cascade layer 3: LLM relevance judgment. The only step that costs money.

Runs only on layer-2 finalists (see app/embeddings.py). Judges relevance
against the company profile, returns a justification, and can abstain — the
only place `indeterminado` means "the metadata is not decidable" rather than
a discard. Structured output guarantees three classes; there is no string to
misparse.
"""

import functools
import json
import time
from dataclasses import dataclass

from app.perfil import carregar

MODELO = "claude-haiku-4-5"

# USD per 1M tokens, input/output. Cache read is ~0.1x input, cache write
# (default 5-minute ephemeral TTL) ~1.25x input — same ratio on every model.
# Sonnet 5 price is the introductory rate through 2026-08-31.
PRECOS = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
}

CLASSES = ("relevante", "nao_relevante", "indeterminado")

SCHEMA = {
    "type": "object",
    "properties": {
        "classe": {"type": "string", "enum": list(CLASSES)},
        "justificativa": {"type": "string"},
        "lote_misto": {"type": "boolean"},
    },
    "required": ["classe", "justificativa", "lote_misto"],
    "additionalProperties": False,
}


@dataclass
class Julgamento:
    classe: str
    justificativa: str
    lote_misto: bool
    custo_usd: float
    latencia_s: float


@functools.lru_cache(maxsize=1)
def _client():
    # Imported lazily so tests never need the SDK installed or a key set.
    import anthropic

    return anthropic.Anthropic()


def system_prompt(perfil: dict) -> list[dict]:
    """The profile as a cacheable system prompt.

    Unlike layer 2's texto_do_perfil, `nao_fornece` belongs here: the LLM can
    reason about negation ("does NOT sell X"), which a single averaged vector
    cannot do (see app/embeddings.py:texto_do_perfil).

    The text is built deterministically from the profile so the prefix is
    byte-stable across calls — required for the cache_control breakpoint to
    ever hit.
    """
    fornece = "\n".join(f"- {i}" for i in perfil["fornece"])
    nao_fornece = "\n".join(f"- {i}" for i in perfil["nao_fornece"])
    texto = (
        "Você julga se uma licitação pública é relevante para esta empresa.\n\n"
        f"Descrição: {' '.join(perfil['descricao_curta'].split())}\n\n"
        f"Fornece:\n{fornece}\n\n"
        f"NÃO fornece:\n{nao_fornece}\n\n"
        "Classifique o objeto da licitação em relevante, nao_relevante ou "
        "indeterminado. Use indeterminado quando o texto não tiver informação "
        "suficiente para decidir — não chute. Marque lote_misto quando o "
        "objeto misturar escopo dentro e fora do perfil."
    )
    return [{"type": "text", "text": texto, "cache_control": {"type": "ephemeral"}}]


def custo(usage, modelo: str = MODELO) -> float:
    preco = PRECOS[modelo]
    preco_input = preco["input"] / 1_000_000
    preco_output = preco["output"] / 1_000_000
    return (
        usage.input_tokens * preco_input
        + usage.output_tokens * preco_output
        + getattr(usage, "cache_read_input_tokens", 0) * preco_input * 0.1
        + getattr(usage, "cache_creation_input_tokens", 0) * preco_input * 1.25
    )


def julgar(
    objeto: str,
    perfil: dict | None = None,
    *,
    modelo: str = MODELO,
    client=None,
    orcamento=None,
) -> Julgamento:
    perfil = perfil or carregar()
    client = client or _client()

    if orcamento is not None:
        orcamento.verificar()

    inicio = time.monotonic()
    resposta = client.messages.create(
        model=modelo,
        max_tokens=512,
        system=system_prompt(perfil),
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"Objeto da licitação: {objeto.strip()}"}],
    )
    latencia = time.monotonic() - inicio

    texto = next(b.text for b in resposta.content if b.type == "text")
    dados = json.loads(texto)
    custo_usd = custo(resposta.usage, modelo)

    julgamento = Julgamento(
        classe=dados["classe"],
        justificativa=dados["justificativa"],
        lote_misto=dados["lote_misto"],
        custo_usd=custo_usd,
        latencia_s=latencia,
    )

    if orcamento is not None:
        orcamento.registrar(julgamento.custo_usd, julgamento.latencia_s)

    return julgamento
