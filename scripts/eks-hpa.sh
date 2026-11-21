#!/usr/bin/env bash
# Teste de carga com HPA no EKS, de ponta a ponta (roteiro em docs/devops/load-testing.md#teste-no-eks-com-hpa):
#   1. confere os pré-requisitos (kubectl >= 1.34, contexto do eks-store, metrics-server, Service lb);
#   2. roda o scripts/hpa-record.py (HPA + k6 no cenário auth + vídeo + prints do Grafana);
#   3. preenche a tabela "Resultado no EKS" com o scripts/fill-eks-hpa.py.
#
#   ./scripts/eks-hpa.sh                                   # descobre o ELB pelo Service lb
#   GATEWAY_URL=http://<elb> ./scripts/eks-hpa.sh          # ou usa a URL informada
#   VUS=100 DURATION=4m MAX=6 BROWSER=chromium ./scripts/eks-hpa.sh
#   DRY_RUN=1 ./scripts/eks-hpa.sh                         # só confere os pré-requisitos
#
# Não lê Secrets nem credenciais; usa o contexto atual do kubectl (aws eks update-kubeconfig).
# Ninguém deve fazer push na main durante a rodada: o deploy do CI reaplica replicas: 2 no gateway.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NS="${NS:-store}"
NAME="${NAME:-eks-hpa}"
VUS="${VUS:-100}"
DURATION="${DURATION:-4m}"
MIN="${MIN:-2}"
MAX="${MAX:-6}"
CPU="${CPU:-50}"
BROWSER="${BROWSER:-chromium}"   # Windows: msedge
PY="${PYTHON:-python3}"

ok()   { echo "ok   - $*"; }
fail() { echo "FALHA - $*" >&2; exit 1; }

# 1. pré-requisitos
command -v kubectl >/dev/null || fail "kubectl não encontrado"
command -v docker  >/dev/null || fail "docker não encontrado (o k6 roda em container)"
minor=$(kubectl version --client -o json | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["clientVersion"]["minor"].rstrip("+"))')
[ "$minor" -ge 34 ] || fail "kubectl 1.$minor: precisa do 1.34+ (--cpu=50% do autoscale)"
ok "kubectl 1.$minor"
ctx=$(kubectl config current-context)
case "$ctx" in *eks-store*) ok "contexto $ctx" ;; *) fail "contexto atual é '$ctx', não o eks-store (aws eks update-kubeconfig --name eks-store)";; esac
kubectl get deploy metrics-server -n kube-system >/dev/null 2>&1 || fail "metrics-server não instalado (ver 'Antes de começar' no roteiro)"
kubectl top nodes >/dev/null 2>&1 || fail "metrics-server ainda sem métricas (esperar ~1 min)"
ok "metrics-server respondendo"
kubectl get deploy gateway -n "$NS" >/dev/null || fail "deployment gateway não existe no namespace $NS"

if [ -z "${GATEWAY_URL:-}" ]; then
  host=$(kubectl get svc lb -n "$NS" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
  [ -n "$host" ] || fail "Service lb sem hostname do ELB ainda"
  GATEWAY_URL="http://$host"
fi
GATEWAY_URL="${GATEWAY_URL%/}"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$GATEWAY_URL/health-check" || true)
[ "$code" = 200 ] || fail "$GATEWAY_URL/health-check respondeu '$code'"
ok "gateway no ar em $GATEWAY_URL"

if kubectl get hpa gateway -n "$NS" >/dev/null 2>&1; then
  echo "aviso - já existe um HPA gateway; o hpa-record.py recria"
fi
[ -n "${DRY_RUN:-}" ] && { echo ">> DRY_RUN: pré-requisitos ok, nada foi executado"; exit 0; }

# restart do auth/account antes da rodada (achado do OOM no ensaio: ver #achado-oom-auth)
kubectl rollout restart deployment/auth deployment/account -n "$NS"
kubectl rollout status deployment/auth -n "$NS" --timeout=180s
kubectl rollout status deployment/account -n "$NS" --timeout=180s

# 2. rodada gravada
"$PY" scripts/hpa-record.py --base-url "$GATEWAY_URL" --name "$NAME" --label "EKS (eks-store)" \
  --namespace "$NS" --min "$MIN" --max "$MAX" --cpu "$CPU" --vus "$VUS" --duration "$DURATION" \
  --scenario auth --browser "$BROWSER"

# 3. tabela da documentação
"$PY" scripts/fill-eks-hpa.py --name "$NAME"
echo ">> confira: git diff docs/devops/load-testing.md && mkdocs build --strict"
