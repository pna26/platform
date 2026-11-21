# Exchange

!!! info "Owner: Gabriel — repository [pna26/exchange-service](https://github.com/pna26/exchange-service){target="_blank"}"

**Currency exchange** microservice in **Python (FastAPI)**. It receives a currency pair, fetches the exchange rate from the
**Central Bank's PTAX** (BACEN's Olinda API, public and with no sign-up) and returns the buy and sell
price to the logged-in user. It follows the [BACEN service](https://insper.github.io/platform/pt/2026.2/exercises/exchange/bacen/){target="_blank"}
suggested by the professor: the free exchange rate APIs (AwesomeAPI, used before) do not respond to AWS
IPs, and on EKS they returned `429` for the NAT IP.

``` mermaid
flowchart LR
    u([User]) -->|"GET /exchange/USD/BRL<br/>JWT cookie"| lb[lb · Nginx] --> gw[gateway]
    gw -->|"+ id-account header"| ex[exchange]
    ex <-->|"hit (30 s)"| cache[(in-memory cache)]
    ex -->|"miss"| api([PTAX · BACEN])
```

## Contract { #contrato }

| Method | Route | Input | Output |
|---|---|---|---|
| GET | `/exchange/{from}/{to}` | `id-account` header (set by the gateway) | `{ sell, buy, date, id-account }` |
| GET | `/exchange/currencies` | — | list of `symbol` and `name`: the currencies PTAX quotes (besides BRL) |

The gateway also accepts `/exchanges/{from}/{to}` and rewrites it to `/exchange/...` (the service also
accepts the alias). Every exchange rate response carries the **`X-Cache`** header: `miss` (PTAX was called),
`hit` (memory) or `stale` (PTAX failed and the last exchange rate was served).

``` bash
curl -H "Cookie: __store_jwt_token=$TOKEN" localhost:8080/exchange/USD/BRL
```

``` json
{ "sell": 5.8889, "buy": 5.8883, "date": "2026-11-19 13:04:18", "id-account": "8f1c..." }
```

| Status | When |
|---|---|
| 200 | exchange rate (from the cache or from PTAX) |
| 400 | currency that is not 3 letters (`/exchange/US/BRL`) |
| 401 | no token (blocked at the gateway) or no `id-account` (did not go through the gateway) |
| 404 | currency PTAX does not quote (`/exchange/XYZ/BRL`) |
| 503 | PTAX down **and** no recent exchange rate in cache (the course guide uses `502`; we kept the `503` from the platform contract) |

### How the rate is calculated { #como-a-taxa-e-calculada }

For each currency, the service queries `CotacaoMoedaPeriodo` over a **7-day** window (weekends and
holidays have no quote) and uses the **last bulletin** in the list. PTAX only quotes currency → BRL, so the real
is worth 1 and any pair is computed as a cross rate via BRL, with `c` = buy and `v` = sell in BRL:

`sell = v(from) / c(to)` · `buy = c(from) / v(to)` (6 decimal places)

There are 10 currencies (AUD, CAD, CHF, DKK, EUR, GBP, JPY, NOK, SEK and USD, plus BRL). Real call on
20/11/2026: `USD/BRL` → `sell 5.8889`, `buy 5.8883` (bulletin of 19/11 13:04); `USD/EUR` → `sell 0.910763`,
`buy 0.910487`.

!!! warning "PTAX limits"

    - Bulletins only exist on business days, between ≈ 10h and 13h (Brasília time): outside that window the response is the last
      published bulletin, not a real-time exchange rate.
    - It is a public BCB service: **do not load test it**. The cache is always on; in a
      load test k6 hits memory (count the `X-Cache: miss`).

## Decisions { #decisoes }

| Decision | Reason |
|---|---|
| FastAPI + async `httpx` | While waiting for the external API, the process serves other requests (the service is almost pure I/O). |
| BACEN PTAX as provider (`EXCHANGE_PROVIDER=ptax`) | Public, no key, responds to AWS IPs (AwesomeAPI returned `429` on EKS). Sell → `sell`, buy → `buy` (hence `sell ≥ buy`). |
| AwesomeAPI only as an option (`EXCHANGE_PROVIDER=awesomeapi`) | The old mode is still available, with PTAX as fallback when AwesomeAPI fails (`EXCHANGE_FALLBACK`). |
| `pip` + `requirements.txt`, not `uv` | The course guide uses `uv`; the reusable `platform` pipeline installs Python dependencies with `pip`, so we kept the same Dockerfile. |
| Exchange rate cache for 30 s | This is this service's bottleneck: see [Exchange rate cache](../bottlenecks/exchange-cache.md). |
| Same currency (`BRL/BRL`) returns `1.0` without calling PTAX | Avoids a pointless external call. |
| `401` without `id-account` | The service should only be reached through the gateway; without the header, the request came from somewhere else. |
| 1 `uvicorn` process per container | `prometheus_client` metrics live in process memory; with several workers each scrape would see a different one. Scales with replicas (`EXCHANGE_REPLICAS` / `replicas` in k8s). |
| Port 8080 and name `exchange` | The gateway forwards to `http://exchange:8080` in compose and k8s. |
| `/metrics` outside `/exchange/**` | The gateway only forwards `/exchange/**`, so metrics stay on the internal network only (Prometheus scrapes directly). |
| `python:3.12-slim` image, user 1001, read-only FS in k8s | Fewer packages for Trivy and the same security pattern as the other services. |

## Configuration { #configuracao }

| Variable | Default | |
|---|---|---|
| `EXCHANGE_PROVIDER` | `ptax` | `ptax` or `awesomeapi` |
| `PTAX_URL` | `https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata` | BACEN PTAX API |
| `CACHE_TTL` | `30` | seconds an exchange rate is valid (`0` disables the cache) |
| `CACHE_STALE_TTL` | `600` | how long the last exchange rate is served if PTAX goes down |
| `UPSTREAM_TIMEOUT` | `3` | timeout of external calls, in seconds |
| `EXCHANGE_API_URL` | `https://economia.awesomeapi.com.br/json/last` | `awesomeapi` mode only |
| `EXCHANGE_API_TOKEN` | — | `awesomeapi` mode only (optional) |
| `EXCHANGE_FALLBACK` | `ptax` | `awesomeapi` mode only: uses PTAX if AwesomeAPI fails (`none` disables it) |

The `exchange_upstream_request_seconds{outcome}` metric (Grafana "External API" panel) measures the main
provider, so it now shows the calls to PTAX; a pair without BRL (`USD/EUR`) makes two calls.

In compose, `EXCHANGE_CACHE_TTL` and `EXCHANGE_REPLICAS` (in `.env`) become `CACHE_TTL` and the number of replicas.

## Code { #codigo }

=== "app/main.py"

    ``` { .python .copy linenums="1" }
    --8<-- "api/exchange-service/app/main.py"
    ```

=== "app/provider.py"

    ``` { .python .copy linenums="1" }
    --8<-- "api/exchange-service/app/provider.py"
    ```

=== "app/cache.py"

    ``` { .python .copy linenums="1" }
    --8<-- "api/exchange-service/app/cache.py"
    ```

=== "Dockerfile"

    ``` { .dockerfile .copy }
    --8<-- "api/exchange-service/Dockerfile"
    ```

=== "k8s/k8s.yaml"

    ``` { .yaml .copy }
    --8<-- "api/exchange-service/k8s/k8s.yaml"
    ```

## Tests { #testes }

`pytest` with the external APIs mocked (`httpx.MockTransport`), running in the CI pipeline
([CI/CD](../devops/ci-cd.md)): PTAX (last bulletin, cross rate, X→BRL and BRL→X, unknown currency →
404, PTAX down → 503, `X-Cache` miss/hit/stale, `/exchange/currencies`, `/exchanges` alias); in
`awesomeapi` mode: response contract, `sell ≥ buy`, 400/401/404/503, `429` falling back to PTAX or `503`, same currency with no external call, 50 requests → 1 external call, and the cache (TTL,
*stale-if-error*, 100 concurrent requests → 1 call). The [smoke test](../como-rodar.md#smoke-test)
checks the route end to end through the gateway, including that the incoming `id-account` is the logged-in user's.

Verified on 20/11/2026: **44 tests passing**; service running locally against the real PTAX
(`USD/BRL` → 200 with `X-Cache: miss` and then `hit`, `XYZ/BRL` → 404, no `id-account` → 401).
Earlier, on 05/11/2026, with AwesomeAPI: 21 tests; service running against the real AwesomeAPI (200 with
`sell ≥ buy` for `USD/BRL` and `EUR/BRL`, correct 400, 401 and 404); full smoke test on the local
platform (`GET /exchange/USD/BRL → 200` with the user's `id-account`). Performance in the
[load test](../devops/load-testing.md) and in [Exchange rate cache](../bottlenecks/exchange-cache.md#resultados).

!!! note "External API in CI and on EKS"

    With AwesomeAPI, the GitHub Actions runner and the EKS NAT got an error (`429`) and Exchange
    returned `503` (the smoke test treats this as a warning). That is why we switched to PTAX. Still to be
    confirmed from AWS: from a cluster pod, `curl` the PTAX URL once (a step in the course
    guide) and `GET /exchange/USD/BRL` through the ELB.

``` bash
cd api/exchange-service
pip install -r requirements.txt pytest && pytest -q
```
