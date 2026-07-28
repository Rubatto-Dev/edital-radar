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
import functools
import json
import time

from app.custo import Orcamento
from app.ferramentas import baixar_anexo, buscar_no_corpus, calcular_prazo, listar_anexos
from app.llm import MODELO, custo as custo_llm, system_prompt as system_prompt_base
from app.perfil import carregar


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
        # Tool results have to be strings, not raw Python objects — the API
        # rejects a dict/list content block with a 400 (found the hard way).
        return json.dumps(buscar_no_corpus(conn, pergunta, limite), default=str)

    def calcular_prazo_fn() -> str:
        """Calcula a urgência do prazo de proposta deste edital."""
        chamadas.append("calcular_prazo")
        return json.dumps(calcular_prazo(edital.get("data_encerramento_proposta")))

    def baixar_anexo_fn() -> str:
        """Baixa o primeiro anexo deste edital e detecta o tipo real por
        magic bytes. Só chame isso se a classificação continuar
        indeterminada depois de ler o objeto."""
        chamadas.append("baixar_anexo")
        cnpj = edital.get("cnpj")
        ano = edital.get("ano_compra")
        sequencial = edital.get("sequencial_compra")
        if not (cnpj and ano and sequencial):
            return json.dumps({"erro": "sem cnpj/ano/sequencial pra localizar anexos"})

        anexos = listar_anexos(cnpj, ano, sequencial)
        if not anexos:
            return json.dumps({"erro": "nenhum anexo disponível"})

        info = baixar_anexo(anexos[0]["url"])
        return json.dumps(
            {
                "titulo": anexos[0]["titulo"],
                "tipo_declarado": info["tipo_declarado"],
                "tipo_detectado": info["tipo_detectado"],
                "tamanho_bytes": info["tamanho_bytes"],
            }
        )

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
