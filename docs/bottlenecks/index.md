# Bottlenecks

Gargalos identificados na plataforma e o que foi feito em cada um (o enunciado pede ao menos 2 por
integrante).

| Gargalo | Problema | Mitigação | Responsável | Status |
|---|---|---|---|---|
| [Balanceamento de carga](load-balancing.md) | Um gateway só é ponto único de falha e de capacidade | Nginx na frente de N réplicas do gateway (compose e EKS) | Leonardo | pronto |
| [Cache](cache.md) | Toda requisição autenticada chamava o Auth (`/auth/solve`) | Redis compartilhado entre as réplicas: token → id da conta | Leonardo | pronto |
| [Varredura de vulnerabilidades](vulnerability-scanning.md) | Dependências, imagens e manifestos com falhas conhecidas | Trivy (SCA, imagem, segredos, IaC) no pipeline + OWASP ZAP (DAST) | Leonardo | pronto |
| [Cache da cotação](exchange-cache.md) | Toda cotação chamava a API externa (latência de terceiros, limite de requisições) | Cache em memória por par: TTL 30 s, lock contra *stampede*, *stale-if-error* | Gabriel | pronto |
| [Observabilidade](observability.md) | Sem visibilidade do comportamento sob carga | Prometheus (descoberta de todas as réplicas) + Grafana com dashboard versionado, no compose e no EKS | Gabriel | pronto |
| [Mensageria](messaging.md) | E-mail de boas-vindas no caminho do cadastro: latência e queda do provedor viram latência e queda do cadastro | Kafka: o account publica `AccountCreated` (transactional outbox) e o notification-service consome de forma idempotente, com retries e DLT | Nicolas | pronto (compose e EKS, com deploy pelo CI em 16/11) |
| [Autorização (RBAC)](rbac.md) | Qualquer usuário logado listava, via e apagava as contas de outras pessoas | Papel (`USER`/`ADMIN`) no Account, claim `role` no JWT e regras no gateway: `USER` só vê a própria conta (403 no resto) | Nicolas | pronto (compose) |
