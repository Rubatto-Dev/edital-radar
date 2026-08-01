"""Tracing has to be invisible when it is off, and harmless when it breaks.

CI runs with no Langfuse keys and must stay green, so "off" is the normal
case here, not a corner. And an exporter that can raise into the caller would
mean observability taking down the judgment it was added to observe — the
failure mode worth testing on purpose.
"""

import pytest

import app.tracing as tracing


@pytest.fixture(autouse=True)
def _cache_limpo():
    # ativo() is lru_cached so the env is read once per process; tests that
    # change the environment have to invalidate it on both sides.
    tracing.ativo.cache_clear()
    yield
    tracing.ativo.cache_clear()


@pytest.fixture
def desligado(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracing.ativo.cache_clear()


@pytest.fixture
def com_chaves(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-teste")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-teste")
    tracing.ativo.cache_clear()


class TestDesligado:
    def test_sem_chaves_fica_desligado(self, desligado):
        assert tracing.ativo() is False

    def test_uma_chave_so_nao_liga(self, desligado, monkeypatch):
        # Half-configured is off, not half-on: a public key with no secret
        # would fail at export time, far from where it was misconfigured.
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-teste")
        tracing.ativo.cache_clear()
        assert tracing.ativo() is False

    def test_update_e_aceito_e_nao_faz_nada(self, desligado):
        # Callers write obs.update(...) unconditionally; that must be safe.
        with tracing.observar("julgar", as_type="generation") as obs:
            obs.update(output={"classe": "relevante"}, cost_details={"total": 0.004})

    def test_descarregar_nao_quebra(self, desligado):
        tracing.descarregar()


class TestNuncaFatal:
    def test_falha_do_sdk_vira_silencio(self, com_chaves, monkeypatch):
        import langfuse

        def explode():
            raise RuntimeError("sem rede")

        monkeypatch.setattr(langfuse, "get_client", explode)

        # No exception: the judgment goes on, untraced.
        with tracing.observar("julgar", as_type="generation") as obs:
            obs.update(output="segue o jogo")

    def test_descarregar_engole_falha_do_sdk(self, com_chaves, monkeypatch):
        import langfuse

        def explode():
            raise RuntimeError("sem rede")

        monkeypatch.setattr(langfuse, "get_client", explode)
        tracing.descarregar()


class TestNaoInterfereNoCaller:
    def test_erro_do_corpo_propaga_com_tracing_desligado(self, desligado):
        # Tracing must never swallow the caller's own failure.
        with pytest.raises(ValueError, match="boom"), tracing.observar("julgar") as obs:
            obs.update(output="nada")
            raise ValueError("boom")

    def test_erro_do_corpo_propaga_com_sdk_quebrado(self, com_chaves, monkeypatch):
        import langfuse

        monkeypatch.setattr(
            langfuse, "get_client", lambda: (_ for _ in ()).throw(RuntimeError("x"))
        )
        with pytest.raises(ValueError, match="boom"), tracing.observar("julgar"):
            raise ValueError("boom")
