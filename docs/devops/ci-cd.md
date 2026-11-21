# CI/CD (GitHub Actions)

!!! info "Owner: Leonardo"

We chose **GitHub Actions** over Jenkins: it needs no self-hosted server, secrets
live in the GitHub organization and results show up directly in PRs and in the Security tab.

## Design { #desenho }

A single **reusable** pipeline lives in the `platform` repository
([`.github/workflows/service.yaml`](https://github.com/pna26/platform/blob/main/.github/workflows/service.yaml){target="_blank"})
and each microservice only has a small file that calls it. A fix to the pipeline applies to every
service at once.

``` mermaid
flowchart LR
    subgraph svc ["service repo (push/PR)"]
        ci["ci.yaml<br/>(10 lines)"]
    end
    subgraph platform ["pna26/platform"]
        reuse["service.yaml<br/>(workflow_call)"]
    end
    ci -->|uses| reuse
    reuse --> b["1. build + tests"]
    b --> s1["2. Trivy: code"]
    s1 --> s2["3. Trivy: image"]
    s2 --> gate{"security<br/>gate"}
    gate -->|"push to main"| p["4. multi-arch push<br/>GHCR + ECR"]
    p --> d["5. EKS deploy<br/>kubectl apply + rollout"]
```

| Stage | When | What it does |
|---|---|---|
| 1. Build and tests | always | `mvn verify` (Java, after `mvn install` of the org's `account`/`auth` interfaces) or `pytest` (Python) |
| 2. Trivy on code | always | dependencies, secrets, `Dockerfile` and `k8s/` ([details](../bottlenecks/vulnerability-scanning.md)) |
| 3. Trivy on image | services | local `docker build` and image scan before publishing |
| Gate | always | fails on a fixable CRITICAL CVE or an exposed secret |
| 4. Push | push to `main` | `linux/amd64` + `linux/arm64` image to `ghcr.io/pna26/<serviço>:sha-<commit>` and `:latest`; also to ECR when AWS credentials are available (creates the repository with *scan on push* if missing) |
| 5. Deploy | push to `main` with `EKS_CLUSTER` set | `aws eks update-kubeconfig`, swaps the `:latest` image in `k8s/` for the commit's, `kubectl apply` and waits for `rollout status` |

On **pull requests** only stages 1–3 run: the PR shows whether it compiles, whether tests pass and whether the
scan finds anything, without publishing anything.

## How each service uses it { #como-cada-servico-usa }

=== "gateway-service"

    ``` { .yaml .copy title=".github/workflows/ci.yaml" }
    --8<-- "api/gateway-service/.github/workflows/ci.yaml"
    ```

=== "account / auth (libraries)"

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

## AWS configuration (align with Nicolas) { #configuracao-da-aws-alinhar-com-o-nicolas }

The pipeline signs in to AWS via **OIDC**: the job requests a token from GitHub and assumes an IAM role, so
no access key is stored in GitHub. That is why the `ci.yaml` of any repo that publishes an image needs
`id-token: write` under `permissions`. The `service.yaml` jobs declare no permissions and inherit those
of `ci.yaml`: a reusable workflow cannot have more permissions than its caller, and if it requested
`id-token: write`, every repository without that line would lose CI. So, without the line, the pipeline runs
the same, publishes only to GHCR and warns that it skipped ECR; the EKS deploy fails with a message stating what is missing.
The libraries (`account`, `auth`) do not publish an image and do not need it.

Deploy only turns on when these variables exist in the `pna26` **organization** (Settings → Secrets and
variables → Actions → Variables), so no repository needs to change:

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::637423273088:role/github-actions-pna26` |
| `AWS_REGION` | `us-east-2` |
| `K8S_NAMESPACE` | `store` |
| `EKS_CLUSTER` | `eks-store` (only once the cluster is up) |

The role trusts the org's repositories (`repo:pna26@317011053/*`, see [AWS](aws.md#problemas-encontrados)) and can: push/pull to ECR (the
`gateway-service`, `auth-service`, `account-service` and `exchange-service` repositories are created separately) and
`eks:DescribeCluster`. For `kubectl apply` it also needs an **access entry** on the cluster
with the `AmazonEKSAdminPolicy` policy on the `store` namespace, plus a `RoleBinding` to the namespace's `admin`
role: without it Kubernetes blocks the Prometheus `Role` that `infra` applies (details in
[EKS](eks.md)).

!!! tip "Without AWS"

    Without `AWS_ROLE_ARN`, the pipeline still builds, tests, scans and publishes the images to
    **GHCR**. EKS can pull from GHCR as long as the packages are public.

## Evidence { #evidencia }

### OIDC and ECR (10–13/11) { #oidc-e-ecr-101311 }

Runs on `main` after OIDC. In all of them, the job signed in to AWS with no key stored in GitHub
(*Credenciais da AWS* step), logged in to ECR and published the image to ECR and GHCR. The *deploy no
EKS* job shows as `skipped` because the `EKS_CLUSTER` variable only exists while the cluster is up.

| Service | Run | Date | Build, tests and Trivy | AWS via OIDC + push to ECR |
|---|---|---|---|---|
| gateway-service | [43455719598](https://github.com/pna26/gateway-service/actions/runs/43455719598){target="_blank"} | 13/11 | :material-check: | :material-check: `gateway-service:sha-0bb8f83` |
| account-service | [42867888940](https://github.com/pna26/account-service/actions/runs/42867888940){target="_blank"} | 10/11 | :material-check: | :material-check: |
| auth-service | [42867899239](https://github.com/pna26/auth-service/actions/runs/42867899239){target="_blank"} | 10/11 | :material-check: | :material-check: |
| notification-service | [42867914768](https://github.com/pna26/notification-service/actions/runs/42867914768){target="_blank"} | 10/11 | :material-check: | :material-check: |
| exchange-service | [42675957434](https://github.com/pna26/exchange-service/actions/runs/42675957434){target="_blank"} | 09/11 | :material-check: | before OIDC: GHCR only (goes to ECR on the next push) |

Excerpt from the gateway log (*Build e push* step):

``` text
pushing manifest for 637423273088.dkr.ecr.us-east-2.amazonaws.com/gateway-service:sha-0bb8f83@sha256:55a869db80fc... done
```

### EKS deploy (16/11) { #deploy-no-eks-1611 }

With `eks-store` up and `EKS_CLUSTER=eks-store` set in the org, the reusable pipeline ran the whole
path without any manual deploy command: build, tests, Trivy, OIDC, push to ECR, swapping the
`:latest` image for the commit's, `kubectl apply` and `rollout status`.

| Workflow | Run | Commit | Build, tests and Trivy | EKS deploy |
|---|---|---|---|---|
| account-service | [43856117521](https://github.com/pna26/account-service/actions/runs/43856117521){target="_blank"} | `b8d85c0` | :material-check: | :material-check: |
| auth-service | [43856123470](https://github.com/pna26/auth-service/actions/runs/43856123470){target="_blank"} | `81e9c12` | :material-check: | :material-check: |
| notification-service | [43856129429](https://github.com/pna26/notification-service/actions/runs/43856129429){target="_blank"} | `b3dc4c6` | :material-check: | :material-check: |
| `infra` (1st attempt) | [43855377825](https://github.com/pna26/platform/actions/runs/43855377825){target="_blank"} | — | — | :material-close: Kubernetes blocked Prometheus's `Role` (solved with a `RoleBinding`, see [EKS](eks.md)) |
| `infra` (2nd attempt) | [43855531385](https://github.com/pna26/platform/actions/runs/43855531385){target="_blank"} | — | — | :material-close: manifests applied and Redis ready, but `lb` did not come up |

Gateway and exchange were not published that day. The red on `infra`'s 2nd attempt was a
problem with `lb` itself:

``` text
deployment "redis" successfully rolled out
Waiting for deployment "lb" rollout to finish: 0 of 2 updated replicas are available...
error: timed out waiting for the condition
```

Nginx resolved the `gateway` name only at startup and, without the `gateway` Service, crashed with *host not
found*. `infra` stopped at `lb` and never got to check Prometheus and Grafana. **Fix:**
`k8s/lb.yaml` now resolves the gateway at runtime (`resolve`, see
[Load balancing](../bottlenecks/load-balancing.md)): `lb` becomes ready before the gateway,
returns `502` while it does not exist and starts forwarding on its own once the gateway comes up. Tested in a
container with the same `nginx.conf`: without gateway, `/lb/health` → `200` and `/` → `502`; the gateway comes up and
`/` turns `200` without restarting Nginx.

!!! info "Next time the cluster comes up"

    The CI deploy of **gateway** and **exchange**, and a green `infra` with the `lb` fix, are left
    for the next time the cluster comes up: one *Run workflow* for each, with the run link here.

## Other `platform` workflows { #outros-workflows-do-platform }

| Workflow | Trigger | What it does |
|---|---|---|
| `integration` | push, PR, every Monday | Brings up the whole platform with compose (submodules), runs `smoke-test.sh` and OWASP ZAP |
| `infra` | change in `k8s/` | Applies Nginx (`lb`), Redis, Kafka, Mailpit, Prometheus and Grafana to EKS |
| `docs` | push to `main` | Publishes this documentation to GitHub Pages |

## Full pipeline { #pipeline-completo }

??? example "`.github/workflows/service.yaml`"

    ``` { .yaml .copy linenums="1" }
    --8<-- ".github/workflows/service.yaml"
    ```
