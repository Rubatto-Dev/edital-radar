"""The claim layer 2 exists to make, checked against real corpus phrases.

These load the embedding model, so they are the slow tests in the suite.
They earn it: this is the project's central technical claim, and a silent
regression here (wrong model, wrong profile text) would show up only as
slightly worse eval numbers much later.
"""

import pytest

from app.embeddings import embedar, similaridade, texto_do_perfil
from app.perfil import carregar

PERFIL = carregar()

SRP_REMEDIO = "Aquisição de medicamentos, através do Sistema de Registro de Preços"
SOFTWARE_GESTAO = "Locação de sistema web integrado de gestão em nuvem para prefeitura"
EXAUSTAO = "Contratação de sistema de exaustão do laboratório de gastronomia"


@pytest.fixture(scope="module")
def vetores():
    textos = [texto_do_perfil(PERFIL), SRP_REMEDIO, SOFTWARE_GESTAO, EXAUSTAO]
    p, srp, sw, ex = embedar(textos)
    return {"perfil": p, "srp": srp, "software": sw, "exaustao": ex}


def test_separa_o_que_keyword_nao_separa(vetores):
    # Both phrases contain "sistema". A keyword filter cannot tell them
    # apart; this is the whole argument for the layer.
    assert "sistema" in SRP_REMEDIO.lower()
    assert "sistema" in SOFTWARE_GESTAO.lower()

    perto = similaridade(vetores["perfil"], vetores["software"])
    longe = similaridade(vetores["perfil"], vetores["srp"])
    assert perto > longe
    assert longe < 0.45  # below the funnel threshold: it gets dropped


def test_armadilha_do_sistema_fisico_fica_longe(vetores):
    assert similaridade(vetores["perfil"], vetores["exaustao"]) < 0.45


def test_perfil_nao_inclui_o_que_a_empresa_nao_fornece(vetores):
    # nao_fornece is left out on purpose: averaging hardware and staffing
    # vocabulary into the profile vector would pull it toward exactly what
    # we want it far from. Negation is layer 3's job.
    texto = texto_do_perfil(PERFIL)
    for excluido in PERFIL["nao_fornece"]:
        assert excluido not in texto


def test_vetor_tem_a_dimensao_do_schema(vetores):
    # sql/001_schema.sql declares vector(384). A mismatch here fails at
    # insert time with a confusing error, so it is checked where it is
    # readable.
    assert len(vetores["perfil"]) == 384
