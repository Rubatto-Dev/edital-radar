"""Answers a free-text question against the indexed corpus. Retrieval only —
no LLM, no cost. The endpoint's whole contract is verifiability: every
result carries the tender's own object text as the citation and a link back
to PNCP, so the user checks it instead of trusting it. Without a citation
the answer does not count as an answer.
"""

import re

# numeroControlePNCP has the form {cnpj}-1-{sequencial}/{ano} (see
# docs/corpus-notes.md). PNCP's public viewer at pncp.gov.br/app/editais
# uses the same three fields with the sequential's leading zeros stripped —
# confirmed against real live PNCP URLs (e.g.
# pncp.gov.br/app/editais/07753868000101/2026/3 for control number
# 07753868000101-1-000003/2026), not guessed from the API docs alone.
NUMERO_CONTROLE_RE = re.compile(r"^(?P<cnpj>\d{14})-1-(?P<sequencial>\d+)/(?P<ano>\d{4})$")

SQL_BUSCA = """
SELECT numero_controle_pncp, objeto_compra, orgao_razao_social, uf_sigla,
       valor_total_estimado, data_encerramento_proposta,
       1 - (objeto_embedding <=> %(vetor)s::vector) AS similaridade
  FROM contratacao
 WHERE objeto_embedding IS NOT NULL
   AND (data_encerramento_proposta IS NULL OR data_encerramento_proposta > %(agora)s)
 ORDER BY objeto_embedding <=> %(vetor)s::vector
 LIMIT %(limite)s
"""


def link_pncp(numero_controle_pncp: str) -> str | None:
    m = NUMERO_CONTROLE_RE.match(numero_controle_pncp)
    if not m:
        return None
    return f"https://pncp.gov.br/app/editais/{m['cnpj']}/{m['ano']}/{int(m['sequencial'])}"


def buscar(conn, vetor, agora, limite: int = 5) -> list[dict]:
    linhas = conn.execute(
        SQL_BUSCA, {"vetor": str(vetor), "agora": agora, "limite": limite}
    ).fetchall()
    return [
        {
            "numero_controle_pncp": l[0],
            # The citation: the tender's own text, verbatim. Not a summary —
            # a summary can be wrong in a way the user can't catch without
            # already knowing the answer.
            "trecho": l[1],
            "link": link_pncp(l[0]),
            "orgao": l[2],
            "uf": l[3],
            "valor_total_estimado": l[4],
            "prazo": l[5].isoformat() if l[5] else None,
            "similaridade": round(l[6], 3),
        }
        for l in linhas
    ]
