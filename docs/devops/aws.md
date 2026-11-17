# AWS

!!! info "Responsável: Nicolas"

Esta página cobre a **base da conta AWS**: região, alerta de custo, acesso pela linha de comando,
os repositórios de imagem (ECR) e a role que o GitHub Actions usa para entrar na AWS **sem chave de
acesso**. O cluster e o banco ficam em [EKS](eks.md); a rede (VPC, sub-redes, NAT) é criada junto com
o cluster pelo `eksctl`.

``` mermaid
flowchart LR
    subgraph gh[GitHub · org pna26]
        ci[GitHub Actions<br/>service.yaml]
    end
    subgraph aws[Conta 637423273088 · us-east-2]
        oidc[provedor OIDC<br/>token.actions.githubusercontent.com]
        role[role IAM<br/>github-actions-pna26]
        ecr[(ECR<br/>5 repositórios)]
        eks[EKS eks-store]
        budget[budget pna26-mensal<br/>US$ 20/mês]
    end
    ci -->|"1. token OIDC assinado pelo GitHub"| oidc
    oidc -->|"2. confere aud e sub"| role
    role -->|"3. credenciais temporárias (1 h)"| ci
    ci -->|"push da imagem"| ecr
    ci -->|"kubectl apply (access entry)"| eks
    eks -.->|"pull"| ecr
```

## Conta e região

| Item | Valor |
|---|---|
| Conta | `637423273088` (fornecida pelo curso) |
| Acesso das pessoas | IAM Identity Center (SSO), papel `AdministratorAccess` |
| Região de tudo | **`us-east-2` (Ohio)** |
| Ferramentas | AWS CLI 2, `eksctl`, `kubectl` |

**Por que `us-east-2`:** tem todos os serviços que usamos (EKS, RDS, ECR) com os mesmos preços de
`us-east-1`, e uma região só evita recurso esquecido em outro lugar gerando custo. Toda a configuração
(cluster, ECR, variável `AWS_REGION` do CI) usa essa região.

### Roteiro do curso e o que fizemos

O [roteiro de AWS do curso](https://insper.github.io/platform/2026.2/exercises/project/aws/){target="_blank"}
sugere criar um usuário IAM, entrar no console, gerar uma *access key* e configurar o AWS CLI com ela.
Seguimos os mesmos passos, trocando a chave fixa por **login SSO**:

| Passo do curso | O que fizemos | Por quê |
|---|---|---|
| 1. Criar usuário | a conta do curso já vem com IAM Identity Center; cada pessoa entra com o seu usuário SSO | não precisou criar usuário IAM |
| 2. Entrar no console | portal do SSO → conta `637423273088` → papel `AdministratorAccess` | — |
| 3. Criar *access key* | **não criamos** | uma *access key* vale até alguém revogá-la; se vazar (num commit, num print), dá acesso à conta. O SSO gera credenciais temporárias, que expiram sozinhas |
| 4. Configurar o AWS CLI | `aws configure sso` (perfil `default`, região `us-east-2`) | o CLI usa as mesmas credenciais temporárias do SSO |

O mesmo princípio vale para o CI: em vez de guardar uma *access key* no GitHub, ele usa a
[role OIDC](#role-do-github-actions-oidc).

``` bash
aws configure sso          # uma vez: portal do SSO, conta, papel e região us-east-2
aws sso login              # quando a sessão expira
aws sts get-caller-identity
```

## Alerta de custo

O primeiro passo, antes de criar qualquer recurso, foi um **budget** no AWS Budgets:

| Configuração | Valor |
|---|---|
| Nome | `pna26-mensal` |
| Limite | US$ 20 por mês (custo real) |
| Alertas por e-mail | 85% e 100% do gasto **real**; 100% do gasto **previsto** para o mês |

O budget **só avisa, não bloqueia**: a AWS continua cobrando depois do limite. Por isso o ambiente do
EKS é apagado quando não está em uso (ver [custos do EKS](eks.md#custos)). O alerta de previsão é o
mais útil: ele dispara no começo do mês se o ritmo de gasto for passar do limite, antes de o dinheiro
ser gasto.

## ECR

Um repositório por imagem, com o mesmo nome que o pipeline usa:

| Repositório | Imagem |
|---|---|
| `gateway-service` | gateway (Java) |
| `auth-service` | auth (Java) |
| `account-service` | account (Java) |
| `exchange-service` | exchange (Python) |
| `notification-service` | consumidor da [mensageria](../bottlenecks/messaging.md) |

Endereço: `637423273088.dkr.ecr.us-east-2.amazonaws.com/<repositório>:<tag>`.

| Decisão | Motivo |
|---|---|
| ECR (e não só o GHCR) para o EKS | Os pacotes do GHCR da org são privados. Os nós do EKS já têm permissão de leitura no ECR da própria conta, sem precisar de segredo de *pull*. |
| **Scan on push** ligado | O ECR varre cada imagem enviada em busca de CVEs, uma segunda opinião além do Trivy do [CI](ci-cd.md). |
| Nomes `*-service` | São os nomes que o `service.yaml` já usava; assim nenhum pipeline precisou mudar. |

## Role do GitHub Actions (OIDC)

O CI precisa enviar imagens ao ECR e fazer deploy no EKS. A forma comum seria criar um usuário IAM e
guardar a **chave de acesso** dele como secret do GitHub. Não fizemos isso: uma chave fixa vale para
sempre até alguém revogá-la, e se vazar (log, fork, repositório público) dá acesso à conta.

Em vez disso, a AWS **confia no GitHub** como provedor de identidade (OIDC):

1. o job pede ao GitHub um **token OIDC** (por isso o `ci.yaml` precisa de `id-token: write`); o token
   é assinado pelo GitHub e diz de qual repositório, branch e workflow ele veio;
2. a action `aws-actions/configure-aws-credentials` troca esse token por **credenciais temporárias**
   (`sts:AssumeRoleWithWebIdentity`) da role `github-actions-pna26`;
3. a AWS só aceita se as **condições** da role baterem; as credenciais expiram em 1 hora.

| Peça | Valor |
|---|---|
| Provedor OIDC | `token.actions.githubusercontent.com`, audiência `sts.amazonaws.com` |
| Role | `arn:aws:iam::637423273088:role/github-actions-pna26` |
| Quem pode assumir | tokens com `sub` começando por `repo:pna26@317011053/` (qualquer repositório da org) |
| O que pode fazer | login no ECR; push/pull **só** nos 5 repositórios acima; `eks:DescribeCluster` |
| Dentro do cluster | access entry com `AmazonEKSAdminPolicy` + `RoleBinding` para o papel `admin`, **só no namespace `store`** (ver [EKS](eks.md#deploy-pelo-cicd)) |

=== "Confiança (quem pode assumir)"

    ``` { .json .copy linenums="1" }
    --8<-- "setup/aws/github-actions-trust.json"
    ```

=== "Permissões (o que pode fazer)"

    ``` { .json .copy linenums="1" }
    --8<-- "setup/aws/github-actions-permissions.json"
    ```

As permissões seguem o **menor privilégio**: a role não cria nem apaga recursos da AWS, não lê outros
repositórios do ECR e, no Kubernetes, só mexe no namespace `store`. Criar o cluster, o RDS e os
Secrets continua sendo um passo manual, feito com o login SSO de uma pessoa.

O ARN da role, a região e o namespace ficam em **variáveis da organização** do GitHub
(`AWS_ROLE_ARN`, `AWS_REGION`, `K8S_NAMESPACE` e, com o cluster no ar, `EKS_CLUSTER`), descritas em
[CI/CD](ci-cd.md#configuracao-da-aws-alinhar-com-o-nicolas). Não são segredos: sem um token OIDC
de um repositório da org, o ARN não serve para nada.

### Como foi criado

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

## Problemas encontrados

| Problema | Causa | Solução |
|---|---|---|
| CI falha com `Not authorized to perform sts:AssumeRoleWithWebIdentity` (10/11/2026, primeiro push depois de ligar o OIDC) | os repositórios da org usam o **subject imutável** do GitHub: o `sub` do token é `repo:pna26@317011053/account-service@1443117992:ref:refs/heads/main` (com os ids numéricos da org e do repositório), e a condição da role era `repo:pna26/*` | condição trocada para `repo:pna26@317011053/*`. É até mais seguro: o id numérico não muda, então outra org que um dia use o nome `pna26` não casa com a regra |
| `docker login` no ECR falha no Windows | token SSO maior que o limite do Gerenciador de Credenciais do Windows | `docker-credential-ecr-login` (detalhes em [EKS](eks.md#problemas-encontrados)) |

O formato do `sub` de um repositório aparece em
`gh api repos/pna26/<repositório>/actions/oidc/customization/sub`.

## Melhorias possíveis

| Melhoria | Efeito |
|---|---|
| Restringir a confiança a `...:ref:refs/heads/main` | só pushes na `main` assumiriam a role (hoje, um workflow em outro branch de um repositório da org também consegue) |
| Ligar *secret scanning* e *push protection* na org | o GitHub bloqueia um push que contenha uma chave da AWS por engano |
| Uma role por repositório | o CI do exchange não conseguiria enviar imagem para o repositório do account |
