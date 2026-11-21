# Gateway

!!! info "Responsável: Leonardo · Repositório: [pna26/gateway-service](https://github.com/pna26/gateway-service){target='_blank'}"

O gateway é o **ponto único de entrada** da plataforma. Ele:

1. **roteia** cada caminho para o microsserviço certo (`/auth/**`, `/accounts/**`, `/exchange/**`);
2. **autoriza**: em toda rota que não é aberta, exige o cookie `__store_jwt_token`, valida o token no
   Auth (`POST /auth/solve`) e repassa o id da conta no header `id-account`;
3. **descarta** qualquer `id-account` enviado pelo cliente, para ninguém se passar por outra conta;
4. guarda no **Redis** o resultado da validação do token (bottleneck de [cache](../bottlenecks/cache.md));
5. expõe `GET /health-check` com o hostname da réplica (mostra o [balanceamento](../bottlenecks/load-balancing.md)).

É implementado com **Spring Cloud Gateway** sobre **WebFlux** (reativo), como no hands-on 2 e 3 do curso.

## Rotas

| Caminho | Destino | Token? |
|---|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/logout` | `auth:8080` | não |
| demais `/auth/**` | `auth:8080` | sim |
| `/accounts/**` | `account:8080` | sim |
| `/exchange/**` | `exchange:8080` | sim |
| `/exchanges/**` | `exchange:8080`, reescrito para `/exchange/**` | sim |
| `GET /health-check` | o próprio gateway | não |
| `/gateway/actuator/health`, `/gateway/actuator/prometheus` | o próprio gateway | não (o `lb` bloqueia o `prometheus` para fora) |

Os destinos vêm de variáveis de ambiente (`AUTH_URI`, `ACCOUNT_URI`, `EXCHANGE_URI`), com os nomes
dos serviços do compose/k8s como padrão.

## Decisão de autorização

``` mermaid
flowchart TD
    A[Requisição] --> B{rota aberta?<br/>RouterValidator}
    B -- sim --> OK[encaminha ao serviço]
    B -- não --> C{cookie<br/>__store_jwt_token?}
    C -- não --> E401[401]
    C -- sim --> D{token no Redis?}
    D -- sim --> H[header id-account = id em cache]
    D -- não --> F[POST /auth/solve]
    F -- 4xx --> E401
    F -- erro/timeout --> E503[503]
    F -- 200 --> G[grava no Redis<br/>TTL = min 5 min, exp]
    G --> H
    H --> OK
```

Diferenças em relação ao código do hands-on 3:

| Hands-on 3 | Aqui | Por quê |
|---|---|---|
| `GlobalFilter` sem ordem | `Ordered` com ordem 100 | Garante que roda **depois** do `RemoveRequestHeader=id-account` (default-filters têm ordem 1, 2…), senão o header que o próprio gateway adiciona seria apagado. |
| `throw ResponseStatusException` | resposta `401`/`503` escrita direto | Evita stack trace no log a cada token inválido e diferencia "token ruim" (401) de "Auth fora do ar" (503). |
| `WebClient.builder()` novo a cada requisição | um `AuthClient` com `WebClient` reaproveitado e timeout de 3 s | Menos objetos por requisição e o gateway não fica pendurado se o Auth travar. |
| sem cache | `TokenCache` (Redis) | Ver [Cache](../bottlenecks/cache.md). |

## Código

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

## Testes

`mvn verify` roda 20 testes (também no CI, a cada push):

| Classe | O que cobre |
|---|---|
| `RouterValidatorTest` | rotas abertas do contrato, método errado, barra no fim, curinga `/**` |
| `TokenCacheTest` | a chave é o SHA-256 (não o token), leitura do `exp`, TTL nunca passa do `exp` |
| `AuthorizationFilterTest` | rota aberta, sem cookie → 401, token válido → `id-account`, cache hit não chama o Auth, token inválido → 401, Auth fora → 503 |
| `GatewayRoutingTest` | sobe o gateway de verdade contra um Auth/Account falsos (servidor HTTP do JDK): roteamento, 401, `id-account` forjado descartado, `/exchanges` → `/exchange` |

``` { .java .copy title="GatewayRoutingTest.java (trecho)" }
--8<-- "api/gateway-service/src/test/java/store/gateway/GatewayRoutingTest.java:119:129"
```

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `AUTH_URI` | `http://auth:8080` | Auth (rotas e `/auth/solve`) |
| `ACCOUNT_URI` | `http://account:8080` | Account |
| `EXCHANGE_URI` | `http://exchange:8080` | Exchange |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | Cache do token |
| `TOKEN_CACHE_ENABLED` | `true` | Liga/desliga o cache |
| `TOKEN_CACHE_TTL` | `5m` | TTL máximo de um token no cache |
| `AUTH_TIMEOUT` | `3s` | Timeout da chamada ao `/auth/solve` |
| `CORS_ALLOWED_ORIGINS` | `http://localhost,...` | Origens com `allowCredentials` |
