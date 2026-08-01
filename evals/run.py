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
import collections
import json
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent
CASOS = RAIZ / "eval-set.yaml"
PERFIL = RAIZ / "perfil-empresa.yaml"
BASELINE = RAIZ / "baseline.json"

METAS = {"recall": 0.85, "precisao": 0.60}

# Only these run by default. Everything else hits the real, paid API — they
# require an explicit `--classificador`, never by omission. Module scope, not
# a local in main(), so CI and the test suite can assert against the same list
# the runner actually uses: this is what stops a distracted commit from making
# every push spend money.
GRATUITOS = ["alerta_tudo", "keyword", "vetorial"]

# Printed on every run, because these numbers are quotable and two of them
# are easy to quote wrongly.
RESSALVAS_DE_METODO = [
    ("A precisao daqui NAO transfere pra producao. A taxa-base de relevantes "
        "no eval set e ~38%; no corpus real e ~0,5%. Precisao depende da "
        "taxa-base, entao o mesmo classificador tem precisao muito menor solto "
        "no PNCP. Comparar classificadores entre si aqui e valido; citar o "
        "numero como 'precisao do produto' nao e."),

    ("Os positivos vieram de busca por palavra-chave sobre objetoCompra. Um "
        "edital relevante sem esses termos e invisivel pro set — e por isso que "
        "o baseline keyword marca recall 1,0. O recall real e desconhecido e "
        "provavelmente menor."),

    ("Corrigir ambos na Fase 3: amostra aleatoria do corpus, rotulada as "
        "cegas, pra estimar taxa-base e recall sem o vies da coleta."),

    ("O limiar do classificador vetorial (0.45) foi escolhido olhando ESTE "
        "set. Isso e ajuste no proprio conjunto de avaliacao: o numero dele e "
        "otimista, e num set novo tende a ser pior. Com 34 casos nao da pra "
        "separar treino de teste sem inutilizar os dois — assumido, nao "
        "escondido."),
]


def carregar():
    casos = yaml.safe_load(CASOS.read_text(encoding="utf-8"))["casos"]
    perfil = yaml.safe_load(PERFIL.read_text(encoding="utf-8"))
    return casos, perfil


def carregar_baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def folga(baseline, deterministico):
    """How far a metric may fall before it counts as a regression.

    Zero for the deterministic classifiers: same input, same output, so any
    drop is a real change in behaviour and should stop the build.

    The layer-3 classifiers call an LLM and do not return the same answer every
    time — `cascata` was measured between 0.900 and 1.000 precision across runs
    on an unchanged pipeline. Demanding an exact number from them would fail
    the build on noise. One case worth of slack is the smallest honest unit
    here: with 13 relevant cases, recall itself only moves in steps of 1/13,
    so anything finer is not a distinction this eval set can make.
    """
    if deterministico:
        return 0.0
    return 1 / baseline["eval_set"]["relevantes"]


def comparar_com_baseline(nome, resultado, baseline):
    """Regressions for one classifier, as a list of human-readable lines."""
    esperados = baseline["classificadores"].get(nome)
    if esperados is None:
        # Not an oversight to wave through: a classifier with no recorded
        # baseline has never been measured, so there is nothing to protect and
        # nothing to trust. Recording it is a deliberate act.
        return [f"sem baseline registrado — medir e adicionar a {BASELINE.name}"]

    margem = folga(baseline, esperados["deterministico"])
    quedas = []
    for metrica in ("recall", "precisao"):
        queda = esperados[metrica] - resultado[metrica]
        if queda > margem + 1e-9:
            quedas.append(
                f"{metrica} {resultado[metrica]:.3f} < baseline {esperados[metrica]:.3f}"
                f" (queda de {queda:.3f}, folga {margem:.3f})"
            )
    return quedas


def tipo_de_resultado(rotulo: str, previsto: str) -> str | None:
    """Names one case's outcome. Split out of `avaliar` deliberately: this is
    the taxonomy — which mistakes are expensive, which are an honest decline —
    and the loop that consumes it is only arithmetic. `None` means the case is
    a true negative, which nothing needs to count.

    - acerto: labeled relevante, called relevante
    - perdido: labeled relevante, called nao_relevante — the expensive error
    - abstencao: labeled relevante, called indeterminado — declined, not wrong
    - falso_positivo: labeled nao_relevante, called relevante
    - abstencao_correta / chute: labeled indeterminado, declined or answered
    """
    if rotulo == "relevante":
        if previsto == "relevante":
            return "acerto"
        return "abstencao" if previsto == "indeterminado" else "perdido"
    if rotulo == "nao_relevante":
        return "falso_positivo" if previsto == "relevante" else None
    return "abstencao_correta" if previsto == "indeterminado" else "chute"


def avaliar(classificador, casos, perfil):
    relevantes = [c for c in casos if c["rotulo"] == "relevante"]
    indeterminados = [c for c in casos if c["rotulo"] == "indeterminado"]

    quantos = collections.Counter()
    quais = collections.defaultdict(list)
    previstos_relevante = 0

    for caso in casos:
        previsto = classificador(caso, perfil)
        if previsto == "relevante":
            previstos_relevante += 1

        tipo = tipo_de_resultado(caso["rotulo"], previsto)
        if tipo:
            quantos[tipo] += 1
            quais[tipo].append(caso["id"])

    acertos = quantos["acerto"]
    recall = acertos / len(relevantes) if relevantes else 0.0
    precisao = acertos / previstos_relevante if previstos_relevante else 0.0

    return {
        "recall": round(recall, 3),
        "precisao": round(precisao, 3),
        "acertos": acertos,
        "relevantes_no_set": len(relevantes),
        "perdidos": quais["perdido"],
        "abstencoes_em_relevantes": quais["abstencao"],
        "falsos_positivos": quais["falso_positivo"],
        "indeterminados_no_set": len(indeterminados),
        "abstencao_correta": quantos["abstencao_correta"],
        "chutes_em_indeterminado": quais["chute"],
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
        "llm": classificadores.llm,
        "cascata": classificadores.cascata,
        "llm_sonnet": classificadores.llm_sonnet,
        "cascata_sonnet": classificadores.cascata_sonnet,
    }
    p = argparse.ArgumentParser()
    p.add_argument("--classificador", choices=list(disponiveis), action="append")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--check-baseline",
        action="store_true",
        help="falha se algum classificador piorou em relacao a evals/baseline.json",
    )
    args = p.parse_args()

    casos, perfil = carregar()
    escolhidos = args.classificador or GRATUITOS
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

    if not args.check_baseline:
        # Reporting is not a pass/fail question. This used to return non-zero
        # unless some classifier cleared METAS — which is every run today, so
        # wiring it into CI would open the pipeline permanently red. Worse, it
        # used `any()`: one baseline scraping past the target would have gone
        # green while the production cascade was failing. The gate is now
        # explicit, per classifier, and measured against what was actually
        # achieved rather than what is still wanted.
        return 0

    baseline = carregar_baseline()
    # Messages go to stderr so `--json --check-baseline` keeps stdout parseable.
    regressoes = {
        nome: quedas
        for nome, r in resultados.items()
        if (quedas := comparar_com_baseline(nome, r, baseline))
    }

    if not regressoes:
        print(
            f"baseline de {baseline['medido_em']}: "
            f"{len(resultados)} classificador(es), nenhuma regressao",
            file=sys.stderr,
        )
        return 0

    print(f"REGRESSAO contra o baseline de {baseline['medido_em']}:", file=sys.stderr)
    for nome, quedas in regressoes.items():
        for queda in quedas:
            print(f"  {nome}: {queda}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
