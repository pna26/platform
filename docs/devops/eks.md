# EKS

!!! info "Owner: Nicolas"

The platform runs on an **Amazon EKS** cluster (`eks-store`, region `us-east-2`), with the
Account database on **Amazon RDS for PostgreSQL**. Everything lives in the `store` namespace. The cluster is created with
`eksctl` from a versioned file ([`setup/eks-store.yaml`](https://github.com/pna26/platform/blob/main/setup/eks-store.yaml){target="_blank"})
and **deleted when not in use**, because it is the most expensive item in the project (see [Costs](#custos)).

## Architecture { #arquitetura }

``` mermaid
flowchart LR
    user([internet]) --> elb[AWS ELB<br/>Service lb · LoadBalancer]
    subgraph vpc[VPC 10.20.0.0/16 · us-east-2a and 2b]
        subgraph pub[public subnets]
            elb
            nat[NAT gateway]
        end
        subgraph priv[private subnets · 2 t3.medium nodes]
            lb[lb · Nginx ×2] --> gw[gateway ×2]
            gw <--> redis[(Redis)]
            gw --> auth & account & exchange[exchange ×2]
            auth --> account
            prom[Prometheus] --> graf[Grafana]
        end
        rds[(RDS PostgreSQL 17<br/>db.t4g.micro)]
    end
    elb --> lb
    account -->|5432| rds
    exchange --> nat --> api([AwesomeAPI])
    priv -.->|pull| ecr[(ECR)]
```

| Component | Choice | Why |
|---|---|---|
| Kubernetes version | 1.37 | EKS default version, in standard support until 11/2027 (versions in extended support cost 6× more per hour) |
| Nodes | 2× `t3.medium` (2 vCPU, 4 GiB), managed node group | memory requests add up to ~2.6 GiB; 2× `t3.small` (~2.9 GiB usable, 11 pods per node) left no headroom for rolling updates or the load test |
| Network | eksctl's own VPC, 2 zones, nodes **in private subnets only** (no public IP) | nodes are not exposed; only the ELB, in the public subnets, receives internet traffic |
| Internet egress | 1 NAT gateway | nodes need to pull images from ECR and exchange calls AwesomeAPI; one NAT per zone would double the cost |
| Cluster access | `authenticationMode: API` (access entries) | Kubernetes permissions granted to AWS roles without editing the `aws-auth` ConfigMap |
| Images | ECR (`637423273088.dkr.ecr.us-east-2.amazonaws.com/<serviço>-service`) | GHCR packages are private; nodes already have read permission on ECR |
| Database | RDS PostgreSQL 17.11, `db.t4g.micro`, 20 GB gp3 | the course's EKS page asks for RDS or DynamoDB; 20 GB is the RDS minimum |

### Service exposure { #exposicao-dos-servicos }

Only `lb` is `type: LoadBalancer`. Gateway, auth, account and exchange are `ClusterIP`: they are reachable
only from inside the cluster, by the names `gateway`, `auth`, `account` and `exchange` on port 8080. These are the
same names as in the compose file, so the Feign clients (`http://account:8080`, `http://auth:8080`) and
`nginx.conf` work unchanged.

## Manifests { #manifestos }

Each service has its own `k8s/k8s.yaml` in its repository (ConfigMap, Deployment and Service), and the
shared infra (lb, Redis, Prometheus, Grafana) lives in `platform/k8s/`. Common patterns:

- label `app: <serviço>`, used by the Service `selector` and by the pipeline's `kubectl rollout`;
- CPU and memory `requests`/`limits` (Java uses `-XX:MaxRAMPercentage=75` to respect the limit);
- non-root container (`runAsNonRoot`, uid 1001), no privilege escalation, read-only file
  system and an `emptyDir` at `/tmp`;
- `readinessProbe`/`livenessProbe` (account and auth use a TCP probe on 8080, because they have no Actuator);
- image ending in `/<serviço>-service:latest`: the pipeline replaces it with the commit tag (`sha-xxxxxxx`).

### Secrets { #segredos }

No secret is stored in Git. The pipeline runs `kubectl apply -f k8s/` on every deploy, so an example
Secret inside `k8s/` would overwrite the real one. Secrets are created once per cluster:

| Secret | Keys | Used by |
|---|---|---|
| `account-secrets` | `DATABASE_USERNAME`, `DATABASE_PASSWORD` | account (`secretRef`) |
| `auth-secrets` | `JWT_SECRET_KEY` | auth (`secretRef`) |
| `notification-secrets` | `DATABASE_USERNAME`, `DATABASE_PASSWORD` | notification (`secretRef`), with the same RDS user |
| `grafana-admin` | `password` | Grafana (created by the `infra` workflow) |

The database host, port and name are not secrets and live in the `account-configmap` ConfigMap
(`DATABASE_HOST` = RDS endpoint).

## RDS PostgreSQL { #rds-postgresql }

| Setting | Value |
|---|---|
| Instance | `rds-store`, `db.t4g.micro`, single-AZ |
| Storage | 20 GB gp3, encrypted, **storage autoscaling disabled** |
| Network | subnet group `rds-store` (private subnets only), **no public access** |
| Firewall | security group `rds-store`: ingress **only on port 5432 and only from the EKS nodes' security group** (no IP range allowed) |
| Backup | 1-day retention, no deletion protection (the environment is recreated often) |
| Schema | created by account's Flyway on first boot (`accounts`) |

The endpoint is `rds-store.<id-da-conta>.us-east-2.rds.amazonaws.com`: it depends only on the instance
name, account and region, so it is the same every time RDS is recreated with the name `rds-store`.
The password is random and exists only in RDS and in the `account-secrets` Secret.

## Deploy via CI/CD { #deploy-pelo-cicd }

The reusable pipeline ([CI/CD](ci-cd.md)) deploys on every push to `main` when the organization
variable `EKS_CLUSTER` is set: `aws eks update-kubeconfig` → swap the image for the commit's →
`kubectl apply -n store -f k8s/` → `kubectl rollout status`.

GitHub Actions signs in to AWS through the OIDC role `github-actions-pna26` (see [AWS](aws.md)). Inside the
cluster, it has an **access entry** with the `AmazonEKSAdminPolicy` access policy **scoped to the
`store` namespace**: it can create and update Deployments, Services, ConfigMaps, Secrets and also
`Role`/`RoleBinding` there, and nothing outside it. `AmazonEKSEditPolicy` is not enough: it follows the Kubernetes
`edit` role, which does not allow touching roles, and the `infra` workflow applies Prometheus's `Role`/`RoleBinding`.
Since access is limited to the namespace, `store` is created by hand (`kubectl create namespace
store`, in step 1 below).

The access policy alone **was not enough either** (found during the 16/11 deploy). When creating a `Role`,
Kubernetes checks whether the creator already holds every permission it is granting (privilege
escalation protection). That check looks only at Kubernetes' own RBAC and does not see the permissions that come
from the AWS access policy. That is why the access entry also puts the role in the `store-deployers` group, and a
`RoleBinding` grants that group the `admin` role **only in the `store` namespace**. The scope stays the same,
now granted through native RBAC.

``` bash
aws eks create-access-entry --cluster-name eks-store --region us-east-2 \
  --principal-arn arn:aws:iam::637423273088:role/github-actions-pna26
aws eks associate-access-policy --cluster-name eks-store --region us-east-2 \
  --principal-arn arn:aws:iam::637423273088:role/github-actions-pna26 \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminPolicy \
  --access-scope type=namespace,namespaces=store
# RBAC nativo: sem isto o infra não consegue criar o Role do Prometheus (escalada de privilégio)
aws eks update-access-entry --cluster-name eks-store --region us-east-2 \
  --principal-arn arn:aws:iam::637423273088:role/github-actions-pna26 --kubernetes-groups store-deployers
kubectl create rolebinding ci-store-admin -n store --clusterrole=admin --group=store-deployers
# conferência: "yes" no store e "no" fora dele
kubectl auth can-i create roles -n store   --as=ci --as-group=store-deployers
kubectl auth can-i create roles -n default --as=ci --as-group=store-deployers
```

!!! success "Automatic deploy proven (16/11/2026)"

    With the cluster up and the `EKS_CLUSTER=eks-store` variable created in the organization, CI deployed
    Nicolas's three services on its own: build, tests, Trivy, push to ECR, `kubectl apply` and
    `rollout status`, all green.

    | Workflow | Run | Result |
    |---|---|---|
    | `infra` (1st attempt) | [43855377825](https://github.com/pna26/platform/actions/runs/43855377825){target="_blank"} | failed: Kubernetes blocked the creation of Prometheus's `Role` (see above) |
    | `infra` (after the `RoleBinding`) | [43855531385](https://github.com/pna26/platform/actions/runs/43855531385){target="_blank"} | Redis, Kafka, Mailpit, Prometheus and Grafana up; Prometheus's `Role` created. Only `lb` did not become ready, because it waits for the `gateway` Service |
    | `account-service` | [43856117521](https://github.com/pna26/account-service/actions/runs/43856117521){target="_blank"} | deployed to EKS ✔ |
    | `auth-service` | [43856123470](https://github.com/pna26/auth-service/actions/runs/43856123470){target="_blank"} | deployed to EKS ✔ |
    | `notification-service` | [43856129429](https://github.com/pna26/notification-service/actions/runs/43856129429){target="_blank"} | deployed to EKS ✔ |

    Gateway and exchange were not published that day: each owner runs their service's CI on the day of the load
    test.

## Environment lifecycle { #ciclo-de-vida-do-ambiente }

All commands run from the root of `platform`, with the AWS CLI logged in (`aws sso login`).

### Create (~25 min) { #criar-25-min }

``` bash
# 1. cluster, VPC, NAT e nós (~15 min); já configura o kubectl e instala o metrics-server
eksctl create cluster -f setup/eks-store.yaml
kubectl create namespace store
kubectl top nodes   # metrics-server respondendo (o HPA depende dele); leva ~1 min depois do cluster
# 2. access entry do CI + RoleBinding (comandos da seção anterior)

# 3. rede do RDS: security group (5432 só dos nós) e subnet group (sub-redes privadas)
VPC=$(aws eks describe-cluster --name eks-store --query cluster.resourcesVpcConfig.vpcId --output text)
NODES_SG=$(aws eks describe-cluster --name eks-store --query cluster.resourcesVpcConfig.clusterSecurityGroupId --output text)
SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC Name=map-public-ip-on-launch,Values=false \
  --query 'Subnets[].SubnetId' --output text)
RDS_SG=$(aws ec2 create-security-group --vpc-id $VPC --group-name rds-store \
  --description "RDS PostgreSQL: 5432 so dos nos do eks-store" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $RDS_SG --protocol tcp --port 5432 --source-group $NODES_SG
aws rds create-db-subnet-group --db-subnet-group-name rds-store \
  --db-subnet-group-description "Sub-redes privadas do eks-store" --subnet-ids $SUBNETS

# 4. RDS (~10 min) e os Secrets com a mesma senha. A senha fica só num arquivo temporário
#    (permissão só do dono), nunca na tela nem na linha de comando, e o arquivo é apagado no fim.
umask 077; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
DB_PASS=$(openssl rand -hex 24)
cat > "$T/rds.json" <<EOF
{"DBInstanceIdentifier":"rds-store","Engine":"postgres","EngineVersion":"17.11",
 "DBInstanceClass":"db.t4g.micro","AllocatedStorage":20,"StorageType":"gp3","StorageEncrypted":true,
 "MasterUsername":"store","MasterUserPassword":"$DB_PASS","DBName":"store",
 "DBSubnetGroupName":"rds-store","VpcSecurityGroupIds":["$RDS_SG"],
 "PubliclyAccessible":false,"MultiAZ":false,"BackupRetentionPeriod":1,"DeletionProtection":false}
EOF
aws rds create-db-instance --cli-input-json "file://$T/rds.json"
printf 'DATABASE_USERNAME=store\nDATABASE_PASSWORD=%s\n' "$DB_PASS" > "$T/db.env"
printf 'JWT_SECRET_KEY=%s\n' "$(openssl rand -base64 32)" > "$T/jwt.env"
kubectl create secret generic account-secrets      -n store --from-env-file="$T/db.env"
kubectl create secret generic notification-secrets -n store --from-env-file="$T/db.env"
kubectl create secret generic auth-secrets         -n store --from-env-file="$T/jwt.env"
unset DB_PASS
aws rds wait db-instance-available --db-instance-identifier rds-store

# 5. liga o deploy automático e publica tudo pelo CI
gh variable set EKS_CLUSTER --org pna26 --body eks-store --visibility all   # gh com escopo admin:org
gh workflow run infra -R pna26/platform --ref main
for r in gateway-service auth-service account-service exchange-service notification-service; do
  gh workflow run ci -R pna26/$r --ref main
done
```

### Test { #testar }

``` bash
LB=$(kubectl get svc lb -n store -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
BASE_URL=http://$LB ./scripts/smoke-test.sh
```

Result on 08/11/2026: **smoke test passed** — register/login with an `HttpOnly` cookie, 401 with no token,
with an invalid token and with a forged `id-account`, both gateway replicas responding and the actuator
blocked at the lb. The only warning was the exchange rate: AwesomeAPI returns **429** for the AWS NAT IP
(the free API's limit for cloud IPs), and exchange returns 503 as expected.

Result on 16/11/2026, with the deploy done by CI (gateway and exchange not published, so no
ELB): through a `kubectl port-forward` to auth, **register 200, login 200** with an
`HttpOnly`/`Secure`/`SameSite=None` cookie, **wrong password 401**. Flyway created the schema in RDS, the outbox
published `AccountCreated` to Kafka and `notification-service` logged the welcome email.
`kubectl top pods` responded (metrics-server ok).

### Delete (~15 min) { #apagar-15-min }

Order matters: the ELB (created by the `lb` Service) and RDS live inside the eksctl VPC and prevent
it from being deleted.

``` bash
kubectl delete svc lb -n store
aws rds delete-db-instance --db-instance-identifier rds-store --skip-final-snapshot --delete-automated-backups
aws rds wait db-instance-deleted --db-instance-identifier rds-store
aws rds delete-db-subnet-group --db-subnet-group-name rds-store
RDS_SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=rds-store \
  --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 delete-security-group --group-id $RDS_SG
eksctl delete cluster -f setup/eks-store.yaml --disable-nodegroup-eviction
gh variable delete EKS_CLUSTER --org pna26   # senão todo push na main tenta deploy num cluster que não existe
```

Check that nothing is still billing (every line should come back empty):

``` bash
aws eks list-clusters; aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier'
aws elb describe-load-balancers --query 'LoadBalancerDescriptions[].LoadBalancerName'
aws ec2 describe-nat-gateways --filter Name=state,Values=available,pending --query 'NatGateways[].NatGatewayId'
aws ec2 describe-addresses --query 'Addresses[].AllocationId'
aws ec2 describe-volumes --query 'Volumes[].VolumeId'
```

On 16/11/2026 the environment was created, tested and deleted on the same day; the check came back empty (only the
ECR images remain).

## Costs { #custos }

On-demand prices in us-east-2, per hour with the environment running:

| Item | US$/h |
|---|---:|
| EKS control plane (standard support) | 0.100 |
| 2× EC2 `t3.medium` | 0.083 |
| NAT gateway (excluding data) | 0.045 |
| Classic Load Balancer | 0.025 |
| RDS `db.t4g.micro` + 20 GB gp3 | 0.019 |
| **Total** | **~0.27** |

With the US$ 20/month budget, that comes to about **70 hours running**. That is why the environment is created for
tests, demos and the load test and deleted right afterwards. The ECR images remain (cents
per month).

## Issues encountered { #problemas-encontrados }

| Issue | Cause | Solution |
|---|---|---|
| `docker login` to ECR fails on Windows ("O fragmento de código recebeu dados incorretos") | the ECR token obtained via SSO is ~3 KB and Windows Credential Manager accepts at most 2560 bytes | use `docker-credential-ecr-login` (ships with Docker Desktop) in `credHelpers`, without `docker login` |
| `denied` when pulling images from GHCR | `gh` token without the `read:packages` scope | `gh auth refresh -h github.com -s read:packages` |
| `infra` workflow fails: `roles.rbac.authorization.k8s.io "prometheus" is forbidden ... attempting to grant RBAC permissions not currently held` | Kubernetes' anti-escalation check does not see the permissions from the AWS access policy (`AmazonEKSAdminPolicy`) | `store-deployers` group on the access entry + `ci-store-admin` `RoleBinding` to the `admin` role in `store` (see [Deploy via CI/CD](#deploy-pelo-cicd)) |
| `lb` in `CrashLoopBackOff`: `host not found in upstream "gateway:8080"` | Nginx resolves the `gateway` name at startup, and the `gateway` Service does not exist yet | normal while gateway is not published; `infra`'s `rollout status` times out because of this |
| `gh variable set --org` returns 403 | the `gh` token lacks the `admin:org` scope | `gh auth refresh -h github.com -s admin:org` (and `--remove-scopes admin:org` afterwards) |
| `aws` responds `Token has expired and refresh failed` | the SSO session expired | `aws sso login` |
| `lb` pods restart twice on the first deploy | Nginx resolves `gateway` at startup and came up before the `gateway` Service existed | none: on the next attempt the Service already exists |
| exchange returns 503 on EKS | AwesomeAPI returns 429 for the AWS egress IP | expected exchange behavior (cache + 503); affects the load test's `exchange` scenario. **Fix:** exchange switched to BACEN's PTAX, which is public and has no cloud blocking ([Exchange](../servicos/exchange.md)); confirm on the next deploy |
