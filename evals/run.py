"""Scores a classifier against the evaluation set.

Run:
    python -m evals.run                 # all classifiers
    python -m evals.run --classificador keyword
    python -m evals.run --json          # machine-readable, for the Phase 3 dashboard

The scoring is not plain binary accuracy, because the label set has three
values and they do not carry equal weight:

  - Missing a relevant tender costs a contract.
  - A false alarm costs seconds.
  - Abstaining on a relevant tender is a *third* thing: the system declined
    to decide and will fetch the document. That is not the same failure as
    confidently saying no, and averaging them together hides the difference
    the `indeterminado` class was created to expose.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent
CASOS = RAIZ / "eval-set.yaml"
PERFIL = RAIZ / "perfil-empresa.yaml"

METAS = {"recall": 0.85, "precisao": 0.60}

# Printed on every run, because these numbers are quotable and two of them
# are easy to quote wrongly.
RESSALVAS_DE_METODO = [
    "A precisao daqui NAO transfere pra producao. A taxa-base de relevantes "
    "no eval set e ~38%; no corpus real e ~0,5%. Precisao depende da "
    "taxa-base, entao o mesmo classificador tem precisao muito menor solto "
    "no PNCP. Comparar classificadores entre si aqui e valido; citar o "
    "numero como 'precisao do produto' nao e.",

    "Os positivos vieram de busca por palavra-chave sobre objetoCompra. Um "
    "edital relevante sem esses termos e invisivel pro set — e por isso que "
    "o baseline keyword marca recall 1,0. O recall real e desconhecido e "
    "provavelmente menor.",

    "Corrigir ambos na Fase 3: amostra aleatoria do corpus, rotulada as "
    "cegas, pra estimar taxa-base e recall sem o vies da coleta.",

    "O limiar do classificador vetorial (0.45) foi escolhido olhando ESTE "
    "set. Isso e ajuste no proprio conjunto de avaliacao: o numero dele e "
    "otimista, e num set novo tende a ser pior. Com 34 casos nao da pra "
    "separar treino de teste sem inutilizar os dois — assumido, nao "
    "escondido.",
]


def carregar():
    casos = yaml.safe_load(CASOS.read_text(encoding="utf-8"))["casos"]
    perfil = yaml.safe_load(PERFIL.read_text(encoding="utf-8"))
    return casos, perfil


def avaliar(classificador, casos, perfil):
    relevantes = [c for c in casos if c["rotulo"] == "relevante"]
    indeterminados = [c for c in casos if c["rotulo"] == "indeterminado"]

    acertos = 0          # labeled relevante, predicted relevante
    perdidos = []        # labeled relevante, predicted nao_relevante — the expensive error
    abstencoes = []      # labeled relevante, predicted indeterminado — declined, not wrong
    falsos_positivos = []
    chutes = []          # labeled indeterminado, answered with a confident yes/no
    abstencao_correta = 0

    previstos_relevante = 0

    for caso in casos:
        previsto = classificador(caso, perfil)
        rotulo = caso["rotulo"]

        if previsto == "relevante":
            previstos_relevante += 1

        if rotulo == "relevante":
            if previsto == "relevante":
                acertos += 1
            elif previsto == "indeterminado":
                abstencoes.append(caso["id"])
            else:
                perdidos.append(caso["id"])
        elif rotulo == "nao_relevante":
            if previsto == "relevante":
                falsos_positivos.append(caso["id"])
        else:  # indeterminado
            if previsto == "indeterminado":
                abstencao_correta += 1
            else:
                chutes.append(caso["id"])

    recall = acertos / len(relevantes) if relevantes else 0.0
    precisao = acertos / previstos_relevante if previstos_relevante else 0.0

    return {
        "recall": round(recall, 3),
        "precisao": round(precisao, 3),
        "acertos": acertos,
        "relevantes_no_set": len(relevantes),
        "perdidos": perdidos,
        "abstencoes_em_relevantes": abstencoes,
        "falsos_positivos": falsos_positivos,
        "indeterminados_no_set": len(indeterminados),
        "abstencao_correta": abstencao_correta,
        "chutes_em_indeterminado": chutes,
        "passa": recall >= METAS["recall"] and precisao >= METAS["precisao"],
    }


def imprimir(nome, r):
    marca = "PASSA" if r["passa"] else "falha"
    print(f"\n{nome}  [{marca}]")
    print(f"  recall    {r['recall']:.3f}   (meta {METAS['recall']})  "
          f"{r['acertos']}/{r['relevantes_no_set']} relevantes encontrados")
    print(f"  precisao  {r['precisao']:.3f}   (meta {METAS['precisao']})")

    if r["perdidos"]:
        print(f"  PERDIDOS ({len(r['perdidos'])}) — o erro caro: {', '.join(r['perdidos'])}")
    if r["abstencoes_em_relevantes"]:
        print(f"  abstencoes em relevantes ({len(r['abstencoes_em_relevantes'])}): "
              f"{', '.join(r['abstencoes_em_relevantes'])}")
    if r["falsos_positivos"]:
        print(f"  falsos positivos ({len(r['falsos_positivos'])}): "
              f"{', '.join(r['falsos_positivos'][:8])}"
              f"{' ...' if len(r['falsos_positivos']) > 8 else ''}")
    if r["chutes_em_indeterminado"]:
        print(f"  chutou onde devia se abster ({len(r['chutes_em_indeterminado'])}): "
              f"{', '.join(r['chutes_em_indeterminado'])}")


def main():
    from evals import classificadores

    disponiveis = {
        "alerta_tudo": classificadores.alerta_tudo,
        "keyword": classificadores.keyword,
        "vetorial": classificadores.vetorial,
    }

    p = argparse.ArgumentParser()
    p.add_argument("--classificador", choices=list(disponiveis), action="append")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    casos, perfil = carregar()
    escolhidos = args.classificador or list(disponiveis)
    resultados = {n: avaliar(disponiveis[n], casos, perfil) for n in escolhidos}

    if args.json:
        print(json.dumps(
            {"casos": len(casos), "resultados": resultados, "ressalvas": RESSALVAS_DE_METODO},
            indent=2, ensure_ascii=False))
    else:
        print(f"eval-set: {len(casos)} casos")
        for nome, r in resultados.items():
            imprimir(nome, r)
        print("\n  --- ressalvas de método (leia antes de citar qualquer número) ---")
        for linha in RESSALVAS_DE_METODO:
            print(f"  * {linha}")
        print()

    # Non-zero while nothing passes, so this can gate CI in Phase 3.
    return 0 if any(r["passa"] for r in resultados.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
