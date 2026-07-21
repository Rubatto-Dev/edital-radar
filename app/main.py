from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    yield
    pool.close()


app = FastAPI(title="edital-radar", lifespan=lifespan)


@app.get("/health")
def health():
    with pool.connection() as conn:
        extensao = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        contratacoes = conn.execute("SELECT count(*) FROM contratacao").fetchone()[0]
        ultima = conn.execute(
            "SELECT status, iniciado_em FROM ingestao_execucao ORDER BY iniciado_em DESC LIMIT 1"
        ).fetchone()

    return {
        "status": "ok",
        "pgvector": extensao is not None,
        "contratacoes": contratacoes,
        # Surfaced here on purpose: an empty corpus caused by a failed
        # ingestion must not look the same as an empty corpus caused by
        # there being nothing to ingest.
        "ultima_ingestao": (
            {"status": ultima[0], "em": ultima[1].isoformat()} if ultima else None
        ),
    }
