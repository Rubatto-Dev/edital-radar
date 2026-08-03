"""The daily driver (python -m app.agente --de --ate): ingest, select
finalists, judge, log, notify — and the ok/parcial/falha discipline when
real things go wrong (PNCP down, spend cap hit, one bad candidate).

Every collaborator (ingerir, indexar, selecionar_finalistas,
julgar_com_agente, registrar_log, notificar_se_relevante) is monkeypatched,
same as tests/test_ingest.py does for buscar_pagina — this tests
executar_janela's own orchestration, not the pieces it calls.
"""

import app.agente as agente
from app.agente import DecisaoAgente, _para_edital, ja_julgados, selecionar_finalistas
from app.custo import TetoEstourado


def _decisao(classe, custo_usd=0.01):
    return DecisaoAgente(
        classe=classe,
        justificativa="justificativa",
        lote_misto=False,
        ferramentas_chamadas=[],
        anexo_baixado=False,
        custo_usd=custo_usd,
        latencia_s=1.0,
        modelo="claude-haiku-4-5",
    )


def _candidato(numero="a"):
    return {
        "numero_controle_pncp": numero,
        "objeto_compra": "objeto bruto",
        "objeto_limpo": "objeto limpo",
        "orgao_razao_social": "orgao",
        "uf_sigla": "SC",
        "valor_total_estimado": 100000.0,
        "data_encerramento_proposta": None,
    }


class TestParaEdital:
    def test_extrai_cnpj_ano_sequencial_do_numero_de_controle(self):
        candidato = _candidato("07753868000101-1-000003/2026")
        edital = _para_edital(candidato)
        assert edital["cnpj"] == "07753868000101"
        assert edital["ano_compra"] == 2026
        assert edital["sequencial_compra"] == 3

    def test_prefere_objeto_limpo_ao_objeto_bruto(self):
        edital = _para_edital(_candidato())
        assert edital["objeto"] == "objeto limpo"

    def test_numero_fora_do_formato_nao_quebra_so_fica_sem_localizadores(self):
        candidato = _candidato("numero-invalido")
        edital = _para_edital(candidato)
        assert "cnpj" not in edital


class ConexaoFalsa:
    """Mirrors tests/test_ingest.py's ConexaoFalsa for agente_execucao."""

    def __init__(self, registro):
        self.registro = registro

    def execute(self, sql, params=None):
        if "INSERT INTO agente_execucao" in sql:
            self.registro["id"] = 1
            self.registro["status"] = "falha"
            return _Linha((1,))
        if "UPDATE agente_execucao" in sql:
            status, avaliados, relevantes, erro, _id = params
            self.registro.update(
                status=status,
                avaliados=avaliados,
                relevantes=relevantes,
                erro=erro,
            )
            return _Linha(None)
        if "SELECT DISTINCT numero_controle_pncp" in sql:
            return _Linha(None)
        return _Linha(None)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Linha:
    def __init__(self, valor):
        self.valor = valor

    def fetchone(self):
        return self.valor


class TestJaJulgados:
    def test_devolve_o_conjunto_de_numeros_ja_julgados(self):
        class ConexaoComLinhas:
            def execute(self, sql, params=None):
                return self

            def fetchall(self):
                return [("a",), ("b",)]

        assert ja_julgados(ConexaoComLinhas()) == {"a", "b"}


class TestSelecionarFinalistas:
    def test_funil_mantem_so_quem_passa_o_limiar_e_nao_foi_julgado(self, monkeypatch):
        monkeypatch.setattr(agente, "candidatos", lambda conn, agora, perfil: [
            _candidato("a"), _candidato("b"), _candidato("ja-julgado"),
        ])
        monkeypatch.setattr(agente, "ja_julgados", lambda conn: {"ja-julgado"})
        monkeypatch.setattr(agente, "embedar", lambda textos: [[1.0]] * len(textos))

        def similaridade_falsa(v1, v2):
            return 0.9 if v2 == [1.0] else 0.0

        # a and b both score 0.9 (>= limiar); the point is ja-julgado never
        # reaches similaridade at all because it's filtered before embedding.
        monkeypatch.setattr(agente, "similaridade", similaridade_falsa)

        finalistas = selecionar_finalistas(
            conn=None, perfil={"descricao_curta": "x", "fornece": []}
        )

        assert {f["numero_controle_pncp"] for f in finalistas} == {"a", "b"}

    def test_sem_candidatos_nao_chama_o_modelo(self, monkeypatch):
        monkeypatch.setattr(agente, "candidatos", lambda conn, agora, perfil: [])
        monkeypatch.setattr(agente, "ja_julgados", lambda conn: set())
        chamado = []
        monkeypatch.setattr(agente, "embedar", lambda textos: chamado.append(textos) or [])

        perfil = {"descricao_curta": "x", "fornece": []}
        assert selecionar_finalistas(conn=None, perfil=perfil) == []
        assert chamado == []


class TestExecutarJanela:
    def _preparar_comuns(self, monkeypatch, execucoes):
        monkeypatch.setattr(agente.pool, "connection", lambda: ConexaoFalsa(execucoes))
        monkeypatch.setattr(agente, "ingerir", lambda de, ate: {"status": "ok", "erro": None})
        monkeypatch.setattr(agente, "indexar", lambda: None)

    def test_caminho_feliz_avalia_registra_e_notifica(self, monkeypatch):
        execucoes = {}
        self._preparar_comuns(monkeypatch, execucoes)
        monkeypatch.setattr(
            agente, "selecionar_finalistas", lambda conn, perfil: [_candidato("a"), _candidato("b")]
        )

        decisoes = iter([_decisao("relevante"), _decisao("nao_relevante")])
        monkeypatch.setattr(agente, "julgar_com_agente", lambda *a, **k: next(decisoes))
        monkeypatch.setattr(agente, "registrar_log", lambda conn, numero, decisao: 1)
        notificadas = []
        monkeypatch.setattr(
            agente,
            "notificar_se_relevante",
            lambda conn, id_, decisao: notificadas.append(decisao.classe),
        )

        resultado = agente.executar_janela("20260701", "20260702", perfil={})

        assert resultado["status"] == "ok"
        assert resultado["candidatos_avaliados"] == 2
        assert resultado["decisoes_relevantes"] == 1
        assert resultado["falhas_finalista"] == 0
        assert notificadas == ["relevante", "nao_relevante"]
        assert execucoes["status"] == "ok"  # chegou no banco, não só no retorno

    def test_ingestao_falha_marca_falha_sem_avaliar_nada(self, monkeypatch):
        execucoes = {}
        monkeypatch.setattr(agente.pool, "connection", lambda: ConexaoFalsa(execucoes))
        monkeypatch.setattr(
            agente, "ingerir", lambda de, ate: {"status": "falha", "erro": "PNCP fora do ar"}
        )
        monkeypatch.setattr(agente, "indexar", lambda: None)

        resultado = agente.executar_janela("20260701", "20260702", perfil={})

        assert resultado["status"] == "falha"
        assert resultado["candidatos_avaliados"] == 0
        assert "PNCP fora do ar" in resultado["erro"]

    def test_teto_estourado_para_o_loop_e_marca_parcial(self, monkeypatch):
        execucoes = {}
        self._preparar_comuns(monkeypatch, execucoes)
        monkeypatch.setattr(
            agente,
            "selecionar_finalistas",
            lambda conn, perfil: [_candidato("a"), _candidato("b")],
        )

        chamadas = {"n": 0}

        def julgar_falso(*a, **k):
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                return _decisao("relevante")
            raise TetoEstourado("teto diario atingido")

        monkeypatch.setattr(agente, "julgar_com_agente", julgar_falso)
        monkeypatch.setattr(agente, "registrar_log", lambda conn, numero, decisao: 1)
        monkeypatch.setattr(agente, "notificar_se_relevante", lambda conn, id_, decisao: None)

        resultado = agente.executar_janela("20260701", "20260702", perfil={})

        assert resultado["status"] == "parcial"
        assert resultado["candidatos_avaliados"] == 1
        assert "teto" in resultado["erro"].lower()

    def test_falha_num_candidato_nao_aborta_os_outros(self, monkeypatch):
        execucoes = {}
        self._preparar_comuns(monkeypatch, execucoes)
        monkeypatch.setattr(
            agente,
            "selecionar_finalistas",
            lambda conn, perfil: [_candidato("com-erro"), _candidato("ok")],
        )

        def julgar_falso(edital, *a, **k):
            if edital["numero_controle_pncp"] == "com-erro":
                raise ValueError("resposta malformada")
            return _decisao("relevante")

        monkeypatch.setattr(agente, "julgar_com_agente", julgar_falso)
        monkeypatch.setattr(agente, "registrar_log", lambda conn, numero, decisao: 1)
        monkeypatch.setattr(agente, "notificar_se_relevante", lambda conn, id_, decisao: None)

        resultado = agente.executar_janela("20260701", "20260702", perfil={})

        assert resultado["status"] == "ok"
        assert resultado["candidatos_avaliados"] == 1
        assert resultado["falhas_finalista"] == 1

    def test_limite_finalistas_corta_a_lista(self, monkeypatch):
        execucoes = {}
        self._preparar_comuns(monkeypatch, execucoes)
        monkeypatch.setattr(
            agente,
            "selecionar_finalistas",
            lambda conn, perfil: [_candidato("a"), _candidato("b"), _candidato("c")],
        )
        monkeypatch.setattr(agente, "julgar_com_agente", lambda *a, **k: _decisao("relevante"))
        monkeypatch.setattr(agente, "registrar_log", lambda conn, numero, decisao: 1)
        monkeypatch.setattr(agente, "notificar_se_relevante", lambda conn, id_, decisao: None)

        resultado = agente.executar_janela(
            "20260701", "20260702", perfil={}, limite_finalistas=1
        )

        assert resultado["candidatos_avaliados"] == 1


class TestMain:
    def test_cli_passa_os_argumentos_pra_executar_janela(self, monkeypatch, capsys):
        chamada = {}

        def executar_falso(de, ate, *, modelo, teto_usd, limite_finalistas):
            chamada.update(
                de=de, ate=ate, modelo=modelo, teto_usd=teto_usd,
                limite_finalistas=limite_finalistas,
            )
            return {"status": "ok", "candidatos_avaliados": 0}

        monkeypatch.setattr(agente, "executar_janela", executar_falso)
        monkeypatch.setattr(agente.pool, "open", lambda: None)
        monkeypatch.setattr(agente.pool, "close", lambda: None)
        monkeypatch.setattr(
            "sys.argv",
            ["agente.py", "--de", "20260701", "--ate", "20260702", "--teto-usd", "0.10"],
        )

        try:
            agente.main()
        except SystemExit as e:
            assert e.code == 0

        assert chamada["de"] == "20260701"
        assert chamada["ate"] == "20260702"
        assert chamada["teto_usd"] == 0.10
        assert chamada["limite_finalistas"] is None

    def test_status_diferente_de_ok_sai_com_codigo_nao_zero(self, monkeypatch):
        monkeypatch.setattr(
            agente, "executar_janela", lambda *a, **k: {"status": "falha", "erro": "x"}
        )
        monkeypatch.setattr(agente.pool, "open", lambda: None)
        monkeypatch.setattr(agente.pool, "close", lambda: None)
        monkeypatch.setattr("sys.argv", ["agente.py", "--de", "20260701", "--ate", "20260702"])

        try:
            agente.main()
            raise AssertionError("main() deveria levantar SystemExit")
        except SystemExit as e:
            assert e.code == 1
