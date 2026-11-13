#!/usr/bin/env bash
# Ensaio local do EKS: sobe a plataforma num cluster kind (Kubernetes dentro do
# Docker) com os MESMOS manifestos do EKS (k8s/ e o k8s/ de cada serviço).
#
#   ./scripts/kind-up.sh              # cria o cluster "store" e faz o deploy
#   kind delete cluster --name store  # apaga tudo
#
# Parecido com o eks-store de propósito:
#   - 2 nós de trabalho (+ o control plane, que no EKS é da AWS);
#   - cada nó reserva CPU e memória para sobrar o mesmo que um t3.medium tem
#     alocável no EKS (≈ 1,93 vCPU e ≈ 3,4 GiB): o scheduler esbarra no mesmo
#     limite de capacidade (ver docs/devops/custos-sla.md#o-que-precisa-rodar);
#   - metrics-server instalado (o HPA depende dele, como no EKS);
#   - o Service lb (LoadBalancer no EKS) fica na NodePort 30080, publicada em
#     http://localhost:8081 (o "ELB" local).
# Diferenças: imagens locais (as do "docker compose build") em vez do ECR/GHCR e
# um Postgres no cluster em vez do RDS. A CPU dos nós não é limitada de verdade
# (o kind divide a CPU da máquina), então a latência não é a do EKS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CLUSTER="${CLUSTER:-store}"
NS="${NS:-store}"
LB_PORT="${LB_PORT:-8081}"
CTX="kind-$CLUSTER"
k() { kubectl --context "$CTX" "$@"; }

command -v kind >/dev/null || { echo "instale o kind: https://kind.sigs.k8s.io" >&2; exit 1; }

# imagem do compose -> imagem que o manifesto usa
SERVICES=(gateway auth account exchange notification)
for s in "${SERVICES[@]}"; do
  docker image inspect "store-$s:latest" >/dev/null 2>&1 \
    || { echo "falta a imagem store-$s:latest: rode 'docker compose build'" >&2; exit 1; }
done

# ---------------------------------------------------------------- cluster
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  # reserva = o que a VM do Docker tem - o alocável de um t3.medium no EKS
  NCPU="$(docker info --format '{{.NCPU}}')"
  MEM_MI="$(( $(docker info --format '{{.MemTotal}}') / 1048576 ))"
  RES_CPU="$(( NCPU * 1000 - 1930 ))m"
  RES_MEM="$(( MEM_MI - 3482 ))Mi"   # sobram 3482 Mi = 3,4 GiB
  echo ">> kind: $CLUSTER (2 workers, reserva cpu=$RES_CPU memory=$RES_MEM por nó)"
  cat <<EOF | kind create cluster --name "$CLUSTER" --wait 120s --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: $LB_PORT
        listenAddress: "127.0.0.1"
  - role: worker
  - role: worker
kubeadmConfigPatches:
  - |
    kind: KubeletConfiguration
    systemReserved:
      cpu: "$RES_CPU"
      memory: "$RES_MEM"
EOF
fi
k get nodes -o custom-columns='NÓ:.metadata.name,CPU ALOCÁVEL:.status.allocatable.cpu,MEMÓRIA ALOCÁVEL:.status.allocatable.memory'

# ---------------------------------------------------------------- imagens
# as dos serviços vão do Docker local para os workers (sem registry); as públicas
# (postgres, kafka, grafana...) os nós baixam sozinhos, como no EKS
WORKERS="$(kind get nodes --name "$CLUSTER" | grep -v control-plane | paste -sd, -)"
for s in "${SERVICES[@]}"; do
  echo ">> imagem store-$s:latest"
  kind load docker-image "store-$s:latest" --name "$CLUSTER" --nodes "$WORKERS" >/dev/null
done

# ---------------------------------------------------------------- metrics-server
if ! k get deployment metrics-server -n kube-system >/dev/null 2>&1; then
  # mesmo comando do EKS; no kind o kubelet tem certificado autoassinado
  k apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  k patch deployment metrics-server -n kube-system --type=json \
    -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi

# ---------------------------------------------------------------- namespace e secrets
k get namespace "$NS" >/dev/null 2>&1 || k create namespace "$NS"
apply_secret() { k create secret generic "$@" -n "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null; }
k get secret auth-secrets -n "$NS" >/dev/null 2>&1 \
  || apply_secret auth-secrets --from-literal=JWT_SECRET_KEY="$(openssl rand -base64 32)"
apply_secret account-secrets --from-literal=DATABASE_USERNAME=store --from-literal=DATABASE_PASSWORD=store
apply_secret notification-secrets --from-literal=DATABASE_USERNAME=store --from-literal=DATABASE_PASSWORD=store
apply_secret grafana-admin --from-literal=password="${GRAFANA_ADMIN_PASSWORD:-admin}"
k create configmap grafana-dashboards -n "$NS" --from-file=setup/grafana/dashboards/ \
  --dry-run=client -o yaml | k apply -f - >/dev/null

# Postgres no lugar do RDS (só no ensaio local)
k apply -n "$NS" -f - >/dev/null <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
        - name: postgres
          image: postgres:17
          env:
            - { name: POSTGRES_USER, value: store }
            - { name: POSTGRES_PASSWORD, value: store }
            - { name: POSTGRES_DB, value: store }
          ports: [ { containerPort: 5432 } ]
          readinessProbe:
            exec: { command: [ pg_isready, -U, store, -d, store ] }
            periodSeconds: 5
          resources:
            requests: { cpu: 100m, memory: 128Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector: { app: postgres }
  ports: [ { port: 5432, targetPort: 5432 } ]
EOF

# ---------------------------------------------------------------- deploy
echo ">> k8s/ (lb, redis, kafka, mailpit, prometheus, grafana)"
k apply -n "$NS" -f k8s/ >/dev/null
k rollout status deployment/postgres -n "$NS" --timeout=180s

echo ">> serviços"
for s in "${SERVICES[@]}"; do
  # o que o CI faz com a tag do commit, aqui com a imagem local
  sed -E -e "s#image: .*/$s-service:latest#image: store-$s:latest#" \
         -e 's#imagePullPolicy: Always#imagePullPolicy: IfNotPresent#' \
         -e 's#DATABASE_HOST: .*rds\.amazonaws\.com#DATABASE_HOST: postgres#' \
         "api/$s-service/k8s/k8s.yaml" | k apply -n "$NS" -f - >/dev/null
done

# o "ELB" local: NodePort fixa 30080, publicada pelo kind em localhost:$LB_PORT
k patch svc lb -n "$NS" -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":8080,"nodePort":30080}]}}' >/dev/null

for d in redis kafka account auth gateway exchange notification lb prometheus grafana; do
  k rollout status "deployment/$d" -n "$NS" --timeout=300s
done
k top nodes 2>/dev/null || echo "(metrics-server ainda coletando: 'kubectl top nodes' em ≈ 1 min)"

echo
echo ">> pronto: http://localhost:$LB_PORT/gateway/actuator/health"
echo "   teste com HPA: ver docs/devops/load-testing.md#ensaio-local-kind"
