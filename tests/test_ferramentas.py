"""The agent's tool functions, tested without Claude and without real
network — same MockTransport pattern as tests/test_pncp.py, same fake
connection pattern as tests/test_consulta.py.
"""

import datetime

import httpx
import pytest

from app.ferramentas import (
    baixar_anexo,
    buscar_no_corpus,
    calcular_prazo,
    consultar_pncp,
    detectar_tipo,
    listar_anexos,
)


def _cliente(respostas):
    it = iter(respostas)

    def handler(request):
        return next(it)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestConsultarPncp:
    def test_devolve_forma_compacta_nao_o_payload_bruto(self):
        registro = {
            "numeroControlePNCP": "07753868000101-1-000003/2026",
            "objetoCompra": "[LICITANET] - Locação de sistema de gestão",
            "valorTotalEstimado": 0.0,
            "unidadeOrgao": {"ufSigla": "SC"},
            "orgaoEntidade": {"razaoSocial": "Prefeitura X", "cnpj": "07753868000101"},
            "anoCompra": 2026,
            "sequencialCompra": 3,
            "dataEncerramentoProposta": "2026-08-15T00:00:00",
        }
        cliente = _cliente([httpx.Response(200, json={"data": [registro]})])

        resultado = consultar_pncp("20260701", "20260715", cliente=cliente)

        assert resultado == [
            {
                "numero_controle_pncp": "07753868000101-1-000003/2026",
                "objeto": "Locação de sistema de gestão",  # prefixo removido
                "valor_total_estimado": None,  # 0.0 -> desconhecido, não zero
                "uf": "SC",
                "orgao": "Prefeitura X",
                "cnpj": "07753868000101",
                "ano_compra": 2026,
                "sequencial_compra": 3,
                "data_encerramento_proposta": "2026-08-15T00:00:00",
            }
        ]

    def test_pagina_vazia_e_lista_vazia(self):
        cliente = _cliente([httpx.Response(204)])
        assert consultar_pncp("20260701", "20260715", cliente=cliente) == []


class TestBuscarNoCorpus:
    def test_embeda_a_pergunta_e_delega_pra_buscar(self, monkeypatch):
        chamadas = {}

        def embedar_falso(textos):
            chamadas["textos"] = textos
            return [[0.1, 0.2]]

        def buscar_falso(conn, vetor, agora, limite):
            chamadas["vetor"] = vetor
            chamadas["limite"] = limite
            return [{"trecho": "resultado"}]

        monkeypatch.setattr("app.ferramentas.embedar", embedar_falso)
        monkeypatch.setattr("app.ferramentas.buscar_corpus", buscar_falso)

        resultado = buscar_no_corpus(conn=object(), pergunta="sistema de gestão", limite=3)

        assert resultado == [{"trecho": "resultado"}]
        assert chamadas["textos"] == ["sistema de gestão"]
        assert chamadas["vetor"] == [0.1, 0.2]
        assert chamadas["limite"] == 3


class TestCalcularPrazo:
    AGORA = datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc)

    def test_sem_prazo_nao_e_erro(self):
        assert calcular_prazo(None, agora=self.AGORA) == {
            "dias_restantes": None,
            "urgencia": "sem_prazo",
        }

    def test_prazo_critico(self):
        prazo = self.AGORA + datetime.timedelta(days=2)
        assert calcular_prazo(prazo, agora=self.AGORA)["urgencia"] == "critico"

    def test_prazo_atencao(self):
        prazo = self.AGORA + datetime.timedelta(days=5)
        assert calcular_prazo(prazo, agora=self.AGORA)["urgencia"] == "atencao"

    def test_prazo_tranquilo(self):
        prazo = self.AGORA + datetime.timedelta(days=30)
        assert calcular_prazo(prazo, agora=self.AGORA)["urgencia"] == "tranquilo"

    def test_prazo_encerrado(self):
        prazo = self.AGORA - datetime.timedelta(days=1)
        r = calcular_prazo(prazo, agora=self.AGORA)
        assert r["urgencia"] == "encerrado"
        assert r["dias_restantes"] < 0

    def test_aceita_string_iso(self):
        r = calcular_prazo("2026-07-29T00:00:00+00:00", agora=self.AGORA)
        assert r["urgencia"] == "critico"

    def test_aceita_string_sem_fuso(self):
        # o PNCP devolve data sem offset; `fromisoformat` a lê como naive e a
        # subtração com um `agora` aware estourava TypeError
        r = calcular_prazo("2026-07-29T00:00:00", agora=self.AGORA)
        assert r["urgencia"] == "critico"

    def test_aceita_string_so_data(self):
        r = calcular_prazo("2026-07-29", agora=self.AGORA)
        assert r["urgencia"] == "critico"

    def test_aceita_agora_naive(self):
        agora = self.AGORA.replace(tzinfo=None)
        prazo = "2026-07-29T00:00:00+00:00"
        assert calcular_prazo(prazo, agora=agora)["urgencia"] == "critico"


class TestListarAnexos:
    def test_tipo_documento_nome_vem_junto_mas_nao_e_a_ultima_palavra(self):
        cliente = _cliente(
            [
                httpx.Response(
                    200,
                    json=[
                        {
                            "url": "https://pncp.gov.br/arquivo/1",
                            "titulo": "Edital",
                            "tipoDocumentoNome": "Edital",
                        }
                    ],
                )
            ]
        )
        resultado = listar_anexos("07753868000101", 2026, 3, cliente=cliente)
        assert resultado == [
            {
                "url": "https://pncp.gov.br/arquivo/1",
                "titulo": "Edital",
                "tipo_documento_nome": "Edital",
            }
        ]


class TestDetectarTipo:
    def test_docx_e_um_zip_por_dentro(self):
        # A real trap from the corpus: a .docx served as
        # application/octet-stream — the extension/content-type lie, the
        # magic bytes don't.
        assert detectar_tipo(b"PK\x03\x04" + b"resto do arquivo") == "zip_ooxml"

    def test_pdf(self):
        assert detectar_tipo(b"%PDF-1.7\n...") == "pdf"

    def test_desconhecido(self):
        assert detectar_tipo(b"nao e nenhum dos formatos conhecidos") == "desconhecido"


class TestBaixarAnexo:
    def test_tipo_detectado_diverge_do_declarado(self):
        # The exact scenario docs/corpus-notes.md flags: content-type says
        # octet-stream, the bytes say docx.
        cliente = _cliente(
            [
                httpx.Response(
                    200,
                    content=b"PK\x03\x04resto",
                    headers={"content-type": "application/octet-stream"},
                )
            ]
        )
        resultado = baixar_anexo("https://pncp.gov.br/arquivo/1", cliente=cliente)

        assert resultado["tipo_declarado"] == "application/octet-stream"
        assert resultado["tipo_detectado"] == "zip_ooxml"
        assert resultado["tamanho_bytes"] == len(b"PK\x03\x04resto")
        assert resultado["conteudo_bytes"] == b"PK\x03\x04resto"

    def test_erro_http_propaga(self):
        cliente = _cliente([httpx.Response(404)])
        with pytest.raises(httpx.HTTPStatusError):
            baixar_anexo("https://pncp.gov.br/arquivo/inexistente", cliente=cliente)
