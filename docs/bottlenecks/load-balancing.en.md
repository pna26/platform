# Load balancing

!!! info "Owner: Leonardo"

## Problem { #problema }

With a single gateway, the whole platform depends on **one process**: if it goes down, nothing responds; if it
saturates, there is no way to scale. Since the gateway is the only entry point, it is the first bottleneck.

## Solution { #solucao }

An **Nginx** (`lb`) becomes the only exposed service and distributes requests across **several
gateway replicas** (Round Robin, the default of the `upstream` block). The gateway no longer publishes a port.

=== "Docker Compose"

    ``` mermaid
    flowchart LR
        user([User]) e0@==>|":8080"| lb["lb · Nginx :80"]:::orange
        subgraph trusted [compose internal network]
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
        user([User]) e0@==> elb["AWS ELB<br/>(Service lb, LoadBalancer)"]
        subgraph eks [EKS cluster]
            elb --> n1["lb pod 1 · Nginx"]:::orange
            elb --> n2["lb pod 2 · Nginx"]:::orange
            n1 & n2 --> svc["Service gateway<br/>(ClusterIP)"]
            svc --> g1[gateway pod 1]
            svc --> g2[gateway pod 2]
        end
        e0@{ animate: true }
        classDef orange fill:#FCBE3E
    ```

    On EKS there are two levels: the AWS ELB spreads traffic across the Nginx pods (which also have 2
    replicas, so the load balancer itself is not a single point of failure) and the `gateway` Service spreads
    it across the gateway pods.

## Configuration { #configuracao }

``` { .nginx .copy linenums="1" title="setup/nginx.conf" }
--8<-- "setup/nginx.conf"
```

What goes beyond the course's hands-on 7:

| Directive | Why |
|---|---|
| `resolver 127.0.0.11` + `server gateway:8080 resolve` | Nginx re-resolves Docker's DNS every 10 s. With `docker compose up --scale gateway=N` the new replicas join the balancing **without** `nginx -s reload` (in the hands-on, Nginx only resolves at startup). |
| `server gateway.store.svc.cluster.local:8080 resolve` + `resolver kube-dns...` (only in `k8s/lb.yaml`) | On EKS, Nginx starts **before** the gateway (`infra` applies `lb` and each service deploys itself afterwards). Without `resolve`, Nginx crashes with *host not found* and `infra` fails waiting for `lb`, as happened on 16/11. With it, `lb` becomes ready, responds `502` until the `gateway` Service exists and then starts forwarding on its own. Nginx's resolver does not use the k8s search domains, hence the fully qualified name. |
| `keepalive 32` + `proxy_http_version 1.1` | Reuses connections to the gateway: fewer TCP handshakes under load. |
| `keepalive_time 10s` (only in `k8s/lb.yaml`) | In k8s, Nginx talks to the `gateway` Service, which balances **connections**, not requests: an open connection stays on the same pod. By closing each connection after 10 s, the replicas created by the HPA join the split in ≈ 35–40 s instead of ≈ 1.5 min ([finding from the HPA test](../devops/load-testing.md#achado-balanceamento)). Not needed on compose: there Nginx sees each replica through DNS and splits the requests itself. |
| `location /gateway/actuator { return 404; }` | Actuator metrics stay on the internal network only (Prometheus talks directly to the gateway). |
| `log_format upstream` | The access log shows which replica (`$upstream_addr`) served each request. |

## Evidence { #evidencia }

The gateway's `GET /health-check` responds with the container hostname. With 3 replicas, 15 requests
through `lb` split evenly — and the third replica joined with compose already running, without reloading
Nginx:

``` bash
$ docker compose up -d --scale gateway=3 --no-recreate
$ for i in $(seq 1 15); do curl -s localhost:8080/health-check; echo; done | sort | uniq -c
   5 {"hostname":"112e1c20be21"}
   5 {"hostname":"5f9dceeefb1f"}
   5 {"hostname":"c98aacd33a1e"}
```

The same test runs in CI (`scripts/smoke-test.sh`, `integration` workflow), which fails if fewer than two
replicas respond.

## k8s manifest { #manifesto-k8s }

``` { .yaml .copy linenums="1" title="k8s/lb.yaml" }
--8<-- "k8s/lb.yaml"
```
