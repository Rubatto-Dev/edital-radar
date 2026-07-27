"""Pure functions behind the agent's tool calls.

Phase 2.2 wires these to Claude via the Tool Runner — plain, typed functions
so their schema can be generated from the signature, no hand-written JSON
schema here. No Claude in this file: each function is testable in isolation,
the same way app/pncp.py and app/consulta.py already are.
"""

import datetime

import httpx

from app.consulta import buscar as buscar_corpus
from app.db import normalizar_valor
from app.embeddings import embedar
from app.pncp import buscar_pagina, limpar_objeto

# GET .../orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos — see
# docs/corpus-notes.md. Separate host from the consultation API (BASE in
# app/pncp.py); this is PNCP's document-listing endpoint, not the search one.
ARQUIVOS_URL = (
    "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
)


def consultar_pncp(
    data_inicial: str, data_final: str, pagina: int = 1, cliente: httpx.Client | None = None
) -> list[dict]:
    """Tenders published to the PNCP in a date window (YYYYMMDD).

    Returns a compact shape, not the raw API payload — small enough to fit
    in a tool result without blowing up the model's context.
    """
    registros = buscar_pagina(data_inicial, data_final, pagina, cliente=cliente)
    resultado = []
    for r in registros:
        unidade = r.get("unidadeOrgao") or {}
        orgao = r.get("orgaoEntidade") or {}
        resultado.append(
            {
                "numero_controle_pncp": r["numeroControlePNCP"],
                "objeto": limpar_objeto(r.get("objetoCompra") or ""),
                "valor_total_estimado": normalizar_valor(r.get("valorTotalEstimado")),
                "uf": unidade.get("ufSigla"),
                "orgao": orgao.get("razaoSocial"),
                "cnpj": orgao.get("cnpj"),
                "ano_compra": r.get("anoCompra"),
                "sequencial_compra": r.get("sequencialCompra"),
                "data_encerramento_proposta": r.get("dataEncerramentoProposta"),
            }
        )
    return resultado


def buscar_no_corpus(conn, pergunta: str, limite: int = 5) -> list[dict]:
    """Semantic search over the already-indexed corpus — same citation/link
    as /query (app/consulta.py); this just embeds the question first."""
    vetor = embedar([pergunta])[0]
    agora = datetime.datetime.now(datetime.timezone.utc)
    return buscar_corpus(conn, vetor, agora, limite)


DIAS_CRITICO = 3
DIAS_ATENCAO = 7


def calcular_prazo(data_encerramento_proposta, agora=None) -> dict:
    """Classifies how urgent a proposal deadline is.

    `data_encerramento_proposta` can be None — tenders with no defined
    deadline exist in the corpus (see app/filtros.py) and are not an error,
    they are "sem_prazo".
    """
    if data_encerramento_proposta is None:
        return {"dias_restantes": None, "urgencia": "sem_prazo"}

    if isinstance(data_encerramento_proposta, str):
        data_encerramento_proposta = datetime.datetime.fromisoformat(
            data_encerramento_proposta
        )

    agora = agora or datetime.datetime.now(datetime.timezone.utc)
    dias = (data_encerramento_proposta - agora).days

    if dias < 0:
        urgencia = "encerrado"
    elif dias < DIAS_CRITICO:
        urgencia = "critico"
    elif dias < DIAS_ATENCAO:
        urgencia = "atencao"
    else:
        urgencia = "tranquilo"

    return {"dias_restantes": dias, "urgencia": urgencia}


def listar_anexos(
    cnpj: str, ano: int, sequencial: int, cliente: httpx.Client | None = None
) -> list[dict]:
    """Lists a tender's attachments.

    `tipo_documento_nome` is API metadata, not trustworthy (see
    docs/corpus-notes.md: a 152-word document labeled "Edital" was just an
    announcement pointing at the municipality's website) — it does not
    decide the file's real type. That is `detectar_tipo`'s job.
    """
    proprio = cliente is None
    cliente = cliente or httpx.Client(timeout=30)
    try:
        r = cliente.get(ARQUIVOS_URL.format(cnpj=cnpj, ano=ano, sequencial=sequencial))
        r.raise_for_status()
        dados = r.json() or []
        return [
            {
                "url": a.get("url"),
                "titulo": a.get("titulo"),
                "tipo_documento_nome": a.get("tipoDocumentoNome"),
            }
            for a in dados
        ]
    finally:
        if proprio:
            cliente.close()


# Magic-byte signatures for the file types actually observed in the corpus.
# Attachments arrive as application/octet-stream regardless of real type
# (a .docx served with that content-type was the first case sampled — see
# docs/corpus-notes.md), so extension and content-type can't be trusted.
ASSINATURAS = [
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip_ooxml"),  # .docx/.xlsx/.pptx are all zip containers
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
]


def detectar_tipo(conteudo: bytes) -> str:
    for assinatura, tipo in ASSINATURAS:
        if conteudo.startswith(assinatura):
            return tipo
    return "desconhecido"


def baixar_anexo(url: str, cliente: httpx.Client | None = None) -> dict:
    """Downloads an attachment and detects its real type from magic bytes.

    `conteudo_bytes` stays in the dict for the Python caller to decide what
    to do with (text extraction is a future phase's job, not this
    function's) — it is not meant to go straight into a tool result, which
    has to be JSON.
    """
    proprio = cliente is None
    cliente = cliente or httpx.Client(timeout=60)
    try:
        r = cliente.get(url)
        r.raise_for_status()
        conteudo = r.content
        tipo_declarado = r.headers.get("content-type")
    finally:
        if proprio:
            cliente.close()

    return {
        "conteudo_bytes": conteudo,
        "tamanho_bytes": len(conteudo),
        "tipo_declarado": tipo_declarado,
        "tipo_detectado": detectar_tipo(conteudo),
    }
