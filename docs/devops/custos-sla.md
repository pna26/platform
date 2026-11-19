# PaaS, custos e SLA

!!! info "Responsável: Gabriel"

!!! note "Preços conferidos em 05/11/2026, alinhados ao cluster real em 09/11/2026"

    Valores **on-demand em `us-east-2`** (a região do `eks-store`, ver [EKS](eks.md)), em USD, mês = 730 h.
    Conferidos contra as páginas de preço do
    [EKS](https://aws.amazon.com/eks/pricing/){target="_blank"},
    [Fargate](https://aws.amazon.com/fargate/pricing/){target="_blank"} e
    [Classic Load Balancer](https://aws.amazon.com/elasticloadbalancing/classicloadbalancer/pricing){target="_blank"}
    e com calculadoras de terceiros para o EC2 (`t3.medium` US$ 0,0416/h) e o IPv4 público
    (US$ 0,005/h). Preços mudam: para um orçamento real, usar a
    [AWS Pricing Calculator](https://calculator.aws/){target="_blank"}. Impostos e câmbio para BRL não
    estão incluídos.

## O que precisa rodar

Soma dos `requests` dos manifestos que vão para o EKS (`platform/k8s/` e o `k8s/` de cada serviço):

| Componente | Réplicas | CPU (request) | Memória (request) |
|---|---:|---:|---:|
| `lb` (Nginx) | 2 | 2 × 50m | 2 × 32 Mi |
| `gateway` | 2 | 2 × 250m | 2 × 384 Mi |
| `auth`, `account`, `notification` (Spring Boot) | 1 + 1 + 1 | 3 × 100m | 3 × 256 Mi |
| `exchange` | 2 | 2 × 100m | 2 × 96 Mi |
| `kafka` + `mailpit` | 1 + 1 | 300m | 800 Mi |
| `redis` | 1 | 100m | 128 Mi |
| `prometheus` + `grafana` | 1 + 1 | 200m | 384 Mi |
| **Plataforma** | | **≈ 1,7 vCPU** | **≈ 3,0 GiB** |
| Sistema do EKS (coredns, aws-node, kube-proxy, metrics-server) | | ≈ 0,55 vCPU | ≈ 0,4 GiB |
| **Reservado** | | **≈ 2,25 vCPU** | **≈ 3,4 GiB** |

O `eks-store` tem **2 nós `t3.medium` fixos** (≈ 1,93 vCPU e ≈ 3,4 GiB alocáveis cada, ≈ 3,86 vCPU e
≈ 6,8 GiB no total). Sobra ≈ **1,6 vCPU**: espaço para **mais ≈ 6 réplicas do gateway** (250m cada) no
[teste com HPA](load-testing.md#teste-no-eks-com-hpa). Passando disso, os pods novos ficam *Pending*
até existir um nó a mais (o node group não escala sozinho: `minSize = maxSize = 2`).

## Custo no EKS (o que entregamos)

| Item | Cálculo | USD/h | USD/mês |
|---|---|---:|---:|
| Control plane do EKS (suporte padrão) | US$ 0,10/h | 0,100 | 73,00 |
| 2 nós EC2 `t3.medium` | 2 × US$ 0,0416/h | 0,083 | 60,74 |
| NAT gateway (1, os nós são privados) | US$ 0,045/h + US$ 0,045/GB | 0,045 | ≈ 33,30 |
| Classic Load Balancer (Service `LoadBalancer` do `lb`) | US$ 0,025/h + US$ 0,008/GB | 0,025 | ≈ 18,50 |
| RDS PostgreSQL `db.t4g.micro` single-AZ + 20 GB gp3 | US$ 0,016/h + 20 × US$ 0,115/mês | 0,019 | ≈ 14,00 |
| IPv4 públicos (NAT + balanceador em 2 zonas) | 3 × US$ 0,005/h | 0,015 | 10,95 |
| Disco dos nós (EBS gp3, 20 GB cada) | 2 × 20 GB × US$ 0,08/mês | 0,004 | 3,20 |
| ECR (imagens dos serviços, ≈ 2 GB) | US$ 0,10/GB/mês | — | 0,20 |
| Saída de dados | primeiros 100 GB/mês gratuitos | — | 0,00 |
| **Total** | | **≈ 0,29** | **≈ 214** |

A [página do EKS](eks.md#custos) chega a ≈ US$ 0,27/h somando os cinco primeiros itens; com os IPv4
públicos e os discos, ≈ US$ 0,29/h.

!!! danger "Versão do Kubernetes: armadilha de 6×"

    Os US$ 0,10/h do control plane valem só enquanto a versão do Kubernetes do cluster está no
    **suporte padrão** (≈ 14 meses após o lançamento da versão no EKS). Depois disso o cluster entra
    sozinho no **suporte estendido**: **US$ 0,60/h ≈ US$ 438/mês**, só de control plane. O `eks-store`
    usa a 1.37, em suporte padrão até 11/2027.

O **NAT gateway** é o item "escondido" mais caro em projetos pequenos (≈ 15 % do total, mais que o
balanceador). O grupo escolheu pagar por ele para os nós não terem IP público; a alternativa barata
seria nós em sub-rede pública com *security group* fechado.

### Para o projeto (budget de US$ 20/mês)

O que custa é o **tempo ligado**: ≈ US$ 0,29 por hora com tudo no ar. Com o budget de US$ 20/mês, são
≈ **70 horas**. O "dia do EKS" (testes, teste de carga com HPA e gravação, ≈ 8 h) sai por ≈ US$ 2,30.
Regras do grupo:

- subir o cluster só para testar/apresentar e **apagar no fim** (`eksctl delete cluster`, roteiro na
  página do [EKS](eks.md)): o control plane cobra mesmo sem nenhum pod;
- alerta de *billing* na conta (página [AWS](aws.md));
- RDS, NAT e balanceador também cobram parados: apagar junto.

### Como baixar o custo em produção

| Medida | Economia | Observação |
|---|---|---|
| Nós **Graviton** (`t4g.medium`, ARM) | ≈ 20 % nos nós | Nossas imagens já são multi-arch (amd64 + arm64) pelo [pipeline](ci-cd.md). |
| Nós **Spot** | até ≈ 70 % nos nós | Os serviços são *stateless* com 2 réplicas; aguentam perder um nó. Não usar para o banco. |
| *Savings Plan* de 1 ano | ≈ 30–40 % em EC2/Fargate | Só quando a carga for estável. |
| Escalar por métrica (HPA) | paga o pico só no pico | Usa as métricas do [Prometheus](../bottlenecks/observability.md). |

## PaaS: e se não usássemos Kubernetes?

Os mesmos containers podem rodar numa **PaaS**, em que o provedor cuida de servidores, balanceamento e
escala. Comparação para a mesma plataforma (6 containers pequenos + PostgreSQL + Redis):

| Opção | Tipo | Estimativa USD/mês | Prós | Contras |
|---|---|---:|---|---|
| **EKS** (atual) | Kubernetes gerenciado | ≈ 214 | Controle total (rede, Nginx, Prometheus, sidecars), portável, é o que o curso pede | US$ 73 só de control plane; mais coisas para operar (nós, upgrades, RBAC) |
| **ECS Fargate** + ALB | CaaS (containers sem servidor) | ≈ 105 | Sem nós nem control plane pago; paga por vCPU/GB do container | Amarrado à AWS; Prometheus precisa de outra solução (AMP ou CloudWatch) |
| **AWS App Runner** | PaaS | ≈ 120 | `git push`/imagem → URL com HTTPS e escala automática | Sem rede interna fácil entre serviços: o "gateway como único ponto de entrada" fica mais difícil |
| **Render** / **Railway** | PaaS | ≈ 60–150 | Mais simples de todos, banco e Redis gerenciados com 1 clique | Planos baratos têm 512 MB (pouco para JVM); menos controle de rede e de região (latência para o Brasil) |
| **Heroku** | PaaS | ≈ 100–180 | Maduro, bom para protótipo | Caro por recurso; dyno básico dorme |

Como chegamos no Fargate: 6 tarefas × (0,25 vCPU + 0,5 GB) = 1,5 vCPU × US$ 0,04048/h + 3 GB ×
US$ 0,004445/h ≈ US$ 54 + ALB ≈ US$ 22 + ElastiCache `cache.t4g.micro` ≈ US$ 12 + RDS ≈ US$ 14.

**Conclusão:** para **esta** plataforma (pequena, 3 desenvolvedores, carga baixa), uma PaaS ou o
Fargate sairia **mais barato e com menos operação** que o EKS — a maior parte do custo do EKS é fixa
(control plane + nós sempre ligados). O EKS passa a compensar quando há **muitos serviços** dividindo
os mesmos nós, quando se precisa de recursos do Kubernetes (HPA por métrica própria, *service mesh*,
operadores, portabilidade entre nuvens) ou quando a equipe já tem a operação do cluster resolvida.
Escolhemos o EKS por ser o objetivo do curso e por manter o mesmo desenho (Nginx → gateway → serviços,
Redis compartilhado, Prometheus) do ambiente local.

## SLA e SLO

**SLA** é o compromisso contratual do provedor (com crédito se não cumprir); **SLO** é a meta interna que
definimos para a nossa plataforma; **SLI** é a medida que mostra se cumprimos.

### SLA dos serviços da AWS que usamos

| Serviço | SLA mensal |
|---|---:|
| EKS (control plane) | 99,95 % |
| EC2 — região (instâncias em ≥ 2 zonas) | 99,99 % |
| EC2 — instância individual | 99,5 % |
| Elastic Load Balancing | 99,99 % |
| RDS single-AZ / Multi-AZ | 99,5 % / 99,95 % |
| API de câmbio (AwesomeAPI) | **nenhum** (serviço gratuito) |

### Disponibilidade composta

Uma requisição autenticada passa **em série** por balanceador → Nginx → gateway → Auth/Account → banco;
a disponibilidade do caminho é o **produto** das disponibilidades. Componentes com **réplicas em zonas
diferentes** ficam em paralelo: `1 − (1 − 0,995)² = 99,9975 %`.

| Cenário | Conta | Disponibilidade | Indisponível/mês |
|---|---|---:|---:|
| Como está (2 réplicas em 2 zonas, RDS single-AZ) | 0,9995 (EKS) × 0,9999 (ELB) × 0,99998 (nós) × 0,995 (RDS) | ≈ **99,44 %** | ≈ 4 h |
| Com RDS Multi-AZ | … × 0,9995 | ≈ **99,89 %** | ≈ 48 min |
| Tudo com 1 réplica, 1 nó | 0,9995 × 0,9999 × 0,995 × 0,995 | ≈ 98,95 % | ≈ 7,7 h |

O **banco single-AZ** é o elo mais fraco: as réplicas do gateway e do Exchange não adiantam se o
PostgreSQL cai. Rotas que não dependem do banco (câmbio com token já em cache no Redis) continuam no ar.
O Exchange depende de uma API **sem SLA**; o cache com *stale-if-error* ([Cache da cotação](../bottlenecks/exchange-cache.md))
segura até 10 min de queda dela. No `eks-store`, a saída para a AwesomeAPI ainda passa pelo **único NAT
gateway** (numa zona só): se ele ou a zona caem, o Exchange perde a API externa, mas o resto da
plataforma não sai do ar, porque nenhuma outra rota depende da internet. E no teste do Nicolas a
AwesomeAPI respondeu `429` para o IP da AWS: o limite gratuito é por IP, e o IP do NAT é compartilhado
por todos os pods. Um `EXCHANGE_API_TOKEN` resolve.

### Nosso SLO

| SLI | SLO (janela de 30 dias) | Como medir (Prometheus) |
|---|---|---|
| Disponibilidade: % de requisições sem erro 5xx no gateway | **99,5 %** (≈ 3 h 39 min de orçamento de erro/mês) | `1 - sum(rate(http_server_requests_seconds_count{application="gateway",status=~"5.."}[30d])) / sum(rate(http_server_requests_seconds_count{application="gateway"}[30d]))` |
| Latência: p95 das requisições | **< 500 ms** | k6 no [teste de carga](load-testing.md); no servidor, p95 do Exchange pelo histograma |
| Erros sob a carga esperada | **< 1 %** | `http_req_failed` do k6 |

**Conferido no EKS (19/11/2026):** nos [testes de carga](load-testing.md#resultado-no-eks) pelo ELB,
**0 erros** em 379.762 requisições: p95 de 254 ms com o HPA escalando o gateway de 2 para 6 réplicas
(100 usuários) e de 370 ms com 200 usuários e 2 réplicas fixas, em ambos com ≈ 180 ms de rede do
Brasil até `us-east-2`. Os três SLOs da tabela abaixo foram cumpridos. A disponibilidade de um mês não
se mede numa rodada de 4 min: ela continua sendo a conta do SLA composto acima.

Por que 99,5 % e não 99,9 %: com o banco single-AZ a própria AWS só garante ≈ 99,4 % no caminho
completo; prometer mais que isso seria prometer o que a infraestrutura não sustenta. Para subir para
**99,9 %** o primeiro passo é o RDS Multi-AZ (+ ≈ US$ 14/mês), depois alerta no Grafana quando a taxa
de 5xx consumir o orçamento de erro mais rápido que o normal.

| SLO | Indisponibilidade permitida por mês |
|---:|---:|
| 99 % | 7 h 18 min |
| 99,5 % | 3 h 39 min |
| 99,9 % | 43 min 49 s |
| 99,95 % | 21 min 54 s |
| 99,99 % | 4 min 23 s |
