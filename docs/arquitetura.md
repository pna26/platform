# Architecture

## Overview { #visao-geral }

``` mermaid
flowchart LR
    user([User]) e0@==>|":8080 local / ELB on AWS"| lb
    subgraph lbl [Load balancing]
        lb["lb · Nginx"]:::orange
    end
    subgraph trusted [Trusted layer — no exposed port]
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
    exchange --> ext([External exchange rate API])
    e0@{ animate: true }
    e1@{ animate: true }
    e2@{ animate: true }
    classDef orange fill:#FCBE3E
    classDef red fill:#fcc
```

| Component | Technology | Role |
|---|---|---|
| `lb` | Nginx | Single exposed entry point. Distributes requests across the gateway replicas. |
| `gateway` | Spring Cloud Gateway (WebFlux) | Routes, validates the JWT (via Auth), and injects the `id-account` header. |
| `redis` | Redis 7 | Cache shared across the gateway replicas: token → account id. |
| `auth` | Spring Boot + JJWT | Login, JWT issuing and validation. |
| `account` | Spring Boot + JPA + Flyway | Sign-up and account data. |
| `exchange` | Python | Currency exchange rates (third-party API). |
| `db` | PostgreSQL 17 | Database for Account (schema `accounts`) and Notification (schema `notification`). |
| `kafka` | Apache Kafka 4 (KRaft) | Messaging: `account-events` topic (see [Messaging](bottlenecks/messaging.md)). |
| `notification` | Spring Boot + Spring Kafka | Consumes `AccountCreated` and sends the welcome email (idempotent, with DLT). |
| `mailpit` | Mailpit | Fake SMTP server with a web inbox (local/cluster only). |

## Flow of an authenticated request { #fluxo-de-uma-requisicao-autenticada }

``` mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant LB as Nginx
    participant GW as Gateway
    participant R as Redis
    participant A as Auth
    participant S as Service (account/exchange)
    U->>LB: GET /accounts (cookie __store_jwt_token)
    LB->>GW: proxy (round robin across replicas)
    GW->>GW: strips id-account sent by the client
    GW->>R: GET gateway:token:sha256(jwt)
    alt cache hit
        R-->>GW: account id
    else cache miss
        GW->>A: POST /auth/solve {token}
        A-->>GW: {idAccount} or 401
        GW->>R: SET ... EX min(5 min, JWT exp)
    end
    GW->>S: GET /accounts + id-account header
    S-->>U: response
```

## API contract { #contrato-da-api }

Agreed upon within the group (based on the course website, hands-on 1 and 3, and the Exchange exercise).

### Gateway { #gateway }

- Reads the `__store_jwt_token` cookie on every request.
- Open routes (no token): `POST /auth/register`, `POST /auth/login`, `GET /auth/logout`.
- Everything else (including `/accounts/**` and `/exchange/**`) requires a token.
- Validates it by calling `POST /auth/solve` on Auth and forwards the id in the **`id-account`** header.
- `/exchanges/**` is also accepted and rewritten to `/exchange/**` (the assignment uses both names).

### Auth (`http://auth:8080`) { #auth-httpauth8080 }

| Method | Route | Input | Output |
|---|---|---|---|
| POST | `/auth/register` | `{ name, email, password }` | 200 |
| POST | `/auth/login` | `{ email, password }` | 200 + `Set-Cookie: __store_jwt_token=<jwt>` |
| GET | `/auth/logout` | — | 200, clears the cookie |
| GET | `/auth/me` | `id-account` header | `{ id, name, email }` |
| POST | `/auth/solve` | `{ token }` | `{ idAccount }` (internal, only the gateway calls it) |
| GET | `/auth/health-check` | — | 200 |

### JWT { #jwt }

- Claims: `jti` = account id, `sub` = name, `email`, `iss`, `nbf`, `exp`.
- Lifetime: 24 h (`JWT_DURATION`). Secret in `JWT_SECRET_KEY` (environment variable, never in the code).
- Cookie: `HttpOnly`, `Secure`, `SameSite=None`, `Path=/`.

### Account (`http://account:8080`) { #account-httpaccount8080 }

| Method | Route | Input | Output |
|---|---|---|---|
| POST | `/accounts` | `{ name, email, password }` | 201 |
| GET | `/accounts` | — | `[{ id, name, email }]` |
| GET | `/accounts/{id}` | — | `{ id, name, email }` |
| DELETE | `/accounts/{id}` | — | 204 |
| POST | `/accounts/login` | `{ email, password }` | `{ id, name, email }` (internal, Auth calls it) |
| GET | `/accounts/health-check` | — | 200 |

### Exchange (Python) { #exchange-python }

| Method | Route | Input | Output |
|---|---|---|---|
| GET | `/exchange/{from}/{to}` | `id-account` header | `{ sell, buy, date, id-account }` |

## Decisions { #decisoes }

| Decision | Reason |
|---|---|
| Service names `auth`, `account`, `exchange`, `gateway` are the same in compose and k8s | The Feign clients use `http://account:8080` and `http://auth:8080`. |
| Only `lb` publishes a port | The gateway and the services stay in the trusted layer; the `id-account` header can only come from the gateway. |
| Token cache in Redis, key = SHA-256 of the JWT | Takes Auth out of the path of every request without storing the token in plain text. |
| One image per service on GHCR and ECR (via OIDC, no AWS key on GitHub) | The pipeline publishes multi-arch images (amd64 for EKS, arm64 for the group's Macs). |

### HTTPS (known limitation) { #https-limitacao-conhecida }

Auth returns the token in the `__store_jwt_token` cookie with `HttpOnly`, `Secure`, and `SameSite=None`.
The browser only stores a `Secure` cookie that comes from HTTPS (or from `localhost`), so:

| Where | Status |
|---|---|
| Local compose (`http://localhost`) | Works in the browser: `localhost` counts as a secure origin. |
| EKS (`http://<elb>.elb.amazonaws.com`) | The ELB serves HTTP only. Clients that send the cookie in the header (`curl`, `scripts/smoke-test.sh`, k6) work; the **browser discards the cookie** and the protected routes return 401. |

**Why there is no HTTPS on EKS:** a public ACM certificate requires a DNS-validated domain, and the
group has no domain; the ELB name belongs to AWS and changes every time the cluster is recreated. A self-signed
certificate on Nginx would make the browser show a security warning, which is unacceptable for a store.

**What it would look like in production:** a custom domain + an ACM certificate + TLS terminated at the load balancer,
with annotations on the `lb` Service and an HTTP-to-HTTPS redirect:

``` yaml
metadata:
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-ssl-cert: arn:aws:acm:us-east-2:<conta>:certificate/<id>
    service.beta.kubernetes.io/aws-load-balancer-ssl-ports: "443"
    service.beta.kubernetes.io/aws-load-balancer-backend-protocol: http
```

From the load balancer inward, traffic stays on HTTP, inside the cluster's private network (trusted layer).
