# External Data Policy

`sources.yaml` is the provider registry and per-metric Source of Truth priority list. It uses JSON syntax, which is a valid YAML 1.2 subset, so the standard-library JSON parser can validate it without a runtime dependency. Provider rows are candidates/configuration, not evidence that a service is licensed or currently available.

## Current Operating State

- External fetch is disabled by default. Normal `scripts/analyze_holdings.py` runs do not make provider requests.
- Fetch requires `--fetch-external-data`, provider `enabled: true`, approved `acquisition_status` and `license_status`, reviewed numeric rate limits, configured resource mappings, and any required API key.
- All candidate providers currently start disabled and unreviewed. Missing or unverified data is represented as `status: unavailable`, `value: null`; estimates are not substituted.
- Source order is metric-specific in `sources.yaml`. At most one source is selected for a metric per collection; sources are not averaged or silently blended. A lower-priority source is marked `source_role: fallback`.
- HTML scraping is disabled. Provider adapters accept configured APIs, official CSV/JSON downloads, or an explicitly marked local development fixture only.
- Alpha Vantage and FRED require API keys in query parameters. Since credentials in URLs are prohibited, these adapters fail closed until a compliant authentication mechanism is available; configure no resources for them in the meantime.

## Provider Candidate Matrix

The detailed machine-readable attributes, candidate free-tier notes, key requirements, rate-limit notes, licensing status, retention/publication permissions, and supported metrics are maintained in `sources.yaml`. Entries marked `verify` or `unreviewed` must remain disabled until reviewed against current provider terms.

| Provider | Candidate use | Initial collection state | Key/license note |
| --- | --- | --- | --- |
| Alpha Vantage | ETF/equity historical prices | Disabled | API key required; free limits and publication terms unverified |
| Tiingo | Equity/ETF prices | Disabled | API key required; treat raw redistribution as disallowed unless license is confirmed |
| Nasdaq Data Link | Dataset-specific market/macro data | Disabled | Review license and redistribution separately for every dataset |
| FRED | Rates, CPI, unemployment, macro series | Disabled | API key required; verify terms and rate limits |
| Yahoo Finance | Local development fixture only | Denied by default | No unofficial endpoint or scraping adapter is implemented |
| iShares / BlackRock | Official ETF facts, structured downloads | Disabled | Product-specific download and reuse terms require review |
| Vanguard | Official ETF facts, structured downloads | Disabled | Product-specific download and reuse terms require review |
| Avantis | Official fund facts/holdings downloads | Disabled | Product-specific download and reuse terms require review |
| Invesco | Official fund facts/holdings downloads | Disabled | Product-specific download and reuse terms require review |
| State Street / SPDR | Official ETF facts/holdings downloads | Disabled | Product-specific download and reuse terms require review |
| Japanese asset managers | NAV, fees, AUM, reports, NISA status | Disabled | Latest delivery report is primary for total expense ratio; store its report period |
| MSCI / FTSE Russell / S&P DJI / Nasdaq Indexes | Index methodology and composition | Disabled | Index license and redistribution must be verified per index |
| ETF.com / Morningstar / licensed fund-flow source | Auxiliary flows/categories | Disabled | Structured licensed access only; no free-page scraping |
| Financial Services Agency / Investment Trusts Association Japan | NISA eligibility/rules | Disabled | Use official downloadable/structured lists when available; review dataset reuse terms |

## Required Record Metadata

Every normalized record stores schema version, provider ID/name, metric, subject, status, value, unit, source name/URL, fetch timestamp, as-of date, optional report period, stale flag, confidence, license status, cache permission, redistribution permission, raw/derived public permission, data class, data origin (`real`, `sample`, or `unknown`), source role, and a reason or raw-response digest where applicable.

`status` is `available` only when a source returned a mapped value with an as-of date. Otherwise `value` must be null and status is `unavailable`. Staleness is based on a configured maximum age; when no age is configured, records are stale and cannot pass recommendation readiness checks.

## Storage and Publication

- API keys are read from process environment or `.env`; `.env` is ignored by Git. Never put credentials in URLs, registry data, logs, tests, or commits.
- Raw response bodies and provider rate state are written only under `data/private/`, which is ignored by GitHub Pages and version control.
- Raw response retention additionally requires `raw_retention_allowed: true` and `cache_allowed: true` for that provider.
- Public Dashboard/report JSON includes an available value only when cache permission and the matching raw/derived publication permission are explicitly `true`. Otherwise it exposes status/provenance with the value masked as unavailable.
- Sample fixtures remain marked `data_origin: sample`, are never public, and never count as real observations.

## Analysis Rules

- Annualized return, volatility, Sharpe ratio, max drawdown, and tracking difference are calculated locally from normalized price/NAV series. Missing risk-free or benchmark series yields an unavailable metric.
- Portfolio overlap is calculated locally from complete holdings using the sum of minimum security weights. Holdings must share source/provider and as-of date; otherwise overlap is unavailable.
- Expense ratio (ongoing/trust fee) and Japanese total expense ratio are distinct metrics. Total expense ratio uses the latest official delivery report and stores report period/as-of.
- A fund receives no score or recommendation until current expense ratio, AUM, complete holdings, and benchmark data are available and non-stale. The system does not infer a winner from partial candidate rows.

## Enabling a Provider

1. Review the provider's current terms, license, rate limits, cache permission, raw redistribution, and derived-data publication separately; record evidence/notes in `sources.yaml`.
2. Configure the resource URL, structured format, exact field mapping, identifier, units, as-of column, and staleness window. Dataset-specific sources require dataset-specific approval.
3. Set `rate_limit_review_status: approved`, an explicit `request_budget_per_run`, and `min_request_interval_seconds` consistent with the provider contract. Do not invent these values.
4. Set `acquisition_status` and `license_status` to approved only after review; set `enabled: true` only for the reviewed provider/resource.
5. Put any required credential in local `.env`, never in Git.
6. Run the explicit `--fetch-external-data` command and review saved private snapshots, public masking, source/as-of metadata, staleness, and provider output before using it in analysis.
