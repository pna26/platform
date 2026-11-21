# How to run

## Prerequisites { #pre-requisitos }

- Git, Java 25, and Maven 3.9+
- Docker with Docker Compose v2

## Locally (Docker Compose) { #localmente-docker-compose }

``` bash
git clone --recurse-submodules https://github.com/pna26/platform.git
cd platform

./scripts/build.sh            # mvn install das interfaces + mvn package dos serviços + cria o .env
docker compose up -d --build  # sobe db, redis, account, auth, exchange, gateway (2 réplicas) e lb
./scripts/smoke-test.sh       # teste ponta a ponta
```

The platform is available at [http://localhost:8080](http://localhost:8080){target="_blank"}. Only `lb` (Nginx)
publishes a port; the other services stay on the compose internal network. Grafana
([http://localhost:3000](http://localhost:3000){target="_blank"}) and Prometheus (`:9090`) are bound to
`127.0.0.1` — see [Observability](bottlenecks/observability.md).

``` { .yaml .copy title="compose.yaml" }
--8<-- "compose.yaml"
```

The `.env` file is created by `scripts/build.sh` from `.env.example`, with a random `JWT_SECRET_KEY`
(`openssl rand -base64 32`). It never goes into git.

### Usage example { #exemplo-de-uso }

``` bash
# cadastro e login (o cookie fica no arquivo cookies.txt)
curl -X POST localhost:8080/auth/register -H 'Content-Type: application/json' \
  -d '{"name":"Ana","email":"ana@store.com","password":"123456"}'
curl -c cookies.txt -X POST localhost:8080/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"ana@store.com","password":"123456"}'

# rotas protegidas (o curl só reenvia cookie Secure em HTTPS, então mandamos o header)
TOKEN=$(awk '/__store_jwt_token/ {print $7}' cookies.txt)
curl -H "Cookie: __store_jwt_token=$TOKEN" localhost:8080/auth/me
curl -H "Cookie: __store_jwt_token=$TOKEN" localhost:8080/accounts
```

### Smoke test { #smoke-test }

`scripts/smoke-test.sh` is the same script that runs in CI (`integration` workflow). It checks:

- sign-up, login (`HttpOnly` cookie), `/auth/me`, `/accounts`, `/exchange/USD/BRL`, and logout;
- `401` without a token, with an invalid token, and with a forged `id-account`;
- that at least 2 gateway replicas responded (load balancing);
- that the token was cached in Redis;
- that `/gateway/actuator/prometheus` is not exposed through `lb`.

``` { .bash .copy title="scripts/smoke-test.sh" }
--8<-- "scripts/smoke-test.sh"
```

### Useful variations { #variacoes-uteis }

``` bash
docker compose up -d --scale gateway=4      # mais réplicas (o Nginx descobre sozinho)
TOKEN_CACHE_ENABLED=false docker compose up -d gateway   # desliga o cache (comparar no teste de carga)
docker compose up -d --scale exchange=3                  # mais réplicas do exchange
docker compose down -v                                    # derruba tudo e apaga os volumes
```

## On EKS { #no-eks }

Deployment to EKS is done by the pipeline ([CI/CD](devops/ci-cd.md)): each service applies its own
`k8s/` and the `platform` repository applies the shared infrastructure (`k8s/lb.yaml` and `k8s/redis.yaml`).
To apply it manually:

``` bash
aws eks update-kubeconfig --name <cluster> --region <região>
kubectl create namespace store
kubectl apply -n store -f k8s/                         # lb (Nginx + LoadBalancer) e redis
kubectl apply -n store -f api/gateway-service/k8s/     # gateway
kubectl get svc lb -n store                            # endereço público do ELB
BASE_URL=http://<endereço-do-elb> ./scripts/smoke-test.sh
```
