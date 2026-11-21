# Platform 2026.2 — pna26

???+ info inline end "Edition"

    2026.2 · Insper
    Platforms, Microservices, DevOps and APIs

## Group { #grupo }

| Member | GitHub | Responsibilities |
|---|---|---|
| Leonardo | [@souzxxx](https://github.com/souzxxx){target="_blank"} | Gateway, CI/CD, load balancing, cache (Redis), vulnerability scanning, documentation (MkDocs) |
| Nicolas | [@NicolasVolf](https://github.com/NicolasVolf){target="_blank"} | Auth, Account, AWS, EKS, messaging |
| Gabriel | [@gabrielalmeidaconde](https://github.com/gabrielalmeidaconde){target="_blank"} | Exchange, load testing, observability, PaaS/costs/SLA |

Task board: [Platform 2026.2 project](https://github.com/orgs/pna26/projects/1){target="_blank"}.

## The project { #o-projeto }

A microservices platform for a store: the user signs up, logs in and looks up exchange rates
between currencies. Every request comes in through a single entry point (the **gateway**, behind an
**Nginx load balancer**), which validates the JWT token and forwards the user's identity to the services.

``` mermaid
flowchart LR
    internet([Internet]) e0@==> lb["lb · Nginx"]
    subgraph trusted [Trusted layer]
        lb e1@==> gw["gateway<br/>(replicas)"]
        gw --> auth
        gw --> account
        gw --> exchange
        gw <-->|token → id| redis[(Redis)]
        auth --> account
        account --> db[(PostgreSQL)]
        account -.->|event| kafka[[Kafka]] -.-> notification
    end
    exchange --> api3([Exchange rate API])
    e0@{ animate: true }
    e1@{ animate: true }
```

Details in [Architecture](arquitetura.md).

## Repositories { #repositorios }

| Repository | What it is | Owner |
|---|---|---|
| [pna26/platform](https://github.com/pna26/platform){target="_blank"} | Main repository: this documentation, `compose.yaml`, Nginx, shared k8s manifests, reusable pipeline and the services as submodules | Leonardo |
| [pna26/gateway-service](https://github.com/pna26/gateway-service){target="_blank"} | API gateway (Spring Cloud Gateway) | Leonardo |
| [pna26/auth](https://github.com/pna26/auth){target="_blank"} | Auth interface (Feign) and DTOs | Nicolas |
| [pna26/auth-service](https://github.com/pna26/auth-service){target="_blank"} | Auth implementation (JWT) | Nicolas |
| [pna26/account](https://github.com/pna26/account){target="_blank"} | Account interface (Feign) and DTOs | Nicolas |
| [pna26/account-service](https://github.com/pna26/account-service){target="_blank"} | Account implementation (PostgreSQL + Flyway) | Nicolas |
| [pna26/exchange-service](https://github.com/pna26/exchange-service){target="_blank"} | Exchange rates (Python/FastAPI) | Gabriel |
| [pna26/notification-service](https://github.com/pna26/notification-service){target="_blank"} | Kafka consumer: welcome e-mail (messaging) | Nicolas |

## Deliverables { #entregas }

| Task | Weight | Owner | Status |
|---|---:|---|---|
| [Gateway](servicos/gateway.md) | 5% | Leonardo | :material-check: done |
| [Auth](servicos/auth.md) | 5% | Nicolas | :material-check: done |
| [Account](servicos/account.md) | 5% | Nicolas | :material-check: done |
| [Exchange](servicos/exchange.md) | 5% | Gabriel | :material-check: done |
| [Bottlenecks](bottlenecks/index.md) | 20% | everyone | 7 implemented (2+ per member) and verified in compose/CI; messaging also on EKS (16/11) |
| [AWS](devops/aws.md) | 5% | Nicolas | :material-check: done (CI signs in to AWS via OIDC since 10/11) |
| [EKS](devops/eks.md) | 10% | Nicolas | cluster tested on 08/11 (manual deploy + smoke test); automatic deploy from CI proven on 16/11 ([runs](devops/eks.md#deploy-pelo-cicd)); load test with HPA on EKS via `scripts/eks-hpa.sh` ([result](devops/load-testing.md#resultado-no-eks)) |
| [CI/CD (GitHub Actions)](devops/ci-cd.md) | 10% | Leonardo | pipeline ready in every repository; OIDC, push to ECR and deploy to EKS proven (account, auth and notification on 16/11, [evidence](devops/ci-cd.md#evidencia)); gateway, exchange and `infra` with the `lb` fix on the next cluster spin-up |
| [Load testing](devops/load-testing.md) | 15% | Gabriel | done on compose and rehearsed with HPA on a kind cluster (12/11); EKS run automated by `scripts/eks-hpa.sh`, which fills in the [result](devops/load-testing.md#resultado-no-eks) |
| [PaaS, costs and SLA](devops/custos-sla.md) | 10% | Gabriel | :material-check: done |
| Documentation (MkDocs) | 10% | everyone | :material-check: done (published by GitHub Actions, with `mkdocs build --strict`) |

## Presentation { #apresentacao }

A 1 to 2 minute video of the project and the bottlenecks, with no narration and with subtitles: see [Presentation](apresentacao.md).
