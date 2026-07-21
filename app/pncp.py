"""Client for the PNCP consultation API.

The API is open and unauthenticated, and unreliable in specific ways that are
documented in docs/corpus-notes.md. This module's job is to make those failures
loud: it either returns data or raises. It never returns an empty page to mean
"the service is down", because that is the bug that makes a user miss a
deadline.
"""

import re
import time

import httpx

BASE = "https://pncp.gov.br/api/consulta/v1"

MODALIDADE_PREGAO_ELETRONICO = 6

# The API rejects tamanhoPagina below 10.
TAMANHO_PAGINA = 50

# Status codes worth retrying. 429 shows up around the 25th rapid request;
# 500/502/503 are the upstream database falling over, which happens.
RETENTAVEIS = {429, 500, 502, 503, 504}

# Objects arrive prefixed with the publishing platform: "[LICITANET] - ",
# "[Portal de Compras Públicas] - ". That is platform metadata leaking into
# free text — stripped before embedding, or every tender from one platform
# gains spurious similarity to every other.
PREFIXO_PLATAFORMA = re.compile(r"^\s*\[[^\]]{1,60}\]\s*[-–]\s*")


class PncpIndisponivel(Exception):
    """The API could not be reached after exhausting retries."""


def limpar_objeto(texto: str) -> str:
    if not texto:
        return ""
    return " ".join(PREFIXO_PLATAFORMA.sub("", texto).split())


def buscar_pagina(
    data_inicial: str,
    data_final: str,
    pagina: int,
    modalidade: int = MODALIDADE_PREGAO_ELETRONICO,
    tentativas: int = 6,
    dormir=time.sleep,
    cliente: httpx.Client | None = None,
) -> list[dict]:
    """One page of tenders. Raises PncpIndisponivel rather than returning [].

    Dates are YYYYMMDD. An empty list means the page genuinely has no records
    — the caller can trust that, which is the whole point.
    """
    params = {
        "dataInicial": data_inicial,
        "dataFinal": data_final,
        "codigoModalidadeContratacao": modalidade,
        "pagina": pagina,
        "tamanhoPagina": TAMANHO_PAGINA,
    }

    proprio = cliente is None
    cliente = cliente or httpx.Client(timeout=90)
    ultimo_erro = None
    try:
        for tentativa in range(tentativas):
            try:
                r = cliente.get(f"{BASE}/contratacoes/publicacao", params=params)
                # 204 is how the API says "no more pages".
                if r.status_code == 204:
                    return []
                if r.status_code in RETENTAVEIS:
                    ultimo_erro = f"HTTP {r.status_code}"
                    dormir(2**tentativa)
                    continue
                r.raise_for_status()
                return r.json().get("data") or []
            except (httpx.TimeoutException, httpx.TransportError) as e:
                ultimo_erro = f"{type(e).__name__}: {e}"
                dormir(2**tentativa)
    finally:
        if proprio:
            cliente.close()

    raise PncpIndisponivel(
        f"{tentativas} tentativas falharam para {data_inicial}-{data_final} "
        f"pagina {pagina}. Ultimo erro: {ultimo_erro}"
    )
