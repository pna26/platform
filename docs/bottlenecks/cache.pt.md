# Cache (Redis)

!!! info "Responsável: Leonardo"

## Problema

Sem cache, **toda** requisição autenticada faz o gateway chamar o Auth (`POST /auth/solve`) antes de
seguir para o serviço de destino. O Auth vira um gargalo e um ponto de falha no caminho de todas as
rotas, mesmo quando o mesmo usuário repete o mesmo token centenas de vezes.

E como o gateway tem várias réplicas atrás do Nginx, um cache **em memória** de cada JVM não
resolveria: a réplica 2 não veria o que a réplica 1 já validou.

## Solução

Padrão **cache-aside** com um **Redis compartilhado** por todas as réplicas do gateway:

``` mermaid
flowchart LR
    gw["gateway<br/>(qualquer réplica)"] -->|"1. GET gateway:token:&lt;sha256&gt;"| redis[(Redis)]
    redis -->|"2a. hit → id da conta"| gw
    redis -->|"2b. miss"| gw
    gw -->|"3. POST /auth/solve (só no miss)"| auth[Auth]
    auth -->|"4. idAccount"| gw
    gw -->|"5. SET ... EX min(5 min, exp)"| redis
```

| Decisão | Motivo |
|---|---|
| Chave = `gateway:token:` + **SHA-256 do JWT** | O Redis nunca guarda o token em si; quem ler o Redis não consegue reutilizar tokens. |
| TTL = **menor** entre 5 min e o `exp` do JWT | Um token expirado nunca continua valendo por causa do cache. |
| Só guarda tokens **válidos** | Token inválido não vai para o cache: continua sendo recusado pelo Auth (401). |
| Redis fora do ar → segue **sem cache** | O cache é otimização: se cair, o gateway volta a chamar o Auth (a validação continua obrigatória). O health do Redis foi desligado no actuator para o gateway não ficar "DOWN" por causa disso. |
| Métrica `gateway_token_cache_total{result=hit\|miss\|error}` | Mostra no Prometheus/Grafana quantas validações o cache economizou. |
| `TOKEN_CACHE_ENABLED=false` | Permite comparar com e sem cache no teste de carga. |

!!! note "Limite conhecido"

    O JWT é *stateless*: depois do logout o cookie é apagado no navegador, mas o token continua
    válido até o `exp` (com ou sem cache). O cache não piora isso, porque o TTL nunca passa do `exp`.

## Código

``` { .java .copy linenums="1" title="TokenCache.java" }
--8<-- "api/gateway-service/src/main/java/store/gateway/security/TokenCache.java"
```

No `AuthorizationFilter`, o cache entra antes da chamada ao Auth:

``` { .java .copy title="AuthorizationFilter.java (trecho)" }
--8<-- "api/gateway-service/src/main/java/store/gateway/security/AuthorizationFilter.java:78:89"
```

``` { .yaml .copy title="compose.yaml (trecho)" }
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
```

No EKS o Redis roda sem persistência e com `maxmemory 200mb` + `allkeys-lru` (é só cache):
[`k8s/redis.yaml`](https://github.com/pna26/platform/blob/main/k8s/redis.yaml){target="_blank"}.

## Resultados

Medidos localmente (Docker Desktop, Mac M-series), com `ab -n 3000 -c 20` numa rota protegida e
2 réplicas do gateway:

| Métrica | Valor |
|---|---|
| Requisições autenticadas | 3001 |
| Resolvidas pelo Redis (`hit`) | **3000** |
| Chamadas ao Auth (`miss`) | **1** |

Ou seja, o Auth saiu do caminho de 99,97 % das requisições. Com o Redis parado
(`docker compose stop redis`) o gateway continuou respondendo `200` com token válido e `401` sem token,
só registrando `redis indisponível, seguindo sem cache` no log.

!!! warning "Latência"

    Na máquina local a **latência** com e sem cache ficou dentro do ruído (≈ 700–1600 req/s nas duas
    configurações), porque todos os containers dividem a mesma CPU e o `/auth/solve` é só uma
    verificação HMAC: ir ao Redis custa quase o mesmo que ir ao Auth. O ganho aqui é **tirar carga do
    Auth** (e do Account, quando o Auth precisar consultá-lo). O efeito em latência deve ser medido no
    EKS, no [teste de carga](../devops/load-testing.md), comparando `TOKEN_CACHE_ENABLED=true` e `false`.

Para conferir o cache:

``` bash
docker compose exec redis redis-cli --scan --pattern 'gateway:token:*'
docker compose exec redis redis-cli TTL gateway:token:<hash>
```
