# edital-radar

Public procurement monitoring for Brazilian software vendors. Tracks daily
tenders published to the national procurement portal (PNCP), matches them
against a company profile, and alerts on relevant opportunities — with the
citation that justifies the match.

**Status:** Phase 1 — ingestion working against the live API. The evaluation
set was built before the system, deliberately.

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
1. SQL over metadata (state, value, deadline, modality)   free
2. Vector search: objeto × company profile (local model)  free
3. LLM relevance judgment — finalists only                cents/day
4. Document download + parsing — approved only            rare
```

Key insight from exploration: PNCP metadata includes `objetoCompra`, a
free-text description of what is being purchased. **Triage runs entirely on
metadata; documents are fetched only for finalists.** This removes document
processing from the critical path.

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

How badly does keyword matching actually do? A keyword pass over 1,200 real
records returned 7 candidates, 1 of which was a true positive. **~14% precision**,
measured on this corpus. That number is the reason this project exists.

Target metrics:

| Metric | Target | Rationale |
|---|---|---|
| Recall (relevant) | ≥ 0.85 | A missed tender is a lost contract |
| Precision | ≥ 0.60 | A false alarm costs seconds to dismiss |

Recall is weighted above precision deliberately: the asymmetry of cost in this
domain is severe.

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
tests/
  test_pncp.py          Client retry and text-cleaning contract
  test_ingest.py        Ingestion outcome contract (ok / parcial / falha)
sql/
  001_schema.sql        Tables, applied on first container start
docs/
  corpus-notes.md       Findings from the live PNCP API and what they changed
evals/
  perfil-empresa.yaml   Company profile — defines what "relevant" means
  eval-set.yaml         Labeled real tenders, built pre-implementation
```

## License

MIT
