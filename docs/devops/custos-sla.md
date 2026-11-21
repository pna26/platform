# PaaS, costs and SLA

!!! info "Owner: Gabriel"

!!! note "Prices checked on 05/11/2026, aligned with the real cluster on 09/11/2026"

    **On-demand prices in `us-east-2`** (the `eks-store` region, see [EKS](eks.md)), in USD, month = 730 h.
    Checked against the pricing pages for
    [EKS](https://aws.amazon.com/eks/pricing/){target="_blank"},
    [Fargate](https://aws.amazon.com/fargate/pricing/){target="_blank"} and
    [Classic Load Balancer](https://aws.amazon.com/elasticloadbalancing/classicloadbalancer/pricing){target="_blank"}
    and with third-party calculators for EC2 (`t3.medium` US$ 0.0416/h) and public IPv4
    (US$ 0.005/h). Prices change: for a real budget, use the
    [AWS Pricing Calculator](https://calculator.aws/){target="_blank"}. Taxes and conversion to BRL are
    not included.

## What needs to run { #o-que-precisa-rodar }

Sum of the `requests` in the manifests that go to EKS (`platform/k8s/` and each service's `k8s/`):

| Component | Replicas | CPU (request) | Memory (request) |
|---|---:|---:|---:|
| `lb` (Nginx) | 2 | 2 × 50m | 2 × 32 Mi |
| `gateway` | 2 | 2 × 250m | 2 × 384 Mi |
| `auth`, `account`, `notification` (Spring Boot) | 1 + 1 + 1 | 3 × 100m | 3 × 256 Mi |
| `exchange` | 2 | 2 × 100m | 2 × 96 Mi |
| `kafka` + `mailpit` | 1 + 1 | 300m | 800 Mi |
| `redis` | 1 | 100m | 128 Mi |
| `prometheus` + `grafana` | 1 + 1 | 200m | 384 Mi |
| **Platform** | | **≈ 1.7 vCPU** | **≈ 3.0 GiB** |
| EKS system (coredns, aws-node, kube-proxy, metrics-server) | | ≈ 0.55 vCPU | ≈ 0.4 GiB |
| **Reserved** | | **≈ 2.25 vCPU** | **≈ 3.4 GiB** |

`eks-store` has **2 fixed `t3.medium` nodes** (≈ 1.93 vCPU and ≈ 3.4 GiB allocatable each, ≈ 3.86 vCPU and
≈ 6.8 GiB in total). That leaves ≈ **1.6 vCPU**: room for **≈ 6 more gateway replicas** (250m each) in the
[HPA test](load-testing.md#teste-no-eks-com-hpa). Beyond that, new pods stay *Pending*
until there is one more node (the node group does not scale on its own: `minSize = maxSize = 2`).

## Cost on EKS (what we delivered) { #custo-no-eks-o-que-entregamos }

| Item | Calculation | USD/h | USD/month |
|---|---|---:|---:|
| EKS control plane (standard support) | US$ 0.10/h | 0.100 | 73.00 |
| 2 EC2 `t3.medium` nodes | 2 × US$ 0.0416/h | 0.083 | 60.74 |
| NAT gateway (1, the nodes are private) | US$ 0.045/h + US$ 0.045/GB | 0.045 | ≈ 33.30 |
| Classic Load Balancer (`lb`'s `LoadBalancer` Service) | US$ 0.025/h + US$ 0.008/GB | 0.025 | ≈ 18.50 |
| RDS PostgreSQL `db.t4g.micro` single-AZ + 20 GB gp3 | US$ 0.016/h + 20 × US$ 0.115/month | 0.019 | ≈ 14.00 |
| Public IPv4 (NAT + load balancer in 2 zones) | 3 × US$ 0.005/h | 0.015 | 10.95 |
| Node disks (EBS gp3, 20 GB each) | 2 × 20 GB × US$ 0.08/month | 0.004 | 3.20 |
| ECR (service images, ≈ 2 GB) | US$ 0.10/GB/month | — | 0.20 |
| Data transfer out | first 100 GB/month free | — | 0.00 |
| **Total** | | **≈ 0.29** | **≈ 214** |

The [EKS page](eks.md#custos) gets to ≈ US$ 0.27/h by adding up the first five items; with the public
IPv4 addresses and the disks, ≈ US$ 0.29/h.

!!! danger "Kubernetes version: the 6× trap"

    The control plane's US$ 0.10/h only applies while the cluster's Kubernetes version is in
    **standard support** (≈ 14 months after the version's release on EKS). After that the cluster
    automatically moves to **extended support**: **US$ 0.60/h ≈ US$ 438/month**, for the control plane alone. `eks-store`
    uses 1.37, in standard support until 11/2027.

The **NAT gateway** is the most expensive "hidden" item in small projects (≈ 15 % of the total, more than the
load balancer). The group chose to pay for it so the nodes do not have public IPs; the cheap alternative
would be nodes in a public subnet with a locked-down *security group*.

### For the project (US$ 20/month budget) { #para-o-projeto-budget-de-us-20mes }

What costs money is **uptime**: ≈ US$ 0.29 per hour with everything up. With the US$ 20/month budget, that is
≈ **70 hours**. The "EKS day" (tests, HPA load test and recording, ≈ 8 h) comes to ≈ US$ 2.30.
Group rules:

- bring the cluster up only to test/present and **delete it at the end** (`eksctl delete cluster`, steps on the
  [EKS](eks.md) page): the control plane charges even with no pods;
- *billing* alert on the account ([AWS](aws.md) page);
- RDS, NAT and the load balancer also charge while idle: delete them too.

### How to lower the cost in production { #como-baixar-o-custo-em-producao }

| Measure | Savings | Note |
|---|---|---|
| **Graviton** nodes (`t4g.medium`, ARM) | ≈ 20 % on nodes | Our images are already multi-arch (amd64 + arm64) via the [pipeline](ci-cd.md). |
| **Spot** nodes | up to ≈ 70 % on nodes | The services are *stateless* with 2 replicas; they can survive losing a node. Do not use for the database. |
| 1-year *Savings Plan* | ≈ 30–40 % on EC2/Fargate | Only once the load is stable. |
| Scale by metric (HPA) | pay for the peak only at the peak | Uses the [Prometheus](../bottlenecks/observability.md) metrics. |

## PaaS: what if we didn't use Kubernetes? { #paas-e-se-nao-usassemos-kubernetes }

The same containers can run on a **PaaS**, where the provider takes care of servers, load balancing and
scaling. Comparison for the same platform (6 small containers + PostgreSQL + Redis):

| Option | Type | Estimate USD/month | Pros | Cons |
|---|---|---:|---|---|
| **EKS** (current) | Managed Kubernetes | ≈ 214 | Full control (network, Nginx, Prometheus, sidecars), portable, it is what the course requires | US$ 73 for the control plane alone; more things to operate (nodes, upgrades, RBAC) |
| **ECS Fargate** + ALB | CaaS (serverless containers) | ≈ 105 | No nodes and no paid control plane; pay per container vCPU/GB | Tied to AWS; Prometheus needs another solution (AMP or CloudWatch) |
| **AWS App Runner** | PaaS | ≈ 120 | `git push`/image → URL with HTTPS and automatic scaling | No easy internal networking between services: the "gateway as the single entry point" becomes harder |
| **Render** / **Railway** | PaaS | ≈ 60–150 | Simplest of all, managed database and Redis in 1 click | Cheap plans have 512 MB (too little for the JVM); less control over network and region (latency to Brazil) |
| **Heroku** | PaaS | ≈ 100–180 | Mature, good for prototypes | Expensive per resource; basic dyno sleeps |

How we got the Fargate number: 6 tasks × (0.25 vCPU + 0.5 GB) = 1.5 vCPU × US$ 0.04048/h + 3 GB ×
US$ 0.004445/h ≈ US$ 54 + ALB ≈ US$ 22 + ElastiCache `cache.t4g.micro` ≈ US$ 12 + RDS ≈ US$ 14.

**Conclusion:** for **this** platform (small, 3 developers, low load), a PaaS or
Fargate would be **cheaper and require less operation** than EKS — most of the EKS cost is fixed
(control plane + always-on nodes). EKS starts to pay off when there are **many services** sharing
the same nodes, when you need Kubernetes features (HPA on custom metrics, *service mesh*,
operators, multi-cloud portability) or when the team already has cluster operations figured out.
We chose EKS because it is the goal of the course and because it keeps the same design (Nginx → gateway → services,
shared Redis, Prometheus) as the local environment.

## SLA and SLO { #sla-e-slo }

**SLA** is the provider's contractual commitment (with credits if not met); **SLO** is the internal target we
set for our platform; **SLI** is the measurement that shows whether we met it.

### SLA of the AWS services we use { #sla-dos-servicos-da-aws-que-usamos }

| Service | Monthly SLA |
|---|---:|
| EKS (control plane) | 99.95 % |
| EC2 — region (instances in ≥ 2 zones) | 99.99 % |
| EC2 — single instance | 99.5 % |
| Elastic Load Balancing | 99.99 % |
| RDS single-AZ / Multi-AZ | 99.5 % / 99.95 % |
| Exchange rate API (AwesomeAPI) | **none** (free service) |

### Composite availability { #disponibilidade-composta }

An authenticated request goes **in series** through load balancer → Nginx → gateway → Auth/Account → database;
the availability of the path is the **product** of the availabilities. Components with **replicas in different
zones** are in parallel: `1 − (1 − 0,995)² = 99,9975 %`.

| Scenario | Calculation | Availability | Downtime/month |
|---|---|---:|---:|
| As is (2 replicas in 2 zones, RDS single-AZ) | 0.9995 (EKS) × 0.9999 (ELB) × 0.99998 (nodes) × 0.995 (RDS) | ≈ **99.44 %** | ≈ 4 h |
| With RDS Multi-AZ | … × 0.9995 | ≈ **99.89 %** | ≈ 48 min |
| Everything with 1 replica, 1 node | 0.9995 × 0.9999 × 0.995 × 0.995 | ≈ 98.95 % | ≈ 7.7 h |

The **single-AZ database** is the weakest link: gateway and Exchange replicas do not help if
PostgreSQL goes down. Routes that do not depend on the database (exchange rate with a token already cached in Redis) stay up.
Exchange depends on an API **with no SLA**; the cache with *stale-if-error* ([Quote cache](../bottlenecks/exchange-cache.md))
covers up to 10 min of it being down. In `eks-store`, egress to AwesomeAPI still goes through the **single NAT
gateway** (in one zone only): if it or the zone goes down, Exchange loses the external API, but the rest of the
platform stays up, because no other route depends on the internet. And in Nicolas's test
AwesomeAPI returned `429` to the AWS IP: the free limit is per IP, and the NAT IP is shared
by all pods. That is why Exchange switched to the Central Bank's PTAX ([Exchange](../servicos/exchange.md)),
which is public and has no such block.

### Our SLO { #nosso-slo }

| SLI | SLO (30-day window) | How to measure (Prometheus) |
|---|---|---|
| Availability: % of requests without 5xx errors at the gateway | **99.5 %** (≈ 3 h 39 min of error budget/month) | `1 - sum(rate(http_server_requests_seconds_count{application="gateway",status=~"5.."}[30d])) / sum(rate(http_server_requests_seconds_count{application="gateway"}[30d]))` |
| Latency: request p95 | **< 500 ms** | k6 in the [load test](load-testing.md); on the server, Exchange p95 from the histogram |
| Errors under expected load | **< 1 %** | k6's `http_req_failed` |

**Verified on EKS (19/11/2026):** in the [load tests](load-testing.md#resultado-no-eks) through the ELB,
**0 errors** in 379,762 requests: p95 of 254 ms with the HPA scaling the gateway from 2 to 6 replicas
(100 users) and 370 ms with 200 users and 2 fixed replicas, both with ≈ 180 ms of network latency from
Brazil to `us-east-2`. The three SLOs in the table were met. A month's availability cannot
be measured in a 4 min run: it is still the composite SLA calculation above.

Why 99.5 % and not 99.9 %: with the single-AZ database, AWS itself only guarantees ≈ 99.4 % on the full
path; promising more than that would be promising what the infrastructure cannot sustain. To go up to
**99.9 %**, the first step is RDS Multi-AZ (+ ≈ US$ 14/month), then a Grafana alert when the 5xx rate
burns through the error budget faster than normal.

| SLO | Allowed downtime per month |
|---:|---:|
| 99 % | 7 h 18 min |
| 99.5 % | 3 h 39 min |
| 99.9 % | 43 min 49 s |
| 99.95 % | 21 min 54 s |
| 99.99 % | 4 min 23 s |
