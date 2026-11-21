# Como rodar

## Pré-requisitos

- Git, Java 25 e Maven 3.9+
- Docker com Docker Compose v2

## Localmente (Docker Compose)

``` bash
git clone --recurse-submodules https://github.com/pna26/platform.git
cd platform

./scripts/build.sh            # mvn install das interfaces + mvn package dos serviços + cria o .env
docker compose up -d --build  # sobe db, redis, account, auth, exchange, gateway (2 réplicas) e lb
./scripts/smoke-test.sh       # teste ponta a ponta
```

A plataforma fica em [http://localhost:8080](http://localhost:8080){target="_blank"}. Só o `lb` (Nginx)
publica porta; os demais serviços ficam na rede interna do compose. O Grafana
([http://localhost:3000](http://localhost:3000){target="_blank"}) e o Prometheus (`:9090`) ficam presos ao
`127.0.0.1` — ver [Observabilidade](bottlenecks/observability.md).

``` { .yaml .copy title="compose.yaml" }
--8<-- "compose.yaml"
```

O `.env` é criado pelo `scripts/build.sh` a partir do `.env.example`, com um `JWT_SECRET_KEY` aleatório
(`openssl rand -base64 32`). Ele nunca vai para o git.

### Exemplo de uso

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

### Smoke test

O `scripts/smoke-test.sh` é o mesmo que roda no CI (workflow `integration`). Ele confere:

- cadastro, login (cookie `HttpOnly`), `/auth/me`, `/accounts`, `/exchange/USD/BRL` e logout;
- `401` sem token, com token inválido e com `id-account` forjado;
- que pelo menos 2 réplicas do gateway responderam (balanceamento);
- que o token ficou em cache no Redis;
- que `/gateway/actuator/prometheus` não fica exposto pelo `lb`.

``` { .bash .copy title="scripts/smoke-test.sh" }
--8<-- "scripts/smoke-test.sh"
```

### Variações úteis

``` bash
docker compose up -d --scale gateway=4      # mais réplicas (o Nginx descobre sozinho)
TOKEN_CACHE_ENABLED=false docker compose up -d gateway   # desliga o cache (comparar no teste de carga)
docker compose up -d --scale exchange=3                  # mais réplicas do exchange
docker compose down -v                                    # derruba tudo e apaga os volumes
```

## No EKS

O deploy no EKS é feito pelo pipeline ([CI/CD](devops/ci-cd.md)): cada serviço aplica o seu
`k8s/` e o repositório `platform` aplica a infra compartilhada (`k8s/lb.yaml` e `k8s/redis.yaml`).
Para aplicar manualmente:

``` bash
aws eks update-kubeconfig --name <cluster> --region <região>
kubectl create namespace store
kubectl apply -n store -f k8s/                         # lb (Nginx + LoadBalancer) e redis
kubectl apply -n store -f api/gateway-service/k8s/     # gateway
kubectl get svc lb -n store                            # endereço público do ELB
BASE_URL=http://<endereço-do-elb> ./scripts/smoke-test.sh
```
