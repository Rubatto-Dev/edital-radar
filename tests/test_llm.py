"""Layer 3 parsing and cost accounting — no network, no API key.

A fake client stands in for anthropic.Anthropic(): julgar() takes `client`
as an injectable dependency for exactly this reason (see app/embeddings.py's
lazy _modelo() for the same pattern applied to the local model).
"""

from types import SimpleNamespace

import pytest

from app.custo import Orcamento
from app.llm import CLASSES, custo, julgar, system_prompt
from app.perfil import carregar

PERFIL = carregar()


def _bloco_texto(texto):
    return SimpleNamespace(type="text", text=texto)


def _resposta(json_texto, *, input_tokens=100, output_tokens=20, cache_read=0, cache_write=0):
    return SimpleNamespace(
        content=[_bloco_texto(json_texto)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


class ClienteFalso:
    def __init__(self, resposta):
        self._resposta = resposta
        self.chamadas = []
        self.messages = self

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        return self._resposta


def test_julgar_parseia_classe_justificativa_e_lote_misto():
    resposta = _resposta(
        '{"classe": "relevante", "justificativa": "vende SaaS de gestão", '
        '"lote_misto": false}'
    )
    cliente = ClienteFalso(resposta)

    julgamento = julgar("Locação de sistema de gestão municipal", PERFIL, client=cliente)

    assert julgamento.classe == "relevante"
    assert julgamento.justificativa == "vende SaaS de gestão"
    assert julgamento.lote_misto is False
    assert julgamento.classe in CLASSES


def test_julgar_pode_se_abster():
    resposta = _resposta(
        '{"classe": "indeterminado", "justificativa": "objeto remete ao anexo", '
        '"lote_misto": false}'
    )
    cliente = ClienteFalso(resposta)

    julgamento = julgar("Aquisição de itens de TI conforme anexo", PERFIL, client=cliente)

    assert julgamento.classe == "indeterminado"


def test_julgar_usa_o_modelo_haiku_e_saida_estruturada():
    resposta = _resposta('{"classe": "nao_relevante", "justificativa": "x", "lote_misto": false}')
    cliente = ClienteFalso(resposta)

    julgar("qualquer objeto", PERFIL, client=cliente)

    chamada = cliente.chamadas[0]
    assert chamada["model"] == "claude-haiku-4-5"
    assert chamada["output_config"]["format"]["type"] == "json_schema"
    assert chamada["output_config"]["format"]["schema"]["properties"]["classe"]["enum"] == list(
        CLASSES
    )


def test_julgar_aceita_outro_modelo_para_ab_test():
    # The issue's own acceptance criterion: if Haiku underperforms on
    # recall, compare against Sonnet with the same prompt and schema.
    resposta = _resposta('{"classe": "relevante", "justificativa": "x", "lote_misto": false}')
    cliente = ClienteFalso(resposta)

    julgamento = julgar("objeto", PERFIL, modelo="claude-sonnet-5", client=cliente)

    assert cliente.chamadas[0]["model"] == "claude-sonnet-5"
    assert julgamento.custo_usd > 0


def test_custo_usa_a_tabela_de_preco_do_modelo():
    resposta = _resposta("{}", input_tokens=1000, output_tokens=1000)
    custo_haiku = custo(resposta.usage, modelo="claude-haiku-4-5")
    custo_sonnet = custo(resposta.usage, modelo="claude-sonnet-5")
    assert custo_sonnet > custo_haiku  # Sonnet custa mais por token


def test_custo_soma_input_output_e_cache():
    resposta = _resposta(
        '{"classe": "relevante", "justificativa": "x", "lote_misto": false}',
        input_tokens=1000,
        output_tokens=100,
        cache_read=500,
        cache_write=2000,
    )
    esperado = custo(resposta.usage)
    assert esperado > 0

    cliente = ClienteFalso(resposta)
    julgamento = julgar("objeto", PERFIL, client=cliente)
    assert julgamento.custo_usd == pytest.approx(esperado)


def test_julgar_registra_no_orcamento(tmp_path):
    resposta = _resposta('{"classe": "relevante", "justificativa": "x", "lote_misto": false}')
    cliente = ClienteFalso(resposta)
    orcamento = Orcamento(teto_usd=10.0, caminho=tmp_path / "ledger.jsonl")

    julgar("objeto", PERFIL, client=cliente, orcamento=orcamento)

    assert orcamento.caminho.exists()
    assert orcamento._gasto_hoje() > 0


def test_julgar_respeita_teto_estourado(tmp_path):
    resposta = _resposta('{"classe": "relevante", "justificativa": "x", "lote_misto": false}')
    cliente = ClienteFalso(resposta)
    orcamento = Orcamento(teto_usd=0.0000001, caminho=tmp_path / "ledger.jsonl")
    orcamento.registrar(1.0)  # já estourou antes da primeira chamada

    from app.custo import TetoEstourado

    with pytest.raises(TetoEstourado):
        julgar("objeto", PERFIL, client=cliente, orcamento=orcamento)

    assert cliente.chamadas == []  # não chamou a API depois de estourado


def test_system_prompt_inclui_nao_fornece_e_e_cacheavel():
    prompt = system_prompt(PERFIL)
    assert prompt[0]["cache_control"] == {"type": "ephemeral"}
    for excluido in PERFIL["nao_fornece"]:
        assert excluido in prompt[0]["text"]

    # byte-estável entre chamadas — pré-condição pra cache hit
    assert system_prompt(PERFIL) == prompt
