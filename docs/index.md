# Platform 2026.2 — pna26

???+ info inline end "Edição"

    2026.2 · Insper
    Plataformas, Microsserviços, DevOps e APIs

## Grupo

| Integrante | GitHub | Responsabilidades |
|---|---|---|
| Leonardo | [@souzxxx](https://github.com/souzxxx){target="_blank"} | Gateway, CI/CD, balanceamento de carga, cache (Redis), varredura de vulnerabilidades, documentação (MkDocs) |
| Nicolas | [@NicolasVolf](https://github.com/NicolasVolf){target="_blank"} | Auth, Account, AWS, EKS, mensageria |
| Gabriel | [@gabrielalmeidaconde](https://github.com/gabrielalmeidaconde){target="_blank"} | Exchange, testes de carga, observabilidade, PaaS/custos/SLA |

Quadro de tarefas: [Projeto Platform 2026.2](https://github.com/orgs/pna26/projects/1){target="_blank"}.

## O projeto

Uma plataforma de microsserviços para uma loja: o usuário se cadastra, faz login e consulta
câmbio entre moedas. Todas as requisições entram por um único ponto (o **gateway**, atrás de um
**balanceador Nginx**), que valida o token JWT e repassa a identidade do usuário para os serviços.

``` mermaid
flowchart LR
    internet([Internet]) e0@==> lb["lb · Nginx"]
    subgraph trusted [Camada confiável]
        lb e1@==> gw["gateway<br/>(réplicas)"]
        gw --> auth
        gw --> account
        gw --> exchange
        gw <-->|token → id| redis[(Redis)]
        auth --> account
        account --> db[(PostgreSQL)]
        account -.->|evento| kafka[[Kafka]] -.-> notification
    end
    exchange --> api3([API de câmbio])
    e0@{ animate: true }
    e1@{ animate: true }
```

Detalhes em [Arquitetura](arquitetura.md).

## Repositórios

| Repositório | O que é | Responsável |
|---|---|---|
| [pna26/platform](https://github.com/pna26/platform){target="_blank"} | Repositório principal: esta documentação, `compose.yaml`, Nginx, manifestos k8s compartilhados, pipeline reutilizável e os serviços como submódulos | Leonardo |
| [pna26/gateway-service](https://github.com/pna26/gateway-service){target="_blank"} | API gateway (Spring Cloud Gateway) | Leonardo |
| [pna26/auth](https://github.com/pna26/auth){target="_blank"} | Interface (Feign) e DTOs do Auth | Nicolas |
| [pna26/auth-service](https://github.com/pna26/auth-service){target="_blank"} | Implementação do Auth (JWT) | Nicolas |
| [pna26/account](https://github.com/pna26/account){target="_blank"} | Interface (Feign) e DTOs do Account | Nicolas |
| [pna26/account-service](https://github.com/pna26/account-service){target="_blank"} | Implementação do Account (PostgreSQL + Flyway) | Nicolas |
| [pna26/exchange-service](https://github.com/pna26/exchange-service){target="_blank"} | Câmbio (Python/FastAPI) | Gabriel |
| [pna26/notification-service](https://github.com/pna26/notification-service){target="_blank"} | Consumidor Kafka: e-mail de boas-vindas (mensageria) | Nicolas |

## Entregas

| Tarefa | Peso | Responsável | Status |
|---|---:|---|---|
| [Gateway](servicos/gateway.md) | 5% | Leonardo | :material-check: pronto |
| [Auth](servicos/auth.md) | 5% | Nicolas | :material-check: pronto |
| [Account](servicos/account.md) | 5% | Nicolas | :material-check: pronto |
| [Exchange](servicos/exchange.md) | 5% | Gabriel | :material-check: pronto |
| [Bottlenecks](bottlenecks/index.md) | 20% | todos | 6 implementados e verificados no compose/CI; falta a verificação no EKS |
| [AWS](devops/aws.md) | 5% | Nicolas | :material-check: pronto (CI entra na AWS por OIDC desde 10/11) |
| [EKS](devops/eks.md) | 10% | Nicolas | cluster testado em 08/11 (deploy manual + smoke test); falta o deploy pelo CI |
| [CI/CD (GitHub Actions)](devops/ci-cd.md) | 10% | Leonardo | pipeline pronto em todos os repositórios; entrada na AWS por OIDC e push no ECR comprovados ([evidência](devops/ci-cd.md#evidencia)); o deploy no EKS aguarda o cluster |
| [Testes de carga](devops/load-testing.md) | 15% | Gabriel | feito no compose e ensaiado com HPA num cluster kind (12/11); falta a rodada no EKS |
| [PaaS, custos e SLA](devops/custos-sla.md) | 10% | Gabriel | :material-check: pronto |
| Documentação (MkDocs) | 10% | todos | em andamento |

## Apresentação

Vídeo de 1 a 2 minutos com o projeto e os bottlenecks: veja [Apresentação](apresentacao.md).
