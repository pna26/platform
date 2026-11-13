# Balanceamento de carga

!!! info "Responsável: Leonardo"

## Problema

Com um único gateway, toda a plataforma depende de **um processo**: se ele cair, nada responde; se ele
saturar, não há como crescer. Como o gateway é a única porta de entrada, ele é o primeiro gargalo.

## Solução

Um **Nginx** (`lb`) passa a ser o único serviço exposto e distribui as requisições entre **várias
réplicas do gateway** (Round Robin, o padrão do bloco `upstream`). O gateway deixa de publicar porta.

=== "Docker Compose"

    ``` mermaid
    flowchart LR
        user([Usuário]) e0@==>|":8080"| lb["lb · Nginx :80"]:::orange
        subgraph trusted [rede interna do compose]
            lb e1@==> g1[gateway-1]
            lb e2@==> g2[gateway-2]
            lb e3@==> g3[gateway-N]
        end
        e0@{ animate: true }
        e1@{ animate: true }
        e2@{ animate: true }
        e3@{ animate: true }
        classDef orange fill:#FCBE3E
    ```

=== "EKS"

    ``` mermaid
    flowchart LR
        user([Usuário]) e0@==> elb["ELB da AWS<br/>(Service lb, LoadBalancer)"]
        subgraph eks [cluster EKS]
            elb --> n1["lb pod 1 · Nginx"]:::orange
            elb --> n2["lb pod 2 · Nginx"]:::orange
            n1 & n2 --> svc["Service gateway<br/>(ClusterIP)"]
            svc --> g1[gateway pod 1]
            svc --> g2[gateway pod 2]
        end
        e0@{ animate: true }
        classDef orange fill:#FCBE3E
    ```

    No EKS há dois níveis: o ELB da AWS espalha o tráfego entre os pods do Nginx (que também têm 2
    réplicas, para o próprio balanceador não ser ponto único de falha) e o Service `gateway` espalha
    entre os pods do gateway.

## Configuração

``` { .nginx .copy linenums="1" title="setup/nginx.conf" }
--8<-- "setup/nginx.conf"
```

Pontos além do hands-on 7 do curso:

| Diretiva | Por quê |
|---|---|
| `resolver 127.0.0.11` + `server gateway:8080 resolve` | O Nginx re-resolve o DNS do Docker a cada 10 s. Com `docker compose up --scale gateway=N` as réplicas novas entram no balanceamento **sem** `nginx -s reload` (no hands-on o Nginx só resolve na inicialização). |
| `keepalive 32` + `proxy_http_version 1.1` | Reaproveita conexões com o gateway: menos handshakes TCP sob carga. |
| `keepalive_time 10s` (só no `k8s/lb.yaml`) | No k8s o Nginx fala com o Service `gateway`, que balanceia **conexões**, não requisições: uma conexão aberta fica no mesmo pod. Fechando cada conexão depois de 10 s, as réplicas que o HPA cria entram na divisão em ≈ 35–40 s em vez de ≈ 1,5 min ([achado do teste com HPA](../devops/load-testing.md#achado-balanceamento)). No compose não precisa: lá o Nginx enxerga cada réplica pelo DNS e divide as requisições ele mesmo. |
| `location /gateway/actuator { return 404; }` | Métricas do actuator ficam só na rede interna (o Prometheus fala direto com o gateway). |
| `log_format upstream` | O access log mostra qual réplica (`$upstream_addr`) atendeu cada requisição. |

## Evidência

O `GET /health-check` do gateway responde com o hostname do container. Com 3 réplicas, 15 requisições
pelo `lb` se dividem igualmente — e a terceira réplica entrou com o compose já rodando, sem reload do
Nginx:

``` bash
$ docker compose up -d --scale gateway=3 --no-recreate
$ for i in $(seq 1 15); do curl -s localhost:8080/health-check; echo; done | sort | uniq -c
   5 {"hostname":"112e1c20be21"}
   5 {"hostname":"5f9dceeefb1f"}
   5 {"hostname":"c98aacd33a1e"}
```

O mesmo teste roda no CI (`scripts/smoke-test.sh`, workflow `integration`), que falha se menos de duas
réplicas responderem.

## Manifesto k8s

``` { .yaml .copy linenums="1" title="k8s/lb.yaml" }
--8<-- "k8s/lb.yaml"
```
