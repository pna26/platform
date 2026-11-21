# Gateway

!!! info "Owner: Leonardo · Repository: [pna26/gateway-service](https://github.com/pna26/gateway-service){target='_blank'}"

The gateway is the platform's **single entry point**. It:

1. **routes** each path to the right microservice (`/auth/**`, `/accounts/**`, `/exchange/**`);
2. **authorizes**: on every route that is not open, it requires the `__store_jwt_token` cookie, validates the token
   with Auth (`POST /auth/solve`) and forwards the account id in the `id-account` header;
3. **drops** any `id-account` sent by the client, so nobody can impersonate another account;
4. stores the token validation result in **Redis** ([cache](../bottlenecks/cache.md) bottleneck);
5. exposes `GET /health-check` with the replica's hostname (shows the [load balancing](../bottlenecks/load-balancing.md)).

It is built with **Spring Cloud Gateway** on top of **WebFlux** (reactive), as in hands-on 2 and 3 of the course.

## Routes { #rotas }

| Path | Destination | Token? |
|---|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/logout` | `auth:8080` | no |
| other `/auth/**` | `auth:8080` | yes |
| `/accounts/**` | `account:8080` | yes |
| `/exchange/**` | `exchange:8080` | yes |
| `/exchanges/**` | `exchange:8080`, rewritten to `/exchange/**` | yes |
| `GET /health-check` | the gateway itself | no |
| `/gateway/actuator/health`, `/gateway/actuator/prometheus` | the gateway itself | no (`lb` blocks `prometheus` from outside) |

Destinations come from environment variables (`AUTH_URI`, `ACCOUNT_URI`, `EXCHANGE_URI`), defaulting to the
compose/k8s service names.

## Authorization decision { #decisao-de-autorizacao }

``` mermaid
flowchart TD
    A[Request] --> B{open route?<br/>RouterValidator}
    B -- yes --> OK[forward to service]
    B -- no --> C{cookie<br/>__store_jwt_token?}
    C -- no --> E401[401]
    C -- yes --> D{token in Redis?}
    D -- yes --> H[header id-account = cached id]
    D -- no --> F[POST /auth/solve]
    F -- 4xx --> E401
    F -- error/timeout --> E503[503]
    F -- 200 --> G[store in Redis<br/>TTL = min 5 min, exp]
    G --> H
    H --> OK
```

Differences from the hands-on 3 code:

| Hands-on 3 | Here | Why |
|---|---|---|
| `GlobalFilter` without order | `Ordered` with order 100 | Ensures it runs **after** `RemoveRequestHeader=id-account` (default-filters have order 1, 2…); otherwise the header the gateway itself adds would be removed. |
| `throw ResponseStatusException` | `401`/`503` response written directly | Avoids a stack trace in the log for every invalid token and distinguishes "bad token" (401) from "Auth down" (503). |
| new `WebClient.builder()` on every request | one `AuthClient` with a reused `WebClient` and a 3 s timeout | Fewer objects per request, and the gateway does not hang if Auth stalls. |
| no cache | `TokenCache` (Redis) | See [Cache](../bottlenecks/cache.md). |

## Code { #codigo }

=== "AuthorizationFilter.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/gateway-service/src/main/java/store/gateway/security/AuthorizationFilter.java"
    ```

=== "RouterValidator.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/gateway-service/src/main/java/store/gateway/security/RouterValidator.java"
    ```

=== "AuthClient.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/gateway-service/src/main/java/store/gateway/security/AuthClient.java"
    ```

=== "TokenCache.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/gateway-service/src/main/java/store/gateway/security/TokenCache.java"
    ```

=== "application.yaml"

    ``` { .yaml .copy linenums="1" }
    --8<-- "api/gateway-service/src/main/resources/application.yaml"
    ```

=== "Dockerfile"

    ``` { .dockerfile .copy linenums="1" }
    --8<-- "api/gateway-service/Dockerfile"
    ```

=== "k8s/k8s.yaml"

    ``` { .yaml .copy linenums="1" }
    --8<-- "api/gateway-service/k8s/k8s.yaml"
    ```

## Tests { #testes }

`mvn verify` runs 20 tests (also in CI, on every push):

| Class | What it covers |
|---|---|
| `RouterValidatorTest` | open routes from the contract, wrong method, trailing slash, `/**` wildcard |
| `TokenCacheTest` | the key is the SHA-256 (not the token), reading `exp`, TTL never goes past `exp` |
| `AuthorizationFilterTest` | open route, no cookie → 401, valid token → `id-account`, cache hit does not call Auth, invalid token → 401, Auth down → 503 |
| `GatewayRoutingTest` | starts the real gateway against a fake Auth/Account (JDK HTTP server): routing, 401, forged `id-account` dropped, `/exchanges` → `/exchange` |

``` { .java .copy title="GatewayRoutingTest.java (excerpt)" }
--8<-- "api/gateway-service/src/test/java/store/gateway/GatewayRoutingTest.java:119:129"
```

## Configuration { #configuracao }

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_URI` | `http://auth:8080` | Auth (routes and `/auth/solve`) |
| `ACCOUNT_URI` | `http://account:8080` | Account |
| `EXCHANGE_URI` | `http://exchange:8080` | Exchange |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | Token cache |
| `TOKEN_CACHE_ENABLED` | `true` | Turns the cache on/off |
| `TOKEN_CACHE_TTL` | `5m` | Maximum TTL of a token in the cache |
| `AUTH_TIMEOUT` | `3s` | Timeout of the call to `/auth/solve` |
| `CORS_ALLOWED_ORIGINS` | `http://localhost,...` | Origins with `allowCredentials` |
