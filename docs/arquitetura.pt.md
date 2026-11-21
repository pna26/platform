# Arquitetura

## Visão geral

``` mermaid
flowchart LR
    user([Usuário]) e0@==>|":8080 local / ELB na AWS"| lb
    subgraph lbl [Balanceamento de carga]
        lb["lb · Nginx"]:::orange
    end
    subgraph trusted [Camada confiável — sem porta exposta]
        lb e1@==> gw1["gateway 1"]
        lb e2@==> gw2["gateway 2"]
        gw1 & gw2 <-->|"token → id-account"| redis[("Redis<br/>cache")]:::red
        gw1 & gw2 -->|"/auth/**"| auth
        gw1 & gw2 -->|"/accounts/**"| account
        gw1 & gw2 -->|"/exchange/**"| exchange
        auth -->|Feign| account
        account --> db[("PostgreSQL")]
        account -.->|"AccountCreated (outbox)"| kafka[["Kafka"]]:::red
        kafka -.-> notification
        notification -.->|SMTP| mailpit(["Mailpit"])
    end
    exchange --> ext([API de câmbio externa])
    e0@{ animate: true }
    e1@{ animate: true }
    e2@{ animate: true }
    classDef orange fill:#FCBE3E
    classDef red fill:#fcc
```

| Componente | Tecnologia | Papel |
|---|---|---|
| `lb` | Nginx | Único ponto exposto. Distribui as requisições entre as réplicas do gateway. |
| `gateway` | Spring Cloud Gateway (WebFlux) | Roteia, valida o JWT (via Auth) e injeta o header `id-account`. |
| `redis` | Redis 7 | Cache compartilhado entre as réplicas do gateway: token → id da conta. |
| `auth` | Spring Boot + JJWT | Login, emissão e validação do JWT. |
| `account` | Spring Boot + JPA + Flyway | Cadastro e dados das contas. |
| `exchange` | Python | Cotação entre moedas (API de terceiros). |
| `db` | PostgreSQL 17 | Banco do Account (schema `accounts`) e do Notification (schema `notification`). |
| `kafka` | Apache Kafka 4 (KRaft) | Mensageria: tópico `account-events` (ver [Mensageria](bottlenecks/messaging.md)). |
| `notification` | Spring Boot + Spring Kafka | Consome `AccountCreated` e envia o e-mail de boas-vindas (idempotente, com DLT). |
| `mailpit` | Mailpit | Servidor SMTP falso com caixa de entrada web (só local/cluster). |

## Fluxo de uma requisição autenticada

``` mermaid
sequenceDiagram
    autonumber
    actor U as Usuário
    participant LB as Nginx
    participant GW as Gateway
    participant R as Redis
    participant A as Auth
    participant S as Serviço (account/exchange)
    U->>LB: GET /accounts (cookie __store_jwt_token)
    LB->>GW: proxy (round robin entre réplicas)
    GW->>GW: remove id-account enviado pelo cliente
    GW->>R: GET gateway:token:sha256(jwt)
    alt cache hit
        R-->>GW: id da conta
    else cache miss
        GW->>A: POST /auth/solve {token}
        A-->>GW: {idAccount} ou 401
        GW->>R: SET ... EX min(5 min, exp do JWT)
    end
    GW->>S: GET /accounts + header id-account
    S-->>U: resposta
```

## Contrato da API

Combinado entre o grupo (baseado no site do curso, hands-on 1 e 3, e no exercício do Exchange).

### Gateway

- Lê o cookie `__store_jwt_token` em toda requisição.
- Rotas abertas (sem token): `POST /auth/register`, `POST /auth/login`, `GET /auth/logout`.
- Todo o resto (inclusive `/accounts/**` e `/exchange/**`) exige token.
- Valida chamando `POST /auth/solve` no Auth e repassa o id no header **`id-account`**.
- `/exchanges/**` também é aceito e reescrito para `/exchange/**` (o enunciado usa os dois nomes).

### Auth (`http://auth:8080`)

| Método | Rota | Entrada | Saída |
|---|---|---|---|
| POST | `/auth/register` | `{ name, email, password }` | 200 |
| POST | `/auth/login` | `{ email, password }` | 200 + `Set-Cookie: __store_jwt_token=<jwt>` |
| GET | `/auth/logout` | — | 200, limpa o cookie |
| GET | `/auth/me` | header `id-account` | `{ id, name, email }` |
| POST | `/auth/solve` | `{ token }` | `{ idAccount }` (interno, só o gateway chama) |
| GET | `/auth/health-check` | — | 200 |

### JWT

- Claims: `jti` = id da conta, `sub` = nome, `email`, `iss`, `nbf`, `exp`.
- Duração: 24 h (`JWT_DURATION`). Segredo em `JWT_SECRET_KEY` (variável de ambiente, nunca no código).
- Cookie: `HttpOnly`, `Secure`, `SameSite=None`, `Path=/`.

### Account (`http://account:8080`)

| Método | Rota | Entrada | Saída |
|---|---|---|---|
| POST | `/accounts` | `{ name, email, password }` | 201 |
| GET | `/accounts` | — | `[{ id, name, email }]` |
| GET | `/accounts/{id}` | — | `{ id, name, email }` |
| DELETE | `/accounts/{id}` | — | 204 |
| POST | `/accounts/login` | `{ email, password }` | `{ id, name, email }` (interno, o Auth chama) |
| GET | `/accounts/health-check` | — | 200 |

### Exchange (Python)

| Método | Rota | Entrada | Saída |
|---|---|---|---|
| GET | `/exchange/{from}/{to}` | header `id-account` | `{ sell, buy, date, id-account }` |

## Decisões

| Decisão | Motivo |
|---|---|
| Nomes dos serviços `auth`, `account`, `exchange`, `gateway` iguais no compose e no k8s | Os Feign clients usam `http://account:8080` e `http://auth:8080`. |
| Só o `lb` publica porta | Gateway e serviços ficam na camada confiável; o header `id-account` só pode vir do gateway. |
| Cache do token no Redis, chave = SHA-256 do JWT | Tira o Auth do caminho de toda requisição sem guardar o token em texto puro. |
| Uma imagem por serviço no GHCR e no ECR (via OIDC, sem chave da AWS no GitHub) | O pipeline publica imagens multi-arch (amd64 para o EKS, arm64 para os Macs do grupo). |

### HTTPS (limitação conhecida)

O Auth devolve o token no cookie `__store_jwt_token` com `HttpOnly`, `Secure` e `SameSite=None`.
O navegador só guarda um cookie `Secure` vindo de HTTPS (ou de `localhost`), então:

| Onde | Situação |
|---|---|
| Compose local (`http://localhost`) | Funciona no navegador: `localhost` conta como origem segura. |
| EKS (`http://<elb>.elb.amazonaws.com`) | O ELB atende só HTTP. Clientes que mandam o cookie no header (`curl`, `scripts/smoke-test.sh`, k6) funcionam; o **navegador descarta o cookie** e as rotas fechadas respondem 401. |

**Por que não há HTTPS no EKS:** um certificado público do ACM exige um domínio validado por DNS, e o
grupo não tem domínio; o nome do ELB é da AWS e muda a cada vez que o cluster é recriado. Um certificado
autoassinado no Nginx faria o navegador mostrar alerta de segurança, o que não serve para uma loja.

**Como ficaria em produção:** domínio próprio + certificado do ACM + TLS terminado no balanceador,
com anotações no Service `lb` e redirecionamento de HTTP para HTTPS:

``` yaml
metadata:
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-ssl-cert: arn:aws:acm:us-east-2:<conta>:certificate/<id>
    service.beta.kubernetes.io/aws-load-balancer-ssl-ports: "443"
    service.beta.kubernetes.io/aws-load-balancer-backend-protocol: http
```

Do balanceador para dentro o tráfego segue em HTTP, na rede privada do cluster (camada confiável).
