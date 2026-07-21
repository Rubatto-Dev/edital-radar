"""Incremental, idempotent ingestion of PNCP tenders into Postgres.

Run: python -m app.ingest --de 20260701 --ate 20260715
"""

import argparse
import json
import logging

import httpx

from psycopg.types.json import Jsonb

from app.db import normalizar_valor, pool
from app.pncp import (
    MODALIDADE_PREGAO_ELETRONICO,
    PncpIndisponivel,
    buscar_pagina,
    limpar_objeto,
)

log = logging.getLogger(__name__)

UPSERT = """
INSERT INTO contratacao (
    numero_controle_pncp, objeto_compra, objeto_limpo, valor_total_estimado,
    uf_sigla, modalidade_id, modalidade_nome, situacao_compra_nome,
    orgao_cnpj, orgao_razao_social, data_publicacao_pncp,
    data_encerramento_proposta, payload
) VALUES (
    %(numero)s, %(objeto)s, %(objeto_limpo)s, %(valor)s,
    %(uf)s, %(modalidade_id)s, %(modalidade_nome)s, %(situacao)s,
    %(cnpj)s, %(razao)s, %(publicacao)s,
    %(encerramento)s, %(payload)s
)
ON CONFLICT (numero_controle_pncp) DO UPDATE SET
    objeto_compra = EXCLUDED.objeto_compra,
    objeto_limpo = EXCLUDED.objeto_limpo,
    valor_total_estimado = EXCLUDED.valor_total_estimado,
    situacao_compra_nome = EXCLUDED.situacao_compra_nome,
    data_encerramento_proposta = EXCLUDED.data_encerramento_proposta,
    payload = EXCLUDED.payload
RETURNING (xmax = 0) AS inserido
"""


def mapear(registro: dict) -> dict:
    """API record -> row. Unmapped fields survive in payload."""
    unidade = registro.get("unidadeOrgao") or {}
    orgao = registro.get("orgaoEntidade") or {}
    objeto = registro.get("objetoCompra") or ""

    return {
        "numero": registro["numeroControlePNCP"],
        "objeto": objeto,
        "objeto_limpo": limpar_objeto(objeto),
        # 0.0 means confidential or unreported, never zero. See db.normalizar_valor.
        "valor": normalizar_valor(registro.get("valorTotalEstimado")),
        "uf": unidade.get("ufSigla"),
        "modalidade_id": registro.get("modalidadeId"),
        "modalidade_nome": registro.get("modalidadeNome"),
        "situacao": registro.get("situacaoCompraNome"),
        "cnpj": orgao.get("cnpj"),
        "razao": orgao.get("razaoSocial"),
        "publicacao": registro.get("dataPublicacaoPncp"),
        "encerramento": registro.get("dataEncerramentoProposta"),
        "payload": Jsonb(registro),
    }


def ingerir(
    data_inicial: str,
    data_final: str,
    modalidade: int = MODALIDADE_PREGAO_ELETRONICO,
    max_paginas: int = 200,
) -> dict:
    """Ingest one date window. Returns a summary of what happened.

    The execution row is written up front as 'falha' and only promoted to
    'ok' at the end. If the process is killed mid-run, what remains on disk
    says the run failed — which is true. Optimistic-then-correct would leave
    a crashed run looking successful, and a successful-looking run with no
    tenders is exactly the lie that costs a user a deadline.
    """
    with pool.connection() as conn:
        execucao_id = conn.execute(
            """
            INSERT INTO ingestao_execucao (data_inicial, data_final, modalidade_id, status)
            VALUES (%s, %s, %s, 'falha') RETURNING id
            """,
            (data_inicial, data_final, modalidade),
        ).fetchone()[0]

    paginas = 0
    novos = 0
    vistos = 0
    erro = None

    try:
        for pagina in range(1, max_paginas + 1):
            registros = buscar_pagina(data_inicial, data_final, pagina, modalidade)
            if not registros:
                break

            with pool.connection() as conn:
                for registro in registros:
                    linha = conn.execute(UPSERT, mapear(registro)).fetchone()
                    vistos += 1
                    if linha[0]:
                        novos += 1

            paginas = pagina
            log.info("pagina %s: %s registros, %s novos", pagina, len(registros), novos)
    except PncpIndisponivel as e:
        erro = str(e)
    except httpx.HTTPStatusError as e:
        # Not unavailability — a 4xx the client should not retry, which means
        # our request is wrong. Recorded rather than raised: an ingestion that
        # dies without leaving a reason on disk is the failure this table
        # exists to prevent, and the cause matters less than the record.
        erro = f"requisicao rejeitada: {e}"

    # 'parcial' is a real outcome, not a rounding of 'ok': some pages landed
    # and the window is incomplete. Re-running is safe — the upsert is
    # idempotent — but the caller has to know it should.
    if erro and paginas:
        status = "parcial"
    elif erro:
        status = "falha"
    else:
        status = "ok"

    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE ingestao_execucao
               SET status = %s, paginas_lidas = %s, registros_novos = %s,
                   erro = %s, terminado_em = now()
             WHERE id = %s
            """,
            (status, paginas, novos, erro, execucao_id),
        )

    return {
        "execucao_id": execucao_id,
        "status": status,
        "paginas": paginas,
        "registros_vistos": vistos,
        "registros_novos": novos,
        "erro": erro,
    }


def main():
    p = argparse.ArgumentParser(description="Ingere contratações do PNCP")
    p.add_argument("--de", required=True, help="data inicial YYYYMMDD")
    p.add_argument("--ate", required=True, help="data final YYYYMMDD")
    p.add_argument("--modalidade", type=int, default=MODALIDADE_PREGAO_ELETRONICO)
    p.add_argument("--max-paginas", type=int, default=200)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pool.open()
    try:
        resultado = ingerir(args.de, args.ate, args.modalidade, args.max_paginas)
    finally:
        pool.close()

    print(json.dumps(resultado, indent=2, ensure_ascii=False))
    raise SystemExit(0 if resultado["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
