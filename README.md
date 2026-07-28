# edital-radar

Public procurement monitoring for Brazilian software vendors. Tracks daily
tenders published to the national procurement portal (PNCP), matches them
against a company profile, and alerts on relevant opportunities — with the
citation that justifies the match.

**Status:** Phase 1 — ingestion working against the live API; all three
cascade layers are implemented. The evaluation set was built before the
system, deliberately.

## The problem

Brazilian public bodies publish ~1,100 electronic tenders per day to the
[PNCP](https://pncp.gov.br). A vendor that can serve maybe 3 of them has no
practical way to find those 3. Companies lose winnable contracts because
nobody read the right notice before the deadline.

Existing tools rely on keyword alerts. In this domain, keyword matching fails
badly — see below.

## Why keyword matching fails here

`Sistema de Registro de Preços` ("Price Registry System") is a **statutory
procurement modality**, not software. It appears in a large share of all
tenders — pharmaceuticals, reinforced concrete, school meals. Any keyword
alert on `sistema` drowns the user in noise.

Real examples from the corpus that a keyword filter marks as software:

| Object (abbreviated) | Actually is |
|---|---|
| "Aquisição de medicamentos, através do **Sistema** de Registro de Preços" | Pharmaceuticals |
| "**Sistema** de Proteção Contra Quedas" | Fall-arrest safety equipment |
| "agenciamento de **hospedagem** (reserva, marcação)" | Hotel booking |
| "**sistema** de exaustão do laboratório de gastronomia" | Kitchen extractor hood |

This is the empirical case for semantic retrieval over regex — and the reason
the evaluation set is built around hard negatives rather than obvious ones.

## Architecture

Cost is a hard constraint (self-funded). The filter cascade is a requirement,
not an optimization:

```
1. SQL: drop expired tenders, attach caveats              free
2. Vector search: objeto × company profile (local model)  free — cuts the volume
3. LLM relevance judgment — finalists only                cents/day
4. Document download + parsing — approved only            rare
```

Key insight from exploration: PNCP metadata includes `objetoCompra`, a
free-text description of what is being purchased. **Triage runs entirely on
metadata; documents are fetched only for finalists.** This removes document
processing from the critical path.

### Layer 1 filters less than it was designed to, on purpose

The original plan had layer 1 cut on state and value, discarding ~95% for
free. Measuring that against the evaluation set killed it: **8 of 13 relevant
tenders sit outside the served states** and 2 below the viability floor, so
hard-filtering both capped recall at **0.385** against a 0.85 target — before
any matching ran, at the one layer where nothing downstream can recover the
loss.

So layer 1 splits facts by kind rather than by field:

| Kind | Example | Effect |
|---|---|---|
| Hard | The proposal deadline has passed | Dropped — objectively unusable |
| Soft | Wrong state, below the floor, unknown value | Kept, with a caveat attached |

A tender in a neighbouring state is inconvenient, not impossible — implementing
SaaS remotely is ordinary. The user decides whether it is worth bidding; the
filter does not get to decide that silently. Same reasoning as the `lote_misto`
flag.

The cost model survives because the volume reduction simply moves to layer 2,
which is also free. Only layer 3 costs money, and layer 2 still runs before it.
On a live slice, layer 1 keeps 98 of 100 tenders and labels 77 of them with at
least one caveat.

### Layer 3: the only step that costs money

Layer 2 is a funnel, not a decision boundary — it still lets through tenders
that share vocabulary with the profile without matching it, including the
`Sistema de Registro de Preços` trap layer 2 doesn't fully close (see above).
Layer 3 judges only those finalists with Claude Haiku 4.5, using structured
output (`output_config.format`, a JSON schema) so the three-class contract —
`relevante` / `nao_relevante` / `indeterminado` — is guaranteed by the API,
not by parsing a string. `nao_fornece` is part of the system prompt here,
unlike layer 2's profile vector (`app/embeddings.py:texto_do_perfil`): an LLM
can reason about negation, an averaged vector can't. The system prompt is
built deterministically from the profile so its bytes are stable across
calls — a precondition for `cache_control` to ever hit, though at this
profile's current size the prompt likely sits below Haiku 4.5's ~4096-token
minimum cacheable prefix, so the cache may not actually engage yet; that's
measured, not assumed.

A hard daily spend cap (`app/custo.py`) gates every call: exceeded → the
caller stops and is notified, rather than silently spending past what a
self-funded intern can absorb. Every attempt — cost and latency — is written
to an append-only ledger, which doubles as the record Phase 3 (observability)
will consume, so the eval loop stays decoupled from Postgres.

### Stack

- FastAPI + PostgreSQL/pgvector + Docker
- `sentence-transformers` for embeddings (local, zero marginal cost)
- Claude Haiku 4.5 for relevance judgment and summarization
- Langfuse (self-hosted) for tracing and evaluation

## Corpus notes

Findings from hands-on exploration of the live API — see
[`docs/corpus-notes.md`](docs/corpus-notes.md) for detail:

- **The API is open** — no authentication. `tamanhoPagina` minimum is 10.
- **Rate limited** at roughly 25 rapid requests; exponential backoff required.
- **`valorTotalEstimado` is often `0.0`** (confidential or unreported), not
  `null`. A naive value filter silently discards these.
- **Attachments are not PDFs.** Served as `application/octet-stream`; the first
  sampled document was `.docx`. Type detection must use magic bytes.
- **`tipoDocumentoNome` is unreliable.** A document typed "Edital" contained
  152 words — it was a notice pointing to the municipality's own website. The
  full tender document is frequently absent from PNCP.

The last point sets v1 scope: **stop at the notice.** Following links into
hundreds of heterogeneous municipal websites is unbounded work. The alert
carries object, value, deadline and link — enough to act on. Full-document
retrieval becomes a measured improvement, not a requirement.

## Evaluation

The evaluation set (`evals/eval-set.yaml`) was written **before** any pipeline
code, using real tenders drawn from a 10,000-record sample. 34 labeled cases —
13 relevant, 18 not, 3 undecidable — of which 24 are marked hard.

It is weighted toward hard negatives: cases carrying IT vocabulary whose correct
label contradicts keyword intuition. A set of obvious cases would pass any naive
system and measure nothing.

Several positives are there not because they are hard to classify, but because
each one breaks a cheap filter if that filter is naive — a tender outside the
served states, one below the viability floor, one whose value is confidential,
one published twice under different keys.

Target metrics:

| Metric | Target | Rationale |
|---|---|---|
| Recall (relevant) | ≥ 0.85 | A missed tender is a lost contract |
| Precision | ≥ 0.60 | A false alarm costs seconds to dismiss |

Recall is weighted above precision deliberately: the asymmetry of cost in this
domain is severe.

### Current numbers

`python -m evals.run` scores any classifier against the set. Two baselines ship
with it, so every later number has something to be a delta from:

| Classifier | Recall | Precision | Passes |
|---|---|---|---|
| Alert on everything | 1.000 | 0.382 | no |
| Keyword match (the incumbent) | 1.000 | 0.448 | no |
| Vector similarity (layer 2) | 1.000 | 0.542 | no |
| **Cascade, layer 3 = Haiku 4.5** (`cascata`) | 0.692 | 0.900–1.000 | no |
| **Cascade, layer 3 = Sonnet 5** (`cascata_sonnet`) | 0.846 | 1.000 | no — 1 case short of 0.85 |

Measured 2026-07-27 against the live API, 24 layer-3 calls per run
(~$0.03–$0.06). None passes, which is the point: the floor is established
before the interesting work starts. If a baseline ever passed, the target
would be too easy or the set too kind.

Layer 3 flips the failure mode from layer 2's: precision jumps to
0.90–1.00 (the LLM correctly clears the false positives layer 2's funnel
still let through), but recall drops below the baselines' 1.000 — the LLM
is more conservative than "alert on everything" on genuinely ambiguous
cases (a fiscal-audit software module, an environmental-management SaaS, a
mixed lot bundling call-center infra with health software). **Recall,
weighted higher in this domain, is worse on both models than the naive
baselines.** Swapping Haiku for Sonnet 5 — same prompt, same schema, only
the model ID changed — recovers 2 of the 4 misses (recall 0.692 → 0.846),
which is the A/B the roadmap called for if Haiku underperforms.

Neither model was pushed further: with only 13 relevant cases in the set,
recall moves in steps of 1/13 ≈ 0.077, so clearing 0.85 requires 12/13
exactly — and re-running or re-prompting until one more case flips is
tuning against the same 34 cases the layer-2 threshold was already tuned
on, a bias the runner's own method caveats call out below. The honest fix
is Phase 3's blind random sample against the live corpus, not another pass
over this set.

Layer 2 is tuned as a **funnel, not a decision boundary**. At the threshold in
use it keeps every relevant case in the set while discarding **69% of the live
corpus** — medicine scores 0.10 against the profile, infant formula 0.036.

It does not solve the headline trap on its own. Three of the five
highest-scoring live tenders still contain "Sistema de Registro de Preços":
embeddings kill the easy keyword failures and leave the hard ones, which is
precisely the work left for the LLM.

### Two numbers that look contradictory and are not

A keyword pass over 1,200 raw corpus records returned 7 candidates, 1 of them
a true positive — roughly **14% precision**. The same keyword classifier scores
**0.448** on the evaluation set. Both are correct; they measure different
things.

Precision depends on base rate. Relevant tenders are ~38% of the evaluation set
and ~0.5% of the live corpus — two orders of magnitude apart. **Evaluation-set
precision is valid for comparing classifiers to each other, and invalid as a
claim about the product.** The runner prints this caveat on every run, because
the number is quotable and easy to quote wrongly.

The second caveat is worse and also printed: keyword scores **recall 1.000**
here, which is not a good result but a symptom. The positives were collected by
keyword search, so the set cannot contain a positive that keyword misses. Real
recall is unknown and probably lower. Phase 3 fixes both by drawing a random
sample from the corpus and labeling it blind.

### Three output classes, not two

Labelling real tenders surfaced two cases a binary classifier gets wrong even
when it guesses right:

- **`indeterminado`** — the metadata is genuinely insufficient. "Purchase of IT
  items, per the conditions established in this tender and its annexes" carries
  no decidable signal, and the text defers to the annex. The correct answer is
  *fetch the document*, not yes or no. This is the only case that triggers
  step 4 of the cascade, and it gets its own metric.
- **`lote_misto`** — a flag, not a class. Tenders bundle in-scope software with
  out-of-scope infrastructure or labor. Given the recall/precision asymmetry,
  these are alerted **with a caveat** rather than discarded; the user decides
  whether partial participation is worth it.

Assumptions about the company that remain unvalidated are recorded explicitly in
`perfil-empresa.yaml` under `premissas`, each listing the labels that depend on
it. One assumption decides an entire family of tenders — leaving it implicit in
the labels would make it invisible when it turns out to be wrong.

## Running it

```sh
cp .env.example .env      # optional — the defaults work
docker compose up -d
curl localhost:8010/health
```

```json
{"status":"ok","pgvector":true,"contratacoes":0,"ultima_ingestao":null}
```

`ultima_ingestao` is in the healthcheck deliberately. An empty corpus because
the last ingestion failed and an empty corpus because there was nothing to
ingest look identical from the outside, and the first one is the failure that
makes a user miss a deadline.

Ports default to 5440 (Postgres) and 8010 (API) to stay clear of the usual
local occupants; override in `.env`.

### Embeddings

The embedding model is an optional extra, so the default install and the API
image stay lean (torch adds ~2GB):

```sh
uv pip install -e ".[embeddings]"
python -m app.indexar
```

### Ingesting

```sh
python -m app.ingest --de 20260701 --ate 20260715
```

Idempotent — re-running a window updates in place and reports `registros_novos: 0`.
Exits non-zero unless the run was clean, so a cron job that fails is noticed.

Every attempt writes a row to `ingestao_execucao`, and the row is created as
`falha` *before* the work starts, then promoted on success. A process killed
mid-run therefore leaves a record that says it failed — which is true.
Writing `ok` optimistically would leave a crashed run looking successful, and a
successful-looking run with no tenders is precisely the lie that costs a user a
deadline.

Three outcomes, deliberately not two:

| Status | Meaning |
|---|---|
| `ok` | The window was read completely. Zero tenders is a valid `ok`. |
| `parcial` | Some pages landed, then the API became unreachable. The corpus has a gap; re-run. |
| `falha` | Nothing was read. |

On a 200-record slice of the live corpus, **19.5%** of tenders had an unknown
value (`0.0` normalised to `NULL`) and **28.5%** carried a publishing-platform
prefix that had to be stripped before the text is usable for matching. Both
transformations happen on ingest.

## Repository layout

```
app/
  main.py               FastAPI app and healthcheck
  db.py                 Connection pool, value normalisation
  pncp.py               API client: retries, and raising instead of returning []
  ingest.py             Incremental idempotent ingestion + CLI
  perfil.py             Loads the company profile
  filtros.py            Cascade layer 1: hard deadline cut, soft caveats
  embeddings.py         Cascade layer 2: local model, profile vs tender
  indexar.py            Backfills pgvector embeddings, idempotent
  llm.py                Cascade layer 3: LLM relevance judgment, structured output
  custo.py              Daily spend cap for layer 3, append-only cost/latency ledger
tests/
  test_pncp.py          Client retry and text-cleaning contract
  test_ingest.py        Ingestion outcome contract (ok / parcial / falha)
  test_filtros.py       Layer 1 must never drop a relevant tender
  test_eval_runner.py   Scoring rules, and that no baseline passes
  test_embeddings.py    The claim: separating what keyword cannot
  test_llm.py           Layer 3 parsing and cost accounting, no network
  test_custo.py         Spend cap actually caps, only on today's spend
sql/
  001_schema.sql        Tables, applied on first container start
docs/
  corpus-notes.md       Findings from the live PNCP API and what they changed
evals/
  perfil-empresa.yaml   Company profile — defines what "relevant" means
  eval-set.yaml         Labeled real tenders, built pre-implementation
  run.py                Scorer + method caveats printed on every run
  classificadores.py    Baselines to beat
```

## License

MIT
