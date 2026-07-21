"""Layer 1 must not lose a relevant tender.

These run the real filter against the real evaluation set — not against
invented rows — because the point of the layer is what it does to *those*
cases.
"""

import yaml

from app.filtros import (
    ABAIXO_DO_PISO,
    FORA_REGIAO,
    VALOR_DESCONHECIDO,
    ressalvas,
)
from app.perfil import CAMINHO_PADRAO, carregar

CASOS = yaml.safe_load(open(CAMINHO_PADRAO.parent / "eval-set.yaml", encoding="utf-8"))["casos"]
RELEVANTES = [c for c in CASOS if c["rotulo"] == "relevante"]
PERFIL = carregar()


class TestRessalvas:
    def test_valor_desconhecido_nao_e_tratado_como_pequeno(self):
        # pos-03: value came as 0.0 from the PNCP (confidential). Marked
        # unknown, never "below the floor" — those are different facts.
        marcas = ressalvas("SC", None, PERFIL)
        assert VALOR_DESCONHECIDO in marcas
        assert ABAIXO_DO_PISO not in marcas

    def test_fora_da_regiao_e_ressalva(self):
        assert FORA_REGIAO in ressalvas("ES", 124412.16, PERFIL)

    def test_dentro_da_regiao_e_da_faixa_nao_gera_ressalva(self):
        assert ressalvas("SC", 242133.87, PERFIL) == []

    def test_abaixo_do_piso(self):
        assert ABAIXO_DO_PISO in ressalvas("MS", 27780.00, PERFIL)

    def test_valor_em_unidade_errada_nao_some(self):
        # neg-18 arrived with 3.60 — a unit price. It is flagged as small,
        # not dropped: the layer that can read the object decides.
        assert ABAIXO_DO_PISO in ressalvas("SC", 3.60, PERFIL)


class TestTetoDeRecall:
    """The measurement that redesigned this layer."""

    def test_nenhum_relevante_e_descartado_pelas_regras_estruturais(self):
        # Ressalvas are caveats, never exclusions. If this ever starts
        # dropping cases, recall is being spent at the cheapest layer where
        # no later stage can recover it.
        for caso in RELEVANTES:
            marcas = ressalvas(caso.get("uf"), caso.get("valor"), PERFIL)
            assert isinstance(marcas, list)  # survives, whatever the caveats

    def test_a_maioria_dos_relevantes_estaria_perdida_com_filtro_duro(self):
        # Documents why the design changed. If someone reintroduces a hard
        # state filter, this test states the cost in numbers.
        ufs = set(PERFIL["restricoes_operacionais"]["ufs_atendidas"])
        fora = [c for c in RELEVANTES if c.get("uf") not in ufs]
        assert len(fora) == 8
        teto = (len(RELEVANTES) - len(fora)) / len(RELEVANTES)
        assert teto < 0.85  # below target before a single line of matching runs

    def test_ressalva_de_regiao_marca_exatamente_os_de_fora(self):
        ufs = set(PERFIL["restricoes_operacionais"]["ufs_atendidas"])
        for caso in RELEVANTES:
            marcas = ressalvas(caso.get("uf"), caso.get("valor"), PERFIL)
            esperado = caso.get("uf") not in ufs
            assert (FORA_REGIAO in marcas) is esperado, caso["id"]
