"""The agent loop: tool wiring, cost accounting, and the contract that it
never finishes without registering a decision.

No real Tool Runner here — construir_ferramentas returns plain callables
tested directly, and julgar_com_agente's orchestration is tested through a
fake client + a monkeypatched construir_ferramentas, so neither test needs
the real Anthropic SDK loop or a network call.
"""

import json
from types import SimpleNamespace

import pytest

from app.agente import construir_ferramentas, julgar_com_agente, registrar_log
from app.llm import custo as custo_llm

EDITAL = {
    "numero_controle_pncp": "07753868000101-1-000003/2026",
    "objeto": "Locação de sistema de gestão municipal",
    "orgao": "Prefeitura de Exemplo",
    "uf": "SC",
    "valor_total_estimado": 300000.0,
    "data_encerramento_proposta": "2026-08-15T00:00:00+00:00",
    "cnpj": "07753868000101",
    "ano_compra": 2026,
    "sequencial_compra": 3,
}

PERFIL_MINIMO = {"fornece": [], "nao_fornece": [], "descricao_curta": "x"}


class TestConstruirFerramentas:
    def test_buscar_no_corpus_delega_e_registra_a_chamada(self, monkeypatch):
        chamadas_reais = {}

        def buscar_falso(conn, pergunta, limite):
            chamadas_reais["args"] = (conn, pergunta, limite)
            return [{"trecho": "achado"}]

        monkeypatch.setattr("app.agente.buscar_no_corpus", buscar_falso)
        funcoes, chamadas, _ = construir_ferramentas(EDITAL, conn="conexao-falsa")

        resultado = funcoes["buscar_no_corpus"]("sistema de gestão", limite=3)

        # Tool results have to be JSON strings — the real API rejects a raw
        # dict/list content block with a 400.
        assert isinstance(resultado, str)
        assert json.loads(resultado) == [{"trecho": "achado"}]
        assert chamadas == ["buscar_no_corpus"]
        assert chamadas_reais["args"] == ("conexao-falsa", "sistema de gestão", 3)

    def test_calcular_prazo_usa_o_prazo_do_proprio_edital(self):
        funcoes, chamadas, _ = construir_ferramentas(EDITAL, conn=None)
        resultado = funcoes["calcular_prazo"]()
        assert isinstance(resultado, str)
        assert "urgencia" in json.loads(resultado)
        assert chamadas == ["calcular_prazo"]

    def test_baixar_anexo_sem_localizadores_nao_quebra(self):
        funcoes, chamadas, _ = construir_ferramentas({"objeto": "x"}, conn=None)
        resultado = funcoes["baixar_anexo"]()
        assert json.loads(resultado) == {"erro": "sem cnpj/ano/sequencial pra localizar anexos"}
        assert chamadas == ["baixar_anexo"]

    def test_baixar_anexo_sem_anexos_disponiveis(self, monkeypatch):
        monkeypatch.setattr("app.agente.listar_anexos", lambda *a, **k: [])
        funcoes, _, _ = construir_ferramentas(EDITAL, conn=None)
        assert json.loads(funcoes["baixar_anexo"]()) == {"erro": "nenhum anexo disponível"}

    def test_baixar_anexo_baixa_o_primeiro_e_resume_sem_os_bytes(self, monkeypatch):
        monkeypatch.setattr(
            "app.agente.listar_anexos",
            lambda *a, **k: [{"url": "https://x/1", "titulo": "Edital"}],
        )
        monkeypatch.setattr(
            "app.agente.baixar_anexo",
            lambda url: {
                "conteudo_bytes": b"PK\x03\x04resto",
                "tamanho_bytes": 12,
                "tipo_declarado": "application/octet-stream",
                "tipo_detectado": "zip_ooxml",
            },
        )
        funcoes, _, _ = construir_ferramentas(EDITAL, conn=None)
        resultado = json.loads(funcoes["baixar_anexo"]())

        assert resultado == {
            "titulo": "Edital",
            "tipo_declarado": "application/octet-stream",
            "tipo_detectado": "zip_ooxml",
            "tamanho_bytes": 12,
        }
        assert "conteudo_bytes" not in resultado  # não vai bytes cru pro tool result

    def test_registrar_decisao_captura_os_campos(self):
        funcoes, _, decisao_capturada = construir_ferramentas(EDITAL, conn=None)
        funcoes["registrar_decisao"]("relevante", "justificativa", True)
        assert decisao_capturada == {
            "classe": "relevante",
            "justificativa": "justificativa",
            "lote_misto": True,
        }


def _mensagem(usage_tokens=None):
    if usage_tokens is None:
        return SimpleNamespace(usage=None)
    entrada, saida = usage_tokens
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=entrada,
            output_tokens=saida,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
    )


class ClienteFalso:
    def __init__(self, mensagens):
        self.mensagens = mensagens
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self._tool_runner))

    def _tool_runner(self, **kwargs):
        return iter(self.mensagens)


class TestJulgarComAgente:
    def test_soma_o_custo_de_cada_turno_do_loop(self, monkeypatch):
        def construir_falso(edital, conn):
            chamadas = ["calcular_prazo"]
            decisao = {"classe": "relevante", "justificativa": "ok", "lote_misto": False}
            return ({"x": lambda: None}, chamadas, decisao)

        monkeypatch.setattr("app.agente.construir_ferramentas", construir_falso)
        cliente = ClienteFalso([_mensagem((100, 20)), _mensagem((50, 10))])

        resultado = julgar_com_agente(EDITAL, perfil=PERFIL_MINIMO, conn=None, client=cliente)

        esperado = custo_llm(_mensagem((100, 20)).usage) + custo_llm(_mensagem((50, 10)).usage)
        assert resultado.custo_usd == pytest.approx(esperado)
        assert resultado.classe == "relevante"
        assert resultado.ferramentas_chamadas == ["calcular_prazo"]
        assert resultado.anexo_baixado is False

    def test_anexo_baixado_reflete_a_ferramenta_chamada(self, monkeypatch):
        def construir_falso(edital, conn):
            chamadas = ["calcular_prazo", "baixar_anexo"]
            decisao = {"classe": "indeterminado", "justificativa": "x", "lote_misto": False}
            return ({}, chamadas, decisao)

        monkeypatch.setattr("app.agente.construir_ferramentas", construir_falso)
        cliente = ClienteFalso([_mensagem((10, 5))])

        resultado = julgar_com_agente(EDITAL, perfil=PERFIL_MINIMO, conn=None, client=cliente)

        assert resultado.anexo_baixado is True

    def test_levanta_erro_se_o_loop_termina_sem_registrar_decisao(self, monkeypatch):
        def construir_falso(edital, conn):
            return ({}, ["calcular_prazo"], {})  # decisao_capturada vazia

        monkeypatch.setattr("app.agente.construir_ferramentas", construir_falso)
        cliente = ClienteFalso([_mensagem((10, 5))])

        with pytest.raises(RuntimeError, match="registrar_decisao"):
            julgar_com_agente(EDITAL, perfil=PERFIL_MINIMO, conn=None, client=cliente)

    def test_teto_estourado_impede_a_chamada(self, monkeypatch, tmp_path):
        from app.custo import Orcamento, TetoEstourado

        monkeypatch.setattr(
            "app.agente.construir_ferramentas",
            lambda edital, conn: (
                {},
                [],
                {"classe": "relevante", "justificativa": "x", "lote_misto": False},
            ),
        )
        orcamento = Orcamento(teto_usd=0.0000001, caminho=tmp_path / "ledger.jsonl")
        orcamento.registrar(1.0)

        cliente = ClienteFalso([_mensagem((10, 5))])
        with pytest.raises(TetoEstourado):
            julgar_com_agente(
                EDITAL,
                perfil={"fornece": [], "nao_fornece": [], "descricao_curta": "x"},
                conn=None,
                client=cliente,
                orcamento=orcamento,
            )


class ConexaoFalsa:
    def __init__(self):
        self.chamadas = []

    def execute(self, sql, params=None):
        self.chamadas.append((sql, params))
        return self

    def fetchone(self):
        return (42,)


class TestRegistrarLog:
    def test_grava_todos_os_campos_da_decisao(self):
        from app.agente import DecisaoAgente

        decisao = DecisaoAgente(
            classe="relevante",
            justificativa="justificativa",
            lote_misto=True,
            ferramentas_chamadas=["calcular_prazo", "baixar_anexo"],
            anexo_baixado=True,
            custo_usd=0.001,
            latencia_s=1.5,
            modelo="claude-haiku-4-5",
        )
        conn = ConexaoFalsa()

        id_gravado = registrar_log(conn, "07753868000101-1-000003/2026", decisao)

        assert id_gravado == 42
        _, params = conn.chamadas[0]
        assert params[0] == "07753868000101-1-000003/2026"
        assert params[1] == "relevante"
        assert params[4] == ["calcular_prazo", "baixar_anexo"]
