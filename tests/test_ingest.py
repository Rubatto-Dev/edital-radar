"""Tests for the ingestion outcome contract.

The rule these protect: an ingestion that fails must leave a record saying so,
with a reason. It must never raise past the caller, and it must never be
mistaken for a run that legitimately found nothing.
"""

import httpx
import pytest

from app import ingest
from app.pncp import PncpIndisponivel


class ConexaoFalsa:
    def __init__(self, registro):
        self.registro = registro

    def execute(self, sql, params=None):
        if "INSERT INTO ingestao_execucao" in sql:
            self.registro["id"] = 1
            self.registro["status"] = "falha"  # pessimistic default
            return _Linha((1,))
        if "UPDATE ingestao_execucao" in sql:
            status, paginas, novos, erro, _id = params
            self.registro.update(
                status=status, paginas=paginas, novos=novos, erro=erro
            )
            return _Linha(None)
        return _Linha((True,))  # upsert: inserted

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Linha:
    def __init__(self, valor):
        self.valor = valor

    def fetchone(self):
        return self.valor


@pytest.fixture
def registro(monkeypatch):
    reg = {}
    monkeypatch.setattr(
        ingest.pool, "connection", lambda: ConexaoFalsa(reg)
    )
    return reg


def _paginas(*resultados):
    it = iter(resultados)

    def falso(*a, **k):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    return falso


def test_sucesso_registra_ok(registro, monkeypatch):
    monkeypatch.setattr(ingest, "buscar_pagina", _paginas([{"numeroControlePNCP": "x", "objetoCompra": "sistema"}], []))
    r = ingest.ingerir("20260701", "20260703")
    assert r["status"] == "ok"
    assert r["erro"] is None
    assert registro["status"] == "ok"


def test_indisponibilidade_na_primeira_pagina_registra_falha_com_motivo(registro, monkeypatch):
    monkeypatch.setattr(ingest, "buscar_pagina", _paginas(PncpIndisponivel("HTTP 503")))
    r = ingest.ingerir("20260701", "20260703")
    assert r["status"] == "falha"
    assert "503" in r["erro"]
    # The reason must reach the database, not just the return value.
    assert "503" in registro["erro"]


def test_indisponibilidade_no_meio_registra_parcial(registro, monkeypatch):
    monkeypatch.setattr(
        ingest,
        "buscar_pagina",
        _paginas([{"numeroControlePNCP": "x", "objetoCompra": "sistema"}], PncpIndisponivel("HTTP 500")),
    )
    r = ingest.ingerir("20260701", "20260703")
    # Some pages landed and the window is incomplete. Calling this 'ok' would
    # hide a gap in the corpus; calling it 'falha' would discard real work.
    assert r["status"] == "parcial"
    assert r["paginas"] == 1


def test_erro_nao_retentavel_e_registrado_em_vez_de_propagado(registro, monkeypatch):
    # Regression: a 403 used to escape as HTTPStatusError, so the UPDATE never
    # ran and the row kept status 'falha' with erro NULL — a failure with no
    # reason on disk.
    resposta = httpx.Response(403, request=httpx.Request("GET", "https://pncp.gov.br/x"))
    monkeypatch.setattr(
        ingest,
        "buscar_pagina",
        _paginas(httpx.HTTPStatusError("403", request=resposta.request, response=resposta)),
    )
    r = ingest.ingerir("20260701", "20260703")
    assert r["status"] == "falha"
    assert "403" in r["erro"]
    assert registro["erro"] and "403" in registro["erro"]


def test_janela_vazia_e_sucesso_nao_falha(registro, monkeypatch):
    # Zero tenders is a legitimate answer and must be distinguishable from
    # an outage. This is the other half of the same contract.
    monkeypatch.setattr(ingest, "buscar_pagina", _paginas([]))
    r = ingest.ingerir("20260701", "20260703")
    assert r["status"] == "ok"
    assert r["registros_novos"] == 0
    assert r["erro"] is None
