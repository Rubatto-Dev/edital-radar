"""Langfuse tracing for the paid path. Off unless configured, never fatal.

Langfuse Cloud rather than self-hosted, and the reason is arithmetic: the
official minimum for self-hosting sums to ~25.5 GiB (web + worker 8, ClickHouse
8 — required, with no supported alternative OLAP store — Postgres 4, blob
storage 4, Redis 1.5), against a development machine with 15 GiB total. The
data is public procurement text, so nothing here is confidential.

Two properties everything else depends on:

  - **Off by default.** No keys, no tracing, no error. CI has no keys and must
    stay green; the test suite must not need a Langfuse account to run.
  - **Never fatal.** Observability that can take production down is worse than
    no observability. A broken exporter degrades to silence, not a stack trace
    in the middle of a judgment the agent was paid to make.
"""

import contextlib
import functools
import logging
import os

log = logging.getLogger(__name__)


class _Silencioso:
    """Stands in for an observation while tracing is off.

    Callers write `obs.update(...)` unconditionally; this makes that a no-op
    instead of forcing an `if ativo()` around every instrumented block.
    """

    def update(self, **_):
        pass


@functools.lru_cache(maxsize=1)
def ativo() -> bool:
    """Both keys present. LANGFUSE_BASE_URL is optional — the SDK defaults to
    the EU cloud region — so it is deliberately not part of the check."""
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def observar(nome: str, **atributos):
    """A context manager for one observation, or a silent stand-in.

    Returns rather than yields: a plain function handing back a context
    manager keeps exceptions raised inside the `with` body propagating on
    their own path, instead of passing through a generator that has to
    re-raise them correctly.
    """
    if not ativo():
        return contextlib.nullcontext(_Silencioso())
    try:
        from langfuse import get_client

        return get_client().start_as_current_observation(name=nome, **atributos)
    except Exception as e:
        log.warning("tracing indisponivel, seguindo sem ele: %s", e)
        return contextlib.nullcontext(_Silencioso())


def ferramenta(nome: str, **entrada):
    """One tool call inside the agent loop, as a child observation.

    `tool` is a real observation type in the SDK, not a label — checked
    against the installed package rather than assumed, and it is what makes
    the loop render as tool calls in the UI instead of anonymous spans.
    """
    return observar(nome, as_type="tool", input=entrada or None)


def descarregar() -> None:
    """Flush pending traces.

    The SDK batches in the background, so a short-lived CLI run — which is
    what the daily driver is — would exit before anything was sent.
    """
    if not ativo():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as e:
        log.warning("falha ao enviar traces pendentes: %s", e)
