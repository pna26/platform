# CI/CD (GitHub Actions)

!!! info "Responsável: Leonardo"

Escolhemos **GitHub Actions** em vez de Jenkins: não precisa de servidor próprio rodando, os segredos
ficam na organização do GitHub e os resultados aparecem direto nos PRs e na aba Security.

## Desenho

Um único pipeline **reutilizável** fica no repositório `platform`
([`.github/workflows/service.yaml`](https://github.com/pna26/platform/blob/main/.github/workflows/service.yaml){target="_blank"})
e cada microsserviço só tem um arquivo pequeno que o chama. Uma correção no pipeline vale para todos os
serviços de uma vez.

``` mermaid
flowchart LR
    subgraph svc ["repo do serviço (push/PR)"]
        ci["ci.yaml<br/>(10 linhas)"]
    end
    subgraph platform ["pna26/platform"]
        reuse["service.yaml<br/>(workflow_call)"]
    end
    ci -->|uses| reuse
    reuse --> b["1. build + testes"]
    b --> s1["2. Trivy: código"]
    s1 --> s2["3. Trivy: imagem"]
    s2 --> gate{"portão de<br/>segurança"}
    gate -->|"push na main"| p["4. push multi-arch<br/>GHCR + ECR"]
    p --> d["5. deploy EKS<br/>kubectl apply + rollout"]
```

| Etapa | Quando | O que faz |
|---|---|---|
| 1. Build e testes | sempre | `mvn verify` (Java, depois do `mvn install` das interfaces `account`/`auth` da org) ou `pytest` (Python) |
| 2. Trivy no código | sempre | dependências, segredos, `Dockerfile` e `k8s/` ([detalhes](../bottlenecks/vulnerability-scanning.md)) |
| 3. Trivy na imagem | serviços | `docker build` local e varredura da imagem antes de publicar |
| Portão | sempre | falha com CVE CRITICAL corrigível ou segredo exposto |
| 4. Push | push na `main` | imagem `linux/amd64` + `linux/arm64` em `ghcr.io/pna26/<serviço>:sha-<commit>` e `:latest`; também no ECR quando houver credenciais da AWS (cria o repositório com *scan on push* se faltar) |
| 5. Deploy | push na `main` com `EKS_CLUSTER` definido | `aws eks update-kubeconfig`, troca a imagem `:latest` do `k8s/` pela do commit, `kubectl apply` e espera o `rollout status` |

Em **pull requests** só rodam as etapas 1–3: o PR mostra se compila, se os testes passam e se a
varredura acha algo, sem publicar nada.

## Como cada serviço usa

=== "gateway-service"

    ``` { .yaml .copy title=".github/workflows/ci.yaml" }
    --8<-- "api/gateway-service/.github/workflows/ci.yaml"
    ```

=== "account / auth (bibliotecas)"

    ``` { .yaml .copy title=".github/workflows/ci.yaml" }
    name: ci
    on:
      push:
        branches: [ main ]
      pull_request:
      workflow_dispatch:
    permissions:
      contents: read
      packages: write
      security-events: write
      id-token: write             # OIDC na AWS
    jobs:
      pipeline:
        uses: pna26/platform/.github/workflows/service.yaml@main
        with:
          service: account          # sem "image": só build, testes e scan
        secrets: inherit
    ```

=== "account-service / auth-service"

    ``` { .yaml .copy title=".github/workflows/ci.yaml" }
    name: ci
    on:
      push:
        branches: [ main ]
      pull_request:
      workflow_dispatch:
    permissions:
      contents: read
      packages: write
      security-events: write
      id-token: write             # OIDC na AWS
    jobs:
      pipeline:
        uses: pna26/platform/.github/workflows/service.yaml@main
        with:
          service: account
          image: account-service
          maven-deps: account       # auth-service: "account auth"
          aws-region: ${{ vars.AWS_REGION }}
          eks-cluster: ${{ vars.EKS_CLUSTER }}
          k8s-namespace: ${{ vars.K8S_NAMESPACE || 'store' }}
        secrets: inherit
    ```

=== "exchange-service (Python)"

    ``` { .yaml .copy title=".github/workflows/ci.yaml" }
    name: ci
    on:
      push:
        branches: [ main ]
      pull_request:
      workflow_dispatch:
    permissions:
      contents: read
      packages: write
      security-events: write
      id-token: write             # OIDC na AWS
    jobs:
      pipeline:
        uses: pna26/platform/.github/workflows/service.yaml@main
        with:
          service: exchange
          image: exchange-service
          language: python
          aws-region: ${{ vars.AWS_REGION }}
          eks-cluster: ${{ vars.EKS_CLUSTER }}
          k8s-namespace: ${{ vars.K8S_NAMESPACE || 'store' }}
        secrets: inherit
    ```

## Configuração da AWS (alinhar com o Nicolas)

O pipeline entra na AWS por **OIDC**: o job pede um token ao GitHub e assume uma role IAM, então
nenhuma chave de acesso fica guardada no GitHub. Por isso o `ci.yaml` de quem publica imagem precisa
de `id-token: write` em `permissions`. Os jobs do `service.yaml` não declaram permissões e herdam as
do `ci.yaml`: um reutilizável não pode ter mais permissão que quem o chama, e se ele pedisse
`id-token: write` todo repositório sem essa linha ficaria sem CI. Assim, sem a linha, o pipeline roda
igual, publica só no GHCR e avisa que pulou o ECR; o deploy no EKS falha com a mensagem do que falta.
As bibliotecas (`account`, `auth`) não publicam imagem e não precisam dela.

O deploy só liga quando estas variáveis existem na **organização** `pna26` (Settings → Secrets and
variables → Actions → Variables), assim nenhum repositório precisa mudar:

| Variável | Valor |
|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::637423273088:role/github-actions-pna26` |
| `AWS_REGION` | `us-east-2` |
| `K8S_NAMESPACE` | `store` |
| `EKS_CLUSTER` | `eks-store` (só depois que o cluster estiver no ar) |

A role confia nos repositórios da org (`repo:pna26@317011053/*`, ver [AWS](aws.md#problemas-encontrados)) e pode: push/pull no ECR (os repositórios
`gateway-service`, `auth-service`, `account-service` e `exchange-service` já são criados por fora) e
`eks:DescribeCluster`. Para o `kubectl apply` ela também precisa de uma **access entry** no cluster
com a política `AmazonEKSAdminPolicy` no namespace `store`, mais um `RoleBinding` para o papel `admin`
do namespace: sem ele o Kubernetes barra o `Role` do Prometheus que o `infra` aplica (detalhes em
[EKS](eks.md)).

!!! tip "Sem AWS"

    Sem `AWS_ROLE_ARN`, o pipeline continua fazendo build, testes, varredura e publica as imagens no
    **GHCR**. O EKS consegue puxar do GHCR desde que os pacotes estejam públicos.

## Evidência

### OIDC e ECR (10–13/11)

Runs na `main` depois do OIDC. Em todos, o job entrou na AWS sem nenhuma chave guardada no GitHub
(passo *Credenciais da AWS*), fez login no ECR e publicou a imagem no ECR e no GHCR. O job *deploy no
EKS* aparece como `skipped` porque a variável `EKS_CLUSTER` só existe com o cluster no ar.

| Serviço | Run | Data | Build, testes e Trivy | AWS por OIDC + push no ECR |
|---|---|---|---|---|
| gateway-service | [43455719598](https://github.com/pna26/gateway-service/actions/runs/43455719598){target="_blank"} | 13/11 | :material-check: | :material-check: `gateway-service:sha-0bb8f83` |
| account-service | [42867888940](https://github.com/pna26/account-service/actions/runs/42867888940){target="_blank"} | 10/11 | :material-check: | :material-check: |
| auth-service | [42867899239](https://github.com/pna26/auth-service/actions/runs/42867899239){target="_blank"} | 10/11 | :material-check: | :material-check: |
| notification-service | [42867914768](https://github.com/pna26/notification-service/actions/runs/42867914768){target="_blank"} | 10/11 | :material-check: | :material-check: |
| exchange-service | [42675957434](https://github.com/pna26/exchange-service/actions/runs/42675957434){target="_blank"} | 09/11 | :material-check: | antes do OIDC: só GHCR (vai ao ECR no próximo push) |

Trecho do log do gateway (passo *Build e push*):

``` text
pushing manifest for 637423273088.dkr.ecr.us-east-2.amazonaws.com/gateway-service:sha-0bb8f83@sha256:55a869db80fc... done
```

### Deploy no EKS (16/11)

Com o `eks-store` no ar e `EKS_CLUSTER=eks-store` na org, o pipeline reutilizável fez o caminho
inteiro sem nenhum comando manual de deploy: build, testes, Trivy, OIDC, push no ECR, troca da imagem
`:latest` pela do commit, `kubectl apply` e `rollout status`.

| Workflow | Run | Commit | Build, testes e Trivy | Deploy no EKS |
|---|---|---|---|---|
| account-service | [43856117521](https://github.com/pna26/account-service/actions/runs/43856117521){target="_blank"} | `b8d85c0` | :material-check: | :material-check: |
| auth-service | [43856123470](https://github.com/pna26/auth-service/actions/runs/43856123470){target="_blank"} | `81e9c12` | :material-check: | :material-check: |
| notification-service | [43856129429](https://github.com/pna26/notification-service/actions/runs/43856129429){target="_blank"} | `b3dc4c6` | :material-check: | :material-check: |
| `infra` (1ª tentativa) | [43855377825](https://github.com/pna26/platform/actions/runs/43855377825){target="_blank"} | — | — | :material-close: o Kubernetes barrou o `Role` do Prometheus (resolvido com um `RoleBinding`, ver [EKS](eks.md)) |
| `infra` (2ª tentativa) | [43855531385](https://github.com/pna26/platform/actions/runs/43855531385){target="_blank"} | — | — | :material-close: manifestos aplicados e Redis pronto, mas o `lb` não subiu |

Gateway e exchange não foram publicados nesse dia. O vermelho da 2ª tentativa do `infra` era um
problema do próprio `lb`:

``` text
deployment "redis" successfully rolled out
Waiting for deployment "lb" rollout to finish: 0 of 2 updated replicas are available...
error: timed out waiting for the condition
```

O Nginx resolvia o nome `gateway` só na inicialização e, sem o Service `gateway`, caía com *host not
found*. O `infra` parava no `lb` e nem chegava a conferir Prometheus e Grafana. **Correção:** o
`k8s/lb.yaml` passou a resolver o gateway em tempo de execução (`resolve`, ver
[Balanceamento de carga](../bottlenecks/load-balancing.md)): o `lb` fica pronto antes do gateway,
responde `502` enquanto ele não existe e começa a encaminhar sozinho quando o gateway sobe. Testado em
contêiner com o mesmo `nginx.conf`: sem gateway, `/lb/health` → `200` e `/` → `502`; o gateway sobe e
`/` passa a `200` sem reiniciar o Nginx.

!!! warning "Falta"

    O deploy do **gateway** e do **exchange** pelo CI, e o `infra` verde com a correção do `lb`, ficam
    para a próxima vez que o cluster subir: um *Run workflow* em cada um, com o link do run aqui.

## Outros workflows do `platform`

| Workflow | Gatilho | O que faz |
|---|---|---|
| `integration` | push, PR, toda segunda | Sobe a plataforma inteira com o compose (submódulos), roda o `smoke-test.sh` e o OWASP ZAP |
| `infra` | mudança em `k8s/` | Aplica no EKS o Nginx (`lb`), Redis, Kafka, Mailpit, Prometheus e Grafana |
| `docs` | push na `main` | Publica esta documentação no GitHub Pages |

## Pipeline completo

??? example "`.github/workflows/service.yaml`"

    ``` { .yaml .copy linenums="1" }
    --8<-- ".github/workflows/service.yaml"
    ```
