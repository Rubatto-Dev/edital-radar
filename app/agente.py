"""The judgment loop with real tool calling — layer 3 with autonomy.

Unlike app/llm.py's single structured call, this lets Claude decide which
tools to reach for (corpus context, deadline math, and — only when the
verdict is still `indeterminado` after reading the object — the attachment)
before settling on a verdict. The verdict itself arrives as a tool call
(`registrar_decisao`), not free text parsed off whatever shape the final
message happens to take — a terminal tool is the reliable signal (see
shared/agent-design.md's "give Claude a tool that records the transition").

Tool-building is a separate function (`construir_ferramentas`) returning
plain, undecorated callables — `julgar_com_agente` wraps them with
`@beta_tool` only at the point of handing them to the SDK. That seam is what
makes the tool logic testable without going through the real Tool Runner.
"""

import dataclasses
import datetime
import functools
import json
import logging
import time

import httpx

from app.consulta import NUMERO_CONTROLE_RE
from app.custo import Orcamento, TetoEstourado
from app.db import pool
from app.embeddings import LIMIAR_FUNIL, embedar, similaridade, texto_do_perfil
from app.ferramentas import baixar_anexo, buscar_no_corpus, calcular_prazo, listar_anexos
from app.filtros import candidatos
from app.indexar import indexar
from app.ingest import ingerir
from app.llm import MODELO
from app.llm import custo as custo_llm
from app.llm import system_prompt as system_prompt_base
from app.notificacao import notificar_se_relevante
from app.perfil import carregar
from app.pncp import PncpIndisponivel
from app.tracing import descarregar, ferramenta, observar

log = logging.getLogger(__name__)


@dataclasses.dataclass
class DecisaoAgente:
    classe: str
    justificativa: str
    lote_misto: bool
    ferramentas_chamadas: list[str]
    anexo_baixado: bool
    custo_usd: float
    latencia_s: float
    modelo: str


@functools.lru_cache(maxsize=1)
def _client():
    # Imported lazily so tests never need the SDK installed or a key set.
    import anthropic

    return anthropic.Anthropic()


def system_prompt(perfil: dict) -> list[dict]:
    prompt = system_prompt_base(perfil)
    prompt[0]["text"] += (
        "\n\nVocê tem ferramentas disponíveis. Use buscar_no_corpus se precisar "
        "de contexto sobre editais parecidos já indexados. Use calcular_prazo "
        "pra saber a urgência do prazo. Se depois de avaliar o objeto a classe "
        "ainda for indeterminado, use baixar_anexo antes de decidir — não chute "
        "sem checar o anexo primeiro. Termine SEMPRE chamando registrar_decisao, "
        "mesmo se a classe continuar indeterminado depois de ver o anexo."
    )
    return prompt


def construir_ferramentas(edital: dict, conn):
    """Builds the plain (undecorated) tool functions for one judgment call.

    Returns (funcoes, chamadas, decisao_capturada): `funcoes` is a
    name -> callable dict; `chamadas` and `decisao_capturada` are the
    mutable containers the closures write to as Claude calls them, read
    back by julgar_com_agente once the loop stops.
    """
    chamadas: list[str] = []
    decisao_capturada: dict = {}

    def buscar_no_corpus_fn(pergunta: str, limite: int = 5) -> str:
        """Busca semântica no corpus de editais já indexados — usa pra achar
        contexto sobre editais parecidos, não pra decidir sozinho.

        Args:
            pergunta: o que buscar, em texto livre.
            limite: quantos resultados no máximo.
        """
        chamadas.append("buscar_no_corpus")
        with ferramenta("buscar_no_corpus", pergunta=pergunta, limite=limite) as obs:
            # Tool results have to be strings, not raw Python objects — the API
            # rejects a dict/list content block with a 400 (found the hard way).
            resultado = json.dumps(buscar_no_corpus(conn, pergunta, limite), default=str)
            obs.update(output=resultado)
            return resultado

    def calcular_prazo_fn() -> str:
        """Calcula a urgência do prazo de proposta deste edital."""
        chamadas.append("calcular_prazo")
        with ferramenta("calcular_prazo") as obs:
            resultado = json.dumps(calcular_prazo(edital.get("data_encerramento_proposta")))
            obs.update(output=resultado)
            return resultado

    def baixar_anexo_fn() -> str:
        """Baixa o primeiro anexo deste edital e detecta o tipo real por
        magic bytes. Só chame isso se a classificação continuar
        indeterminada depois de ler o objeto."""
        chamadas.append("baixar_anexo")

        # Kept as its own function so the early returns below survive
        # untouched: this is the tool whose failure paths the agent reasons
        # about, and flattening them to add a span would be rewriting the
        # part that matters to get at the part that does not.
        def executar() -> str:
            cnpj = edital.get("cnpj")
            ano = edital.get("ano_compra")
            sequencial = edital.get("sequencial_compra")
            if not (cnpj and ano and sequencial):
                return json.dumps({"erro": "sem cnpj/ano/sequencial pra localizar anexos"})

            # A real failure here (unreachable PNCP, corrupted download) must not
            # crash the loop — it becomes something Claude can reason about
            # (e.g. fall back to indeterminado) instead of an unhandled 500 that
            # takes the whole daily run down with it.
            try:
                anexos = listar_anexos(cnpj, ano, sequencial)
                if not anexos:
                    return json.dumps({"erro": "nenhum anexo disponível"})
                info = baixar_anexo(anexos[0]["url"])
            except httpx.HTTPError as e:
                return json.dumps({"erro": f"falha ao baixar anexo: {e}"})

            return json.dumps(
                {
                    "titulo": anexos[0]["titulo"],
                    "tipo_declarado": info["tipo_declarado"],
                    "tipo_detectado": info["tipo_detectado"],
                    "tamanho_bytes": info["tamanho_bytes"],
                }
            )

        with ferramenta("baixar_anexo") as obs:
            resultado = executar()
            obs.update(output=resultado)
            return resultado

    def registrar_decisao_fn(classe: str, justificativa: str, lote_misto: bool) -> str:
        """Registra a decisão final. SEMPRE a última coisa que você faz.

        Args:
            classe: relevante, nao_relevante ou indeterminado.
            justificativa: por que essa classe.
            lote_misto: True se o edital mistura escopo dentro e fora do perfil.
        """
        decisao_capturada.update(
            classe=classe, justificativa=justificativa, lote_misto=lote_misto
        )
        # Traced like the others: this is the terminal tool, so its span is
        # what shows the loop actually reached a verdict rather than running
        # out of turns.
        with ferramenta(
            "registrar_decisao",
            classe=classe,
            justificativa=justificativa,
            lote_misto=lote_misto,
        ) as obs:
            obs.update(output="decisão registrada")
        return "decisão registrada"

    funcoes = {
        "buscar_no_corpus": buscar_no_corpus_fn,
        "calcular_prazo": calcular_prazo_fn,
        "baixar_anexo": baixar_anexo_fn,
        "registrar_decisao": registrar_decisao_fn,
    }
    return funcoes, chamadas, decisao_capturada


def julgar_com_agente(
    edital: dict,
    perfil: dict | None = None,
    *,
    conn,
    client=None,
    orcamento: Orcamento | None = None,
    modelo: str = MODELO,
) -> DecisaoAgente:
    perfil = perfil or carregar()
    client = client or _client()

    if orcamento is not None:
        orcamento.verificar()

    funcoes, chamadas, decisao_capturada = construir_ferramentas(edital, conn)

    from anthropic import beta_tool

    tools = [beta_tool(f) for f in funcoes.values()]

    mensagem_usuario = (
        f"Objeto: {edital.get('objeto')}\n"
        f"Órgão: {edital.get('orgao')}\n"
        f"UF: {edital.get('uf')}\n"
        f"Valor estimado: {edital.get('valor_total_estimado')}\n"
        f"Prazo de proposta: {edital.get('data_encerramento_proposta')}\n"
    )

    # The tool spans built in construir_ferramentas nest under this one on
    # their own: the SDK propagates context, so the trace ends up shaped like
    # the decision — which tools were reached for, in what order, before the
    # verdict. That shape is the audit trail decisao_agente records in SQL,
    # here made visible.
    with observar(
        "julgar_com_agente",
        as_type="agent",
        input={
            "objeto": edital.get("objeto"),
            "numero_controle_pncp": edital.get("numero_controle_pncp"),
        },
    ) as obs:
        inicio = time.monotonic()
        runner = client.beta.messages.tool_runner(
            model=modelo,
            max_tokens=1024,
            system=system_prompt(perfil),
            tools=tools,
            messages=[{"role": "user", "content": mensagem_usuario}],
        )

        custo_total = 0.0
        for mensagem in runner:
            uso = getattr(mensagem, "usage", None)
            if uso is not None:
                custo_total += custo_llm(uso, modelo)
        latencia = time.monotonic() - inicio

        if not decisao_capturada:
            raise RuntimeError(
                "o agente terminou o loop sem chamar registrar_decisao — "
                f"ferramentas chamadas: {chamadas}"
            )

        obs.update(
            output=dict(decisao_capturada),
            metadata={
                "ferramentas_chamadas": chamadas,
                "modelo": modelo,
                "latencia_s": latencia,
            },
            # Reported once, here. The tool_runner's own calls do not go
            # through app.llm.julgar, so there is no inner generation
            # reporting the same spend twice.
            cost_details={"total": custo_total},
        )

    resultado = DecisaoAgente(
        classe=decisao_capturada["classe"],
        justificativa=decisao_capturada["justificativa"],
        lote_misto=decisao_capturada["lote_misto"],
        ferramentas_chamadas=chamadas,
        anexo_baixado="baixar_anexo" in chamadas,
        custo_usd=custo_total,
        latencia_s=latencia,
        modelo=modelo,
    )

    if orcamento is not None:
        orcamento.registrar(resultado.custo_usd, resultado.latencia_s)

    return resultado


REGISTRAR_LOG_SQL = """
INSERT INTO decisao_agente (
    numero_controle_pncp, classe, justificativa, lote_misto,
    ferramentas_chamadas, anexo_baixado, custo_usd, latencia_s, modelo
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""


def registrar_log(conn, numero_controle_pncp: str, decisao: DecisaoAgente) -> int:
    """Writes the auditable decision record. Append-only — a tender can be
    re-judged (a new ingest, a prompt change), and each judgment is its own
    row, the same way ingestao_execucao never overwrites a past run."""
    linha = conn.execute(
        REGISTRAR_LOG_SQL,
        (
            numero_controle_pncp,
            decisao.classe,
            decisao.justificativa,
            decisao.lote_misto,
            decisao.ferramentas_chamadas,
            decisao.anexo_baixado,
            decisao.custo_usd,
            decisao.latencia_s,
            decisao.modelo,
        ),
    ).fetchone()
    return linha[0]


# ---------------------------------------------------------------------------
# Daily driver: python -m app.agente --de --ate
# ---------------------------------------------------------------------------


def ja_julgados(conn) -> set:
    """Tenders that already have a decision on record — skipped so a
    re-run doesn't re-spend on the same candidate."""
    linhas = conn.execute("SELECT DISTINCT numero_controle_pncp FROM decisao_agente").fetchall()
    return {linha[0] for linha in linhas}


def _para_edital(candidato: dict) -> dict:
    """Reshapes a layer-1 candidate row (app.filtros.candidatos) into the
    edital shape julgar_com_agente expects. cnpj/ano/sequencial are not
    stored columns — numeroControlePNCP already encodes them (see
    docs/corpus-notes.md), so they are parsed from it rather than adding a
    schema change just to carry three integers."""
    edital = {
        "numero_controle_pncp": candidato["numero_controle_pncp"],
        "objeto": candidato["objeto_limpo"] or candidato["objeto_compra"],
        "orgao": candidato["orgao_razao_social"],
        "uf": candidato["uf_sigla"],
        "valor_total_estimado": candidato["valor_total_estimado"],
        "data_encerramento_proposta": candidato["data_encerramento_proposta"],
    }
    m = NUMERO_CONTROLE_RE.match(candidato["numero_controle_pncp"])
    if m:
        edital["cnpj"] = m["cnpj"]
        edital["ano_compra"] = int(m["ano"])
        edital["sequencial_compra"] = int(m["sequencial"])
    return edital


def selecionar_finalistas(conn, perfil: dict, limiar: float = LIMIAR_FUNIL) -> list[dict]:
    """Layer 1 (structured filter, never drops — only caveats) then layer 2
    (funnel) on top of whatever the agent has not already judged."""
    candidatos_l1 = candidatos(conn, datetime.datetime.now(datetime.UTC), perfil)
    julgados = ja_julgados(conn)
    candidatos_l1 = [c for c in candidatos_l1 if c["numero_controle_pncp"] not in julgados]
    if not candidatos_l1:
        return []

    vetor_perfil = embedar([texto_do_perfil(perfil)])[0]
    objetos = [c["objeto_limpo"] or c["objeto_compra"] for c in candidatos_l1]
    vetores = embedar(objetos)

    return [
        c
        for c, v in zip(candidatos_l1, vetores, strict=True)
        if similaridade(vetor_perfil, v) >= limiar
    ]


INICIAR_EXECUCAO_SQL = """
INSERT INTO agente_execucao (data_inicial, data_final, status)
VALUES (%s, %s, 'falha') RETURNING id
"""

FINALIZAR_EXECUCAO_SQL = """
UPDATE agente_execucao
   SET status = %s, candidatos_avaliados = %s, decisoes_relevantes = %s,
       erro = %s, terminado_em = now()
 WHERE id = %s
"""


def _status_final(erro: str | None, avaliados: int) -> str:
    """ok / parcial / falha. `parcial` exists because a window that judged some
    candidates before the spend cap tripped is not the same outcome as one that
    never got off the ground, and the difference matters when reading the log."""
    if erro and avaliados:
        return "parcial"
    return "falha" if erro else "ok"


def _julgar_finalistas(finalistas, perfil, orcamento, modelo):
    """Judges each finalist, logging and notifying, and returns
    (avaliados, relevantes, falhas, erro).

    Split out of `executar_janela` so the orchestration up there reads as the
    handful of steps it is, and the per-candidate failure policy lives in one
    place: the spend cap stops the whole window, anything else costs exactly
    one candidate and the run continues.
    """
    avaliados = relevantes = falhas = 0

    for candidato in finalistas:
        edital = _para_edital(candidato)
        with pool.connection() as conn:
            try:
                decisao = julgar_com_agente(
                    edital, perfil, conn=conn, orcamento=orcamento, modelo=modelo
                )
            except TetoEstourado as e:
                return avaliados, relevantes, falhas, str(e)
            except Exception as e:  # noqa: BLE001 — one bad candidate must not abort the window
                # Logged and counted, not swallowed silently: falhas_finalista
                # shows up in the run's own summary.
                log.warning("falha ao julgar %s: %s", edital["numero_controle_pncp"], e)
                falhas += 1
                continue

            decisao_id = registrar_log(conn, edital["numero_controle_pncp"], decisao)
            notificar_se_relevante(conn, decisao_id, decisao)

        avaliados += 1
        if decisao.classe == "relevante":
            relevantes += 1

    return avaliados, relevantes, falhas, None


def executar_janela(
    data_inicial: str,
    data_final: str,
    perfil: dict | None = None,
    modelo: str = MODELO,
    teto_usd: float | None = None,
    limite_finalistas: int | None = None,
) -> dict:
    """The daily run: ingest the window, backfill embeddings, judge every
    layer-2 survivor not already judged, log and notify each verdict.

    Same ok/parcial/falha discipline as app.ingest.ingerir — written as
    'falha' before any work starts, promoted at the end, so a killed
    process leaves a record that says it failed, which is true.
    """
    perfil = perfil or carregar()
    orcamento = Orcamento(teto_usd) if teto_usd is not None else Orcamento()

    with pool.connection() as conn:
        execucao_id = conn.execute(
            INICIAR_EXECUCAO_SQL, (data_inicial, data_final)
        ).fetchone()[0]

    avaliados = 0
    relevantes = 0
    falhas_finalista = 0
    erro = None

    try:
        resultado_ingest = ingerir(data_inicial, data_final)
        if resultado_ingest["status"] == "falha":
            raise PncpIndisponivel(resultado_ingest["erro"] or "ingestao falhou")
        indexar()

        with pool.connection() as conn:
            finalistas = selecionar_finalistas(conn, perfil)
        if limite_finalistas is not None:
            finalistas = finalistas[:limite_finalistas]

        avaliados, relevantes, falhas_finalista, erro = _julgar_finalistas(
            finalistas, perfil, orcamento, modelo
        )
    except PncpIndisponivel as e:
        erro = str(e)

    status = _status_final(erro, avaliados)

    with pool.connection() as conn:
        conn.execute(
            FINALIZAR_EXECUCAO_SQL,
            (status, avaliados, relevantes, erro, execucao_id),
        )

    return {
        "execucao_id": execucao_id,
        "status": status,
        "candidatos_avaliados": avaliados,
        "decisoes_relevantes": relevantes,
        "falhas_finalista": falhas_finalista,
        "erro": erro,
    }


def main():
    import argparse

    p = argparse.ArgumentParser(description="Roda o agente de monitoramento numa janela de datas")
    p.add_argument("--de", required=True, help="data inicial YYYYMMDD")
    p.add_argument("--ate", required=True, help="data final YYYYMMDD")
    p.add_argument("--teto-usd", type=float, default=None)
    p.add_argument("--limite-finalistas", type=int, default=None)
    p.add_argument("--modelo", default=MODELO)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pool.open()
    try:
        resultado = executar_janela(
            args.de,
            args.ate,
            modelo=args.modelo,
            teto_usd=args.teto_usd,
            limite_finalistas=args.limite_finalistas,
        )
    finally:
        pool.close()
        # The SDK batches in the background, so a CLI run this short would
        # exit with the traces still queued.
        descarregar()

    print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
    raise SystemExit(0 if resultado["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
