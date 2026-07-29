"""Aggregations behind /metrics. No HTTP here, so it stays testable.

Two sources, both of which already existed for other reasons:

  - `data/custo-ledger.jsonl` — one line per paid call, written by
    app.custo.Orcamento since Phase 1.6. It is **gitignored**, so a fresh
    clone, the container and CI all have no ledger at all. That is the normal
    case, not an error: the panel reports "no data yet" instead of failing.
  - `evals/baseline.json` — the measured numbers the regression gate protects
    (Phase 3.2). Shown next to METAS so the panel says both what was achieved
    and what is still wanted, which are different things and easy to conflate.

Deliberately not here: hallucination rate. Nothing measures it yet — that is
RUB-101 — and a panel showing a metric nobody computed is worse than a panel
missing one.
"""

import json
import math
import statistics

from app.custo import CAMINHO_PADRAO, TETO_PADRAO_USD
from evals.run import BASELINE, METAS


def ler_ledger(caminho=CAMINHO_PADRAO) -> list[dict]:
    """Every recorded call, oldest first. Missing file means no calls yet."""
    if not caminho.exists():
        return []
    registros = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha:
            registros.append(json.loads(linha))
    return registros


def percentil(valores: list[float], p: float) -> float | None:
    """Nearest-rank percentile.

    Chosen over interpolation because the samples here are small — tens of
    calls, not thousands — and an interpolated p95 of 40 measurements invents
    a latency that was never observed. Nearest-rank always returns a real
    measurement, which is the honest answer at this sample size.
    """
    if not valores:
        return None
    ordenados = sorted(valores)
    # ceil, not round: Python rounds halves to even, so round() would return
    # the larger of two samples for p50 on an even-sized list — off by one in
    # the direction that flatters the number.
    indice = math.ceil(p / 100 * len(ordenados)) - 1
    return ordenados[max(0, min(len(ordenados) - 1, indice))]


def resumir_ledger(registros: list[dict]) -> dict:
    custos = [r["custo_usd"] for r in registros]
    latencias = [r["latencia_s"] for r in registros]

    por_dia: dict[str, dict] = {}
    for r in registros:
        dia = por_dia.setdefault(r["data"], {"chamadas": 0, "custo_usd": 0.0})
        dia["chamadas"] += 1
        dia["custo_usd"] += r["custo_usd"]

    ultimo_dia = max(por_dia) if por_dia else None

    return {
        "chamadas": len(registros),
        "custo_total_usd": round(sum(custos), 6),
        # The number to quote: what one judgment costs. Everything else in the
        # panel is context for this one.
        "custo_medio_usd": round(statistics.fmean(custos), 6) if custos else None,
        "latencia_p50_s": round(percentil(latencias, 50), 3) if latencias else None,
        "latencia_p95_s": round(percentil(latencias, 95), 3) if latencias else None,
        "teto_diario_usd": TETO_PADRAO_USD,
        "gasto_no_ultimo_dia_usd": (
            round(por_dia[ultimo_dia]["custo_usd"], 6) if ultimo_dia else None
        ),
        "por_dia": [
            {"data": d, "chamadas": v["chamadas"], "custo_usd": round(v["custo_usd"], 6)}
            for d, v in sorted(por_dia.items())
        ],
    }


def resumir_baseline() -> dict:
    if not BASELINE.exists():
        return {"disponivel": False}

    dados = json.loads(BASELINE.read_text(encoding="utf-8"))
    classificadores = [
        {
            "nome": nome,
            "recall": v["recall"],
            "precisao": v["precisao"],
            "deterministico": v["deterministico"],
            "modelo": v.get("modelo"),
            # Target and achievement kept apart on purpose: no classifier
            # meets METAS today, and a panel that blurs the two would be
            # reporting an aspiration as a result.
            "atinge_metas": v["recall"] >= METAS["recall"]
            and v["precisao"] >= METAS["precisao"],
        }
        for nome, v in dados["classificadores"].items()
    ]
    return {
        "disponivel": True,
        "medido_em": dados["medido_em"],
        "eval_set": dados["eval_set"],
        "metas": METAS,
        "classificadores": classificadores,
    }


def resumo() -> dict:
    registros = ler_ledger()
    return {
        "custo": resumir_ledger(registros),
        "qualidade": resumir_baseline(),
    }
