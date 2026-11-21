# Exchange rate cache (Exchange)

!!! info "Owner: Gabriel"

!!! note "Provider"

    The measurements on this page were taken with AwesomeAPI. Since 20/11 the default provider is the
    Central Bank of Brazil's PTAX ([Exchange](../servicos/exchange.md)); the cache (30 s TTL, per-pair lock
    and *stale-if-error*) is the same and still needed: PTAX is a public service, never load-test it.

## Problem { #problema }

Exchange depends on a **third-party API** (AwesomeAPI). Without a cache, every
`GET /exchange/USD/BRL` request turns into an HTTP call outside our network:

- the **latency** of a service we do not control (tens to hundreds of ms, sometimes seconds) lands
  on the path of every request;
- the free API has a **rate limit**: under load it starts responding `429`, and then Exchange
  goes down for **all** users, not just for whoever generated the load;
- if the external API goes down, Exchange goes down with it.

It is the same pattern as the [token cache](cache.md) in the gateway, but here the bottleneck is outside the platform.

## Solution { #solucao }

**In-memory** cache inside Exchange itself, per currency pair:

``` mermaid
flowchart LR
    r["N requests<br/>USD → BRL"] --> ex[exchange]
    ex -->|"quote &lt; 30 s old?"| hit{hit}
    hit -->|yes| resp([response])
    hit -->|no| lock["pair lock<br/>(only 1 fetch)"]
    lock -->|"1 call"| api([AwesomeAPI])
    api -->|ok| save["store in cache"] --> resp
    api -->|"failure / 429"| stale{"quote<br/>&lt; 10 min old?"}
    stale -->|yes| resp
    stale -->|no| e503([503])
```

| Decision | Reason |
|---|---|
| **30 s** TTL (`CACHE_TTL`) | A 30-second-old quote is acceptable for a store; this way each replica makes at most 2 calls/min per pair, regardless of load. |
| Process memory, not Redis | There are few pairs (tiny cardinality) and the data is public: losing the cache on a restart costs 1 call. A shared Redis would only save `replicas − 1` calls every 30 s. |
| **One lock per pair** | When the cache expires with 200 users asking for USD-BRL at the same time, only **one** request calls the API and the others wait and read the cache (avoids *cache stampede*). |
| ***Stale-if-error*** for up to 10 min (`CACHE_STALE_TTL`) | If the external API fails or returns `429`, we serve the last known quote instead of `503`. |
| Nonexistent pair (`404`) does **not** go into the cache | Client errors are not cached. |
| 3 s timeout on the external call | A slow API does not hold Exchange's connections indefinitely. |
| `CACHE_TTL=0` turns it off | For comparison in the [load test](../devops/load-testing.md). |

## Code { #codigo }

``` { .python .copy linenums="1" title="app/cache.py" }
--8<-- "api/exchange-service/app/cache.py"
```

## How to verify { #como-verificar }

The service's tests prove the behavior without depending on the real API (`tests/test_cache.py`):

| Test | Expected result |
|---|---|
| 50 consecutive requests to `/exchange/USD/BRL` | **1** call to the external API |
| 100 **concurrent** requests with an empty cache | **1** call to the external API |
| API down 2 min after the last quote | `200` with the previous quote (*stale*) |
| API down 11 min later | `503` |

With the platform running, the metric `exchange_quote_cache_total{result="hit|miss|stale|error"}` shows the
hit rate in Grafana ([Observability](observability.md)).

## Results { #resultados }

Measured on 05/11/2026 by the [load test](../devops/load-testing.md) (experiment 3: 10 users,
30 s, `GET /exchange/USD/BRL` and `EUR/BRL` through `lb` → gateway → exchange, against the real AwesomeAPI):

| `EXCHANGE_CACHE_TTL` | Requests | p50 | p95 | p99 | Errors |
|---|---:|---:|---:|---:|---:|
| `0` (no cache) | 445 | 23 ms | 49 ms | 69 ms | 0 % |
| `30` (with cache) | 454 | **3 ms** | **4 ms** | 17 ms | 0 % |

p95 drops by **92%**: without cache, the latency of every quote is that of the external API; with cache, only the first
quote of each pair every 30 s pays that latency. The biggest gain does not show in the table: without cache, the
external API received ≈ 7 calls per second from just 10 users; with cache, it receives at most 2 per
minute per pair and per replica, regardless of load — far from the free API's rate limit.

Earlier, running the service on its own against the real API: the first `USD/BRL` lookup took 60 ms (external
call), the next one **1.5 ms** (cache).
