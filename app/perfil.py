"""Loads the company profile that defines what 'relevant' means.

The profile is the contract of the evaluation set: changing it invalidates
labels. It is data, not code, so that the rule can be inspected and diffed
without reading Python.
"""

import functools
from pathlib import Path

import yaml

CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "evals" / "perfil-empresa.yaml"


@functools.lru_cache(maxsize=4)
def carregar(caminho: Path | str = CAMINHO_PADRAO) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def restricoes(perfil: dict | None = None) -> dict:
    return (perfil or carregar())["restricoes_operacionais"]
