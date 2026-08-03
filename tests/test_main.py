"""The HTTP edge — the only surface anything outside this process touches.

Everything below it was already covered; these four routes were not, so a
change to the API contract could ship green. No database and no model here:
`TestClient(app)` outside a `with` block does not fire the lifespan, so the
pool is never opened, and each test substitutes what its route reaches for.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.main as main


class _Resultado:
    def __init__(self, linha):
        self._linha = linha

    def fetchone(self):
        return self._linha


class _Conn:
    """Answers each execute() with the next queued row, in order."""

    def __init__(self, respostas):
        self._respostas = list(respostas)

    def execute(self, *_args, **_kwargs):
        return _Resultado(self._respostas.pop(0))


class _Pool:
    def __init__(self, *respostas):
        self._respostas = respostas

    @contextlib.contextmanager
    def connection(self):
        yield _Conn(self._respostas)


class _PoolMorto:
    """A database that is down, which is a state the panel has to survive."""

    @contextlib.contextmanager
    def connection(self):
        raise RuntimeError("banco inalcancavel")
        yield  # pragma: no cover


@pytest.fixture
def cliente():
    return TestClient(main.app)


class TestQuery:
    def test_pergunta_vazia_e_recusada(self, cliente, monkeypatch):
        # The guard exists so an empty query never reaches the encoder — that
        # would embed the empty string and rank the whole corpus by noise.
        monkeypatch.setattr(main, "pool", _Pool())
        assert cliente.get("/query", params={"q": ""}).status_code == 422

    def test_pergunta_so_de_espacos_e_recusada(self, cliente, monkeypatch):
        monkeypatch.setattr(main, "pool", _Pool())
        assert cliente.get("/query", params={"q": "   "}).status_code == 422

    def test_devolve_a_pergunta_e_os_resultados(self, cliente, monkeypatch):
        monkeypatch.setattr(main, "pool", _Pool())
        monkeypatch.setattr(main, "embedar", lambda textos: [[0.1] * 384])
        monkeypatch.setattr(
            main,
            "buscar",
            lambda conn, vetor, agora, limite: [
                {"trecho": "sistema de gestao", "link": "https://pncp.gov.br/x"}
            ],
        )

        r = cliente.get("/query", params={"q": "gestao publica"})

        assert r.status_code == 200
        corpo = r.json()
        assert corpo["pergunta"] == "gestao publica"
        assert corpo["resultados"][0]["link"].startswith("https://pncp.gov.br")

    def test_limite_chega_na_busca(self, cliente, monkeypatch):
        # Passed through rather than silently defaulted — the caller controls
        # how much it pays for in rendering.
        recebidos = {}
        monkeypatch.setattr(main, "pool", _Pool())
        monkeypatch.setattr(main, "embedar", lambda textos: [[0.1] * 384])

        def buscar_falso(conn, vetor, agora, limite):
            recebidos["limite"] = limite
            return []

        monkeypatch.setattr(main, "buscar", buscar_falso)
        cliente.get("/query", params={"q": "gestao", "limite": 3})

        assert recebidos["limite"] == 3


class TestHealth:
    def test_expoe_a_ultima_ingestao(self, cliente, monkeypatch):
        # The reason this field exists: an empty corpus caused by a failed
        # ingestion must not look like an empty corpus caused by there being
        # nothing to ingest.
        import datetime

        quando = datetime.datetime(2026, 7, 28, 10, 0, tzinfo=datetime.UTC)
        monkeypatch.setattr(main, "pool", _Pool((1,), (1200,), ("parcial", quando)))

        corpo = cliente.get("/health").json()

        assert corpo["status"] == "ok"
        assert corpo["pgvector"] is True
        assert corpo["contratacoes"] == 1200
        assert corpo["ultima_ingestao"]["status"] == "parcial"
        assert corpo["ultima_ingestao"]["em"].startswith("2026-07-28")

    def test_sem_ingestao_registrada_devolve_none(self, cliente, monkeypatch):
        monkeypatch.setattr(main, "pool", _Pool((1,), (0,), None))
        assert cliente.get("/health").json()["ultima_ingestao"] is None

    def test_pgvector_ausente_e_reportado(self, cliente, monkeypatch):
        monkeypatch.setattr(main, "pool", _Pool(None, (0,), None))
        assert cliente.get("/health").json()["pgvector"] is False


class TestMetrics:
    def test_responde_com_custo_e_qualidade(self, cliente):
        corpo = cliente.get("/metrics").json()
        assert set(corpo) == {"custo", "qualidade"}
        assert "chamadas" in corpo["custo"]
        assert corpo["qualidade"]["disponivel"] is True

    def test_responde_com_o_banco_fora_do_ar(self, cliente, monkeypatch):
        # The design claim, made testable: both sources are files, so the panel
        # still answers when Postgres is down — which is when someone is
        # actually looking at a dashboard.
        monkeypatch.setattr(main, "pool", _PoolMorto())
        assert cliente.get("/metrics").status_code == 200


class TestPainel:
    def test_serve_html(self, cliente):
        r = cliente.get("/painel")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")

    def test_a_pagina_busca_o_endpoint_de_metricas(self, cliente):
        # If the panel stops fetching /metrics it renders an empty shell and
        # nothing else fails — so the wiring is asserted here.
        assert 'fetch("metrics")' in cliente.get("/painel").text
