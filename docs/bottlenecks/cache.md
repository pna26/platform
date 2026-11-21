# Cache (Redis)

!!! info "Owner: Leonardo"

## Problem { #problema }

Without a cache, **every** authenticated request makes the gateway call Auth (`POST /auth/solve`) before
moving on to the target service. Auth becomes a bottleneck and a point of failure on the path of every
route, even when the same user repeats the same token hundreds of times.

And since the gateway has several replicas behind Nginx, an **in-memory** cache in each JVM would not
solve it: replica 2 would not see what replica 1 already validated.

## Solution { #solucao }

**Cache-aside** pattern with a **Redis shared** by all gateway replicas:

``` mermaid
flowchart LR
    gw["gateway<br/>(any replica)"] -->|"1. GET gateway:token:&lt;sha256&gt;"| redis[(Redis)]
    redis -->|"2a. hit → account id"| gw
    redis -->|"2b. miss"| gw
    gw -->|"3. POST /auth/solve (only on miss)"| auth[Auth]
    auth -->|"4. idAccount"| gw
    gw -->|"5. SET ... EX min(5 min, exp)"| redis
```

| Decision | Reason |
|---|---|
| Key = `gateway:token:` + **SHA-256 of the JWT** | Redis never stores the token itself; whoever reads Redis cannot reuse tokens. |
| TTL = the **lower** of 5 min and the JWT `exp` | An expired token never stays valid because of the cache. |
| Only stores **valid** tokens | An invalid token does not go into the cache: it keeps being rejected by Auth (401). |
| Redis down → carries on **without cache** | The cache is an optimization: if it goes down, the gateway goes back to calling Auth (validation is still mandatory). The Redis health check was turned off in the actuator so the gateway does not show "DOWN" because of it. |
| Metric `gateway_token_cache_total{result=hit\|miss\|error}` | Shows in Prometheus/Grafana how many validations the cache saved. |
| `TOKEN_CACHE_ENABLED=false` | Allows comparing with and without cache in the load test. |

!!! note "Known limitation"

    The JWT is *stateless*: after logout the cookie is deleted in the browser, but the token stays
    valid until `exp` (with or without cache). The cache does not make this worse, because the TTL never goes past `exp`.

## Code { #codigo }

``` { .java .copy linenums="1" title="TokenCache.java" }
--8<-- "api/gateway-service/src/main/java/store/gateway/security/TokenCache.java"
```

In `AuthorizationFilter`, the cache comes in before the call to Auth:

``` { .java .copy title="AuthorizationFilter.java (excerpt)" }
--8<-- "api/gateway-service/src/main/java/store/gateway/security/AuthorizationFilter.java:78:89"
```

``` { .yaml .copy title="compose.yaml (excerpt)" }
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
```

On EKS, Redis runs without persistence and with `maxmemory 200mb` + `allkeys-lru` (it is only a cache):
[`k8s/redis.yaml`](https://github.com/pna26/platform/blob/main/k8s/redis.yaml){target="_blank"}.

## Results { #resultados }

Measured locally (Docker Desktop, Mac M-series), with `ab -n 3000 -c 20` on a protected route and
2 gateway replicas:

| Metric | Value |
|---|---|
| Authenticated requests | 3001 |
| Resolved by Redis (`hit`) | **3000** |
| Calls to Auth (`miss`) | **1** |

In other words, Auth was taken off the path of 99.97% of requests. With Redis stopped
(`docker compose stop redis`) the gateway kept responding `200` with a valid token and `401` without a token,
only logging `redis indisponível, seguindo sem cache`.

!!! warning "Latency"

    On the local machine, **latency** with and without cache was within the noise (≈ 700–1600 req/s in both
    configurations), because all containers share the same CPU and `/auth/solve` is just an
    HMAC check: going to Redis costs almost the same as going to Auth. The gain here is **taking load off
    Auth** (and off Account, when Auth needs to query it). The latency effect should be measured on
    EKS, in the [load test](../devops/load-testing.md), comparing `TOKEN_CACHE_ENABLED=true` and `false`.

To check the cache:

``` bash
docker compose exec redis redis-cli --scan --pattern 'gateway:token:*'
docker compose exec redis redis-cli TTL gateway:token:<hash>
```
