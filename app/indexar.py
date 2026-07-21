"""Embeds tenders that do not have a vector yet, into pgvector.

Run: python -m app.indexar [--lote 256]

Idempotent by construction: it only touches rows where objeto_embedding is
NULL, so re-running after a partial ingestion costs nothing.
"""

import argparse
import logging

from app.db import pool
from app.embeddings import MODELO, embedar

log = logging.getLogger(__name__)

PENDENTES = """
SELECT numero_controle_pncp, objeto_limpo
  FROM contratacao
 WHERE objeto_embedding IS NULL
   AND objeto_limpo IS NOT NULL
   AND objeto_limpo <> ''
 LIMIT %s
"""


def indexar(lote: int = 256) -> int:
    total = 0
    while True:
        with pool.connection() as conn:
            linhas = conn.execute(PENDENTES, (lote,)).fetchall()
            if not linhas:
                break

            vetores = embedar([l[1] for l in linhas])
            for (numero, _), vetor in zip(linhas, vetores):
                conn.execute(
                    "UPDATE contratacao SET objeto_embedding = %s::vector "
                    "WHERE numero_controle_pncp = %s",
                    (str(vetor), numero),
                )
            total += len(linhas)
            log.info("indexados %s", total)
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lote", type=int, default=256)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pool.open()
    try:
        n = indexar(args.lote)
    finally:
        pool.close()
    print(f"{n} contratacoes indexadas com {MODELO}")


if __name__ == "__main__":
    main()
