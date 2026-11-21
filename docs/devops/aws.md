# AWS

!!! info "Owner: Nicolas"

This page covers the **AWS account foundation**: region, cost alert, command-line access,
the image repositories (ECR) and the role GitHub Actions uses to sign in to AWS **without an access
key**. The cluster and the database are covered in [EKS](eks.md); the network (VPC, subnets, NAT) is created together with
the cluster by `eksctl`.

``` mermaid
flowchart LR
    subgraph gh[GitHub · org pna26]
        ci[GitHub Actions<br/>service.yaml]
    end
    subgraph aws[Account 637423273088 · us-east-2]
        oidc[OIDC provider<br/>token.actions.githubusercontent.com]
        role[IAM role<br/>github-actions-pna26]
        ecr[(ECR<br/>5 repositories)]
        eks[EKS eks-store]
        budget[budget pna26-mensal<br/>US$ 20/month]
    end
    ci -->|"1. OIDC token signed by GitHub"| oidc
    oidc -->|"2. checks aud and sub"| role
    role -->|"3. temporary credentials (1 h)"| ci
    ci -->|"image push"| ecr
    ci -->|"kubectl apply (access entry)"| eks
    eks -.->|"pull"| ecr
```

## Account and region { #conta-e-regiao }

| Item | Value |
|---|---|
| Account | `637423273088` (provided by the course) |
| People's access | IAM Identity Center (SSO), `AdministratorAccess` role |
| Region for everything | **`us-east-2` (Ohio)** |
| Tools | AWS CLI 2, `eksctl`, `kubectl` |

**Why `us-east-2`:** it has every service we use (EKS, RDS, ECR) at the same prices as
`us-east-1`, and a single region avoids forgotten resources elsewhere generating costs. All configuration
(cluster, ECR, CI's `AWS_REGION` variable) uses this region.

### Course guide and what we did { #roteiro-do-curso-e-o-que-fizemos }

The [course's AWS guide](https://insper.github.io/platform/2026.2/exercises/project/aws/){target="_blank"}
suggests creating an IAM user, signing in to the console, generating an *access key* and configuring the AWS CLI with it.
We followed the same steps, replacing the long-lived key with **SSO login**:

| Course step | What we did | Why |
|---|---|---|
| 1. Create user | the course account already comes with IAM Identity Center; each person signs in with their own SSO user | no IAM user needed |
| 2. Sign in to the console | SSO portal → account `637423273088` → `AdministratorAccess` role | — |
| 3. Create *access key* | **we did not create one** | an *access key* stays valid until someone revokes it; if it leaks (in a commit, in a screenshot), it grants access to the account. SSO issues temporary credentials that expire on their own |
| 4. Configure the AWS CLI | `aws configure sso` (`default` profile, region `us-east-2`) | the CLI uses the same temporary SSO credentials |

The same principle applies to CI: instead of storing an *access key* in GitHub, it uses the
[OIDC role](#role-do-github-actions-oidc).

``` bash
aws configure sso          # uma vez: portal do SSO, conta, papel e região us-east-2
aws sso login              # quando a sessão expira
aws sts get-caller-identity
```

## Cost alert { #alerta-de-custo }

The first step, before creating any resource, was a **budget** in AWS Budgets:

| Setting | Value |
|---|---|
| Name | `pna26-mensal` |
| Limit | US$ 20 per month (actual cost) |
| Email alerts | 85% and 100% of **actual** spend; 100% of **forecasted** spend for the month |

The budget **only alerts, it does not block**: AWS keeps charging after the limit. That is why the EKS
environment is deleted when not in use (see [EKS costs](eks.md#custos)). The forecast alert is the
most useful one: it fires early in the month if the spending pace is going to exceed the limit, before the money
is spent.

## ECR { #ecr }

One repository per image, with the same name the pipeline uses:

| Repository | Image |
|---|---|
| `gateway-service` | gateway (Java) |
| `auth-service` | auth (Java) |
| `account-service` | account (Java) |
| `exchange-service` | exchange (Python) |
| `notification-service` | [messaging](../bottlenecks/messaging.md) consumer |

Address: `637423273088.dkr.ecr.us-east-2.amazonaws.com/<repositório>:<tag>`.

| Decision | Reason |
|---|---|
| ECR (not just GHCR) for EKS | The org's GHCR packages are private. EKS nodes already have read permission on the account's own ECR, with no *pull* secret needed. |
| **Scan on push** enabled | ECR scans every pushed image for CVEs, a second opinion on top of [CI](ci-cd.md)'s Trivy. |
| `*-service` names | These are the names `service.yaml` already used; so no pipeline had to change. |

## GitHub Actions role (OIDC) { #role-do-github-actions-oidc }

CI needs to push images to ECR and deploy to EKS. The usual approach would be to create an IAM user and
store its **access key** as a GitHub secret. We did not do that: a long-lived key is valid
forever until someone revokes it, and if it leaks (log, fork, public repository) it grants access to the account.

Instead, AWS **trusts GitHub** as an identity provider (OIDC):

1. the job requests an **OIDC token** from GitHub (that is why `ci.yaml` needs `id-token: write`); the token
   is signed by GitHub and states which repository, branch and workflow it came from;
2. the `aws-actions/configure-aws-credentials` action exchanges that token for **temporary credentials**
   (`sts:AssumeRoleWithWebIdentity`) of the `github-actions-pna26` role;
3. AWS only accepts it if the role's **conditions** match; the credentials expire in 1 hour.

| Component | Value |
|---|---|
| OIDC provider | `token.actions.githubusercontent.com`, audience `sts.amazonaws.com` |
| Role | `arn:aws:iam::637423273088:role/github-actions-pna26` |
| Who can assume it | tokens whose `sub` starts with `repo:pna26@317011053/` (any repository in the org) |
| What it can do | ECR login; push/pull **only** to the 5 repositories above; `eks:DescribeCluster` |
| Inside the cluster | access entry with `AmazonEKSAdminPolicy` + `RoleBinding` to the `admin` role, **only in the `store` namespace** (see [EKS](eks.md#deploy-pelo-cicd)) |

=== "Trust (who can assume it)"

    ``` { .json .copy linenums="1" }
    --8<-- "setup/aws/github-actions-trust.json"
    ```

=== "Permissions (what it can do)"

    ``` { .json .copy linenums="1" }
    --8<-- "setup/aws/github-actions-permissions.json"
    ```

Permissions follow **least privilege**: the role neither creates nor deletes AWS resources, does not read other
ECR repositories and, in Kubernetes, only touches the `store` namespace. Creating the cluster, RDS and the
Secrets remains a manual step, done with a person's SSO login.

The role ARN, region and namespace live in GitHub **organization variables**
(`AWS_ROLE_ARN`, `AWS_REGION`, `K8S_NAMESPACE` and, with the cluster up, `EKS_CLUSTER`), described in
[CI/CD](ci-cd.md#configuracao-da-aws-alinhar-com-o-nicolas). They are not secrets: without an OIDC token
from an org repository, the ARN is useless.

### How it was created { #como-foi-criado }

``` bash
# provedor OIDC do GitHub (uma vez por conta)
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com

# role com a política de confiança e a de permissões
aws iam create-role --role-name github-actions-pna26 \
  --assume-role-policy-document file://setup/aws/github-actions-trust.json
aws iam put-role-policy --role-name github-actions-pna26 \
  --policy-name ecr-push-eks-describe \
  --policy-document file://setup/aws/github-actions-permissions.json

# repositórios do ECR, com varredura a cada push
for r in gateway-service auth-service account-service exchange-service notification-service; do
  aws ecr create-repository --repository-name $r --region us-east-2 \
    --image-scanning-configuration scanOnPush=true
done
```

## Issues encountered { #problemas-encontrados }

| Issue | Cause | Solution |
|---|---|---|
| CI fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity` (10/11/2026, first push after enabling OIDC) | the org's repositories use GitHub's **immutable subject**: the token's `sub` is `repo:pna26@317011053/account-service@1443117992:ref:refs/heads/main` (with the numeric ids of the org and repository), and the role's condition was `repo:pna26/*` | condition changed to `repo:pna26@317011053/*`. It is even safer: the numeric id does not change, so another org that someday uses the name `pna26` does not match the rule |
| `docker login` to ECR fails on Windows | SSO token larger than the Windows Credential Manager limit | `docker-credential-ecr-login` (details in [EKS](eks.md#problemas-encontrados)) |

A repository's `sub` format can be seen with
`gh api repos/pna26/<repositório>/actions/oidc/customization/sub`.

## Possible improvements { #melhorias-possiveis }

| Improvement | Effect |
|---|---|
| Restrict trust to `...:ref:refs/heads/main` | only pushes to `main` would assume the role (today, a workflow on another branch of an org repository can too) |
| Enable *secret scanning* and *push protection* in the org | GitHub blocks a push that accidentally contains an AWS key |
| One role per repository | exchange's CI would not be able to push an image to account's repository |
