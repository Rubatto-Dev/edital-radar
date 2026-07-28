"""The daily spend cap must actually cap — and only on today's spend."""

import datetime
import json

import pytest

from app.custo import Orcamento, TetoEstourado


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "custo-ledger.jsonl"


def test_verificar_passa_com_ledger_vazio(ledger):
    Orcamento(teto_usd=1.0, caminho=ledger).verificar()


def test_registrar_acumula_e_verificar_estoura_no_teto(ledger):
    orcamento = Orcamento(teto_usd=0.10, caminho=ledger)

    orcamento.registrar(0.06, latencia_s=0.5)
    orcamento.verificar()  # 0.06 < 0.10, ainda passa

    orcamento.registrar(0.05, latencia_s=0.4)
    with pytest.raises(TetoEstourado):
        orcamento.verificar()  # 0.11 >= 0.10


def test_gasto_de_outro_dia_nao_conta(ledger):
    ontem = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"data": ontem, "custo_usd": 999.0, "latencia_s": 1.0}) + "\n",
        encoding="utf-8",
    )

    Orcamento(teto_usd=1.0, caminho=ledger).verificar()  # não estoura


def test_registrar_grava_uma_linha_jsonl_por_chamada(ledger):
    orcamento = Orcamento(teto_usd=1.0, caminho=ledger)
    orcamento.registrar(0.001, latencia_s=0.2)
    orcamento.registrar(0.002, latencia_s=0.3)

    linhas = ledger.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 2
    primeira = json.loads(linhas[0])
    assert primeira["custo_usd"] == 0.001
    assert primeira["data"] == datetime.date.today().isoformat()
