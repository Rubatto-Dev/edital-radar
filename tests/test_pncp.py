import httpx
import pytest

from app.db import normalizar_valor
from app.pncp import PncpIndisponivel, buscar_pagina, limpar_objeto


class TestLimparObjeto:
    def test_remove_prefixo_de_plataforma(self):
        assert (
            limpar_objeto("[LICITANET] - CONTRATAÇÃO DE SISTEMA DE GESTÃO")
            == "CONTRATAÇÃO DE SISTEMA DE GESTÃO"
        )
        assert (
            limpar_objeto("[Portal de Compras Públicas] - Locação de software")
            == "Locação de software"
        )

    def test_preserva_colchete_que_nao_e_prefixo(self):
        # Only a leading bracket followed by a dash is platform metadata.
        texto = "Aquisição de sistema [módulo A] e treinamento"
        assert limpar_objeto(texto) == texto

    def test_normaliza_espacos_e_quebras(self):
        assert limpar_objeto("  Sistema   de\n gestão  ") == "Sistema de gestão"

    def test_vazio(self):
        assert limpar_objeto("") == ""
        assert limpar_objeto(None) == ""


class TestNormalizarValor:
    def test_zero_vira_none_porque_significa_desconhecido(self):
        # The PNCP never returns null; 0.0 is confidential or unreported.
        # Keeping it as 0 makes "unknown" and "free" the same number.
        assert normalizar_valor(0.0) is None
        assert normalizar_valor(0) is None

    def test_none_continua_none(self):
        assert normalizar_valor(None) is None

    def test_valor_real_passa(self):
        assert normalizar_valor(313134.0) == 313134.0

    def test_valor_pequeno_passa_mesmo_sendo_suspeito(self):
        # 3.60 is a real observed value: a unit price, not a total. Deciding
        # it is bogus is not this function's call — dropping it here would
        # hide the problem from the filter that has to reason about it.
        assert normalizar_valor(3.60) == 3.60


def _cliente(respostas):
    """Client that replays a fixed sequence of responses."""
    it = iter(respostas)

    def handler(request):
        resultado = next(it)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestBuscarPagina:
    def test_retorna_registros(self):
        cliente = _cliente([httpx.Response(200, json={"data": [{"a": 1}]})])
        assert buscar_pagina("20260701", "20260715", 1, cliente=cliente) == [{"a": 1}]

    def test_204_significa_fim_das_paginas(self):
        cliente = _cliente([httpx.Response(204)])
        assert buscar_pagina("20260701", "20260715", 9, cliente=cliente) == []

    def test_repete_apos_429_e_sucede(self):
        cliente = _cliente(
            [
                httpx.Response(429),
                httpx.Response(429),
                httpx.Response(200, json={"data": [{"a": 1}]}),
            ]
        )
        dormidas = []
        registros = buscar_pagina(
            "20260701", "20260715", 1, cliente=cliente, dormir=dormidas.append
        )
        assert registros == [{"a": 1}]
        assert dormidas == [1, 2]  # exponential

    def test_repete_apos_500_do_banco_do_pncp(self):
        cliente = _cliente(
            [httpx.Response(500), httpx.Response(200, json={"data": []})]
        )
        assert buscar_pagina(
            "20260701", "20260715", 1, cliente=cliente, dormir=lambda _: None
        ) == []

    def test_indisponibilidade_levanta_em_vez_de_devolver_vazio(self):
        # The whole point. If this returned [], the caller would record a
        # successful run with zero tenders and the user would read that as
        # "nothing relevant today" while the API was simply down.
        cliente = _cliente([httpx.Response(503)] * 6)
        with pytest.raises(PncpIndisponivel) as excinfo:
            buscar_pagina(
                "20260701", "20260715", 1, cliente=cliente, dormir=lambda _: None
            )
        assert "503" in str(excinfo.value)

    def test_timeout_tambem_levanta(self):
        cliente = _cliente([httpx.TimeoutException("timeout")] * 6)
        with pytest.raises(PncpIndisponivel):
            buscar_pagina(
                "20260701", "20260715", 1, cliente=cliente, dormir=lambda _: None
            )
