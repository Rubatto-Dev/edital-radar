"""Cascade layer 1: structured filtering over metadata. Free.

The original design had this layer cut on state and value as well. Measuring
against the evaluation set killed that: 8 of 13 relevant tenders sit outside
the served states and 2 below the viability floor, so hard-filtering both
capped recall at 0.385 against a 0.85 target — a loss no later layer can undo,
incurred at the cheapest layer.

So the split is by *kind of fact*:

  hard   — the tender is objectively unusable (the deadline has passed)
  soft   — the tender is usable but inconvenient (wrong region, too small)

Soft facts become caveats attached to the alert. The user decides whether a
tender in a neighbouring state is worth bidding on; the filter does not get to
decide that silently. This is the same reasoning as `lote_misto` in the
evaluation set.
"""

from app.perfil import restricoes

# The only genuinely disqualifying fact. A tender whose proposal window has
# closed cannot be acted on, no matter how well it matches.
SQL_CANDIDATOS = """
SELECT numero_controle_pncp, objeto_compra, objeto_limpo, valor_total_estimado,
       uf_sigla, data_encerramento_proposta, orgao_razao_social
  FROM contratacao
 WHERE data_encerramento_proposta IS NULL
    OR data_encerramento_proposta > %(agora)s
 ORDER BY data_encerramento_proposta NULLS LAST
"""

FORA_REGIAO = "fora_da_regiao_atendida"
ABAIXO_DO_PISO = "abaixo_do_piso_de_viabilidade"
ACIMA_DA_CAPACIDADE = "acima_da_capacidade"
VALOR_DESCONHECIDO = "valor_desconhecido"


def ressalvas(uf: str | None, valor, perfil: dict | None = None) -> list[str]:
    """Caveats to attach to an alert. Never a reason to drop it."""
    r = restricoes(perfil)
    marcas = []

    if uf and uf not in r["ufs_atendidas"]:
        marcas.append(FORA_REGIAO)

    if valor is None:
        # NULL here means the PNCP sent 0.0 — confidential or unreported.
        # Treating it as "too small" is the silent discard this whole rule
        # exists to prevent (see pos-03).
        marcas.append(VALOR_DESCONHECIDO)
    elif valor < r["valor_minimo_viavel"]:
        marcas.append(ABAIXO_DO_PISO)
    elif valor > r["valor_maximo_viavel"]:
        marcas.append(ACIMA_DA_CAPACIDADE)

    return marcas


def candidatos(conn, agora, perfil: dict | None = None) -> list[dict]:
    linhas = conn.execute(SQL_CANDIDATOS, {"agora": agora}).fetchall()
    return [
        {
            "numero_controle_pncp": l[0],
            "objeto_compra": l[1],
            "objeto_limpo": l[2],
            "valor_total_estimado": l[3],
            "uf_sigla": l[4],
            "data_encerramento_proposta": l[5],
            "orgao_razao_social": l[6],
            "ressalvas": ressalvas(l[4], l[3], perfil),
        }
        for l in linhas
    ]
