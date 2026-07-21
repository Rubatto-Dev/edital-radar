# Corpus notes — PNCP API

Findings from hands-on exploration of the live PNCP API, July 2026. Everything
below was observed against real responses, not read in documentation.

These notes exist because they changed the architecture. Each finding maps to a
design decision in the [README](../README.md).

## The API is open

Base: `https://pncp.gov.br/api/consulta/v1`

```
GET /contratacoes/publicacao
  ?dataInicial=20260701&dataFinal=20260715
  &codigoModalidadeContratacao=6      # 6 = electronic auction (pregão eletrônico)
  &pagina=1&tamanhoPagina=10
```

No authentication, no API key. `tamanhoPagina` has a **minimum of 10** — smaller
values return `400 must be greater than or equal to 10`.
`codigoModalidadeContratacao` is required.

## Volume

**16,616 electronic auctions in 15 days** — roughly 1,100/day across 1,662 pages.

This alone rules out sending everything to an LLM. The filter cascade is a
requirement, not an optimization.

## Metadata is strong — and that is the good news

Every tender arrives with structured fields sufficient for cheap triage:

| Field | Use |
|---|---|
| `objetoCompra` | **free-text description of what is being purchased** — the basis for semantic matching |
| `valorTotalEstimado` | value-range filter |
| `unidadeOrgao.ufSigla` / `.ufNome` | geographic filter |
| `modalidadeNome` / `modalidadeId` | procurement modality |
| `dataEncerramentoProposta` | **deadline — the field that defines urgency** |
| `situacaoCompraNome` | e.g. "Divulgada no PNCP" |
| `orgaoEntidade.razaoSocial` / `.cnpj` | who is buying |
| `numeroControlePNCP` | unique key (`{cnpj}-1-{sequential}/{year}`) |

### Architectural consequence

**The entire triage can run on `objetoCompra`, without downloading a single
document.** That is cheaper and faster than the original plan assumed:

```
1. SQL over metadata (state, value, deadline, modality)   free
2. Embedding of objetoCompra × company profile            free (local model)
3. LLM judges relevance — finalists only                  cents
4. Document download + parsing — approved only            a few per day
```

Document processing leaves the critical path and becomes the final step. This
cuts cost, latency and complexity at once.

## Attachments: reachable, but the typing lies

```
GET https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{year}/{sequential}/arquivos
```

Returns a list with `url`, `titulo`, `tipoDocumentoNome`.

### Two traps found in the first case sampled

**1. They are not PDFs.** The file was served as
`application/octet-stream; charset=ISO-8859-1` and was in fact a **`.docx`**.
Type detection must use magic bytes — not the extension, not the content type.
Expect `.pdf`, `.docx`, and probably scans and images.

**2. `tipoDocumentoNome: "Edital"` is unreliable.** The document typed as the
tender notice itself contained **152 words** — it was merely an announcement
pointing at the municipality's own website:

> "The tender document may be obtained at the Procurement Department of the
> Municipality of Lindoeste (...) or at https://www.lindoeste.pr.gov.br/"

In other words: **the full tender document is frequently absent from PNCP.** It
sits behind a link on a municipal website or a third-party platform.

## Why this strengthens the project

If the data were clean, anyone could do this. The mess is what makes
structure-aware chunking, document classification and source fallback necessary
— and what makes the evaluation honest:

- It yields a concrete metric: **document coverage** — in what share of cases is
  the full tender obtainable versus only the announcement?
- It yields a specific interview story: the metadata type cannot be trusted, so
  the document is classified by its content, and there is an eval measuring it.

### Decisions that follow

1. **Triage on `objetoCompra`**, not on the document. Documents for finalists only.
2. **Document classifier**: announcement vs. full tender vs. technical study vs.
   annex. Do not trust `tipoDocumentoNome`.
3. **Type detection by magic bytes**, not extension.
4. **v1 scope: stop at the announcement.** Following links into hundreds of
   heterogeneous municipal sites is unbounded. v1 delivers object, value,
   deadline and link. Full-document retrieval is a measured improvement, not a
   requirement.
5. **New eval metric**: full-document coverage.

## Second pass (1,250-record sample)

### Real rate limit

The API returns **429 Too Many Requests** at around the 25th request in rapid
sequence (~0.3s apart). The ingester needs exponential backoff and retry. Not
optional.

### `valorTotalEstimado` is often `0.0`

Confidential or unreported values come back as `0.0`, never as `null`.
**A naive value-range filter discards those tenders silently** — including good
ones. Treat `0.0` as *unknown* and let it through. Encoded in the company
profile as `valor_zero_significa: desconhecido`.

### The domain's #1 false positive: "Sistema de Registro de Preços"

`Sistema de Registro de Preços` (price registry system) is a **statutory
procurement modality**, present in a huge fraction of all tenders — from
pharmaceuticals to reinforced concrete. It has nothing to do with software.

Equally poisonous variants found in real data:

- "sistema de proteção contra quedas" — fall-arrest equipment
- "sistema de exaustão" — kitchen extractor hood
- "sistemas de abastecimento de água" — water supply
- "sistema de vigilância" — security cameras
- "agenciamento de hospedagem" — hotel booking, not server hosting

Any keyword filter on `sistema` or `hospedagem` returns garbage at industrial
volume. **This is the empirical argument for embeddings plus LLM over regex.**

### Sector distribution (500-record sample, 2026-07-01 to 07-15)

| Sector | % | ~/day | Median value |
|---|---|---|---|
| Health / pharmaceuticals | 24% | ~264 | R$ 515k |
| Services / outsourced labor | 17% | ~187 | R$ 401k |
| IT / software | 14% | ~154 | R$ 274k |
| Vehicles / fuel | 12% | ~132 | R$ 383k |
| Construction / engineering | 9% | ~99 | R$ 713k |

Caveat: classified by keyword, with known false positives. Order of magnitude,
not precision.

**Chosen niche: IT / software** — the deciding criterion was the ability to
label the eval set with confidence, not volume.

## Third pass (10,000-record sample, April–July 2026)

Pulled to expand the evaluation set. Four findings, all of which change code.

### `valorTotalEstimado` is not only sometimes absent — sometimes it is a different unit

Already known: `0.0` means unknown. New: a uniform-voucher tender came back with
`valorTotalEstimado: 3.60` — the per-unit fee, not the contract total. Another
listed `1.84`. The field cannot be trusted as a total in either direction, so a
value-range filter is a weak signal, never a hard gate. Captured as `neg-18`.

### The same tender is published more than once, under different keys

`21250048000128-1-000038/2026` and `...-000039/2026` carry identical objects and
identical values. So do `76279967000116-1-000040` and `-000041`, where the second
copy is prefixed with the platform name.

`numeroControlePNCP` does **not** solve deduplication — the duplicates have
distinct keys. Dedupe needs content similarity plus buying-body identity.
Alerting a user twice for one tender destroys trust faster than a false positive
does. Captured as `pos-13`.

### Objects carry platform prefixes that are not part of the object

Roughly a fifth of the sampled objects begin with `[Portal de Compras Públicas] - `
or `[LICITANET] - `. This is publishing-platform metadata leaking into the free
text. It must be stripped before embedding, or every tender from the same
platform gains spurious similarity to every other.

### Keyword search has ~14% precision here — measured, not assumed

A first keyword pass over 1,200 records returned 7 candidates, of which **1** was
a true positive. Two were already labeled negatives in the eval set, and four
were regex accidents ("manutenção corretiva" matching a pattern written for
"manutenção evolutiva").

This is the project's central claim, now with a number attached: the incumbent
approach in this market is keyword alerting, and on this corpus it is wrong
roughly six times out of seven.

## Availability

On 2026-07-21 the consultation API returned `500 Erro na comunicação com o banco
de dados` (`Failed to obtain JDBC Connection`, Hikari pool exhausted) for
requests that had succeeded the day before. The upstream service is not reliably
available; the ingester must treat outages as an expected state, not an
exception — retry with backoff, and never let an outage look like "no tenders
today".
