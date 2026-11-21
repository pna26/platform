# Bottlenecks

Bottlenecks identified in the platform and what was done about each one (the assignment asks for at
least 2 per member).

| Bottleneck | Problem | Mitigation | Owner | Status |
|---|---|---|---|---|
| [Load balancing](load-balancing.md) | A single gateway is a single point of failure and of capacity | Nginx in front of N gateway replicas (compose and EKS) | Leonardo | done |
| [Cache](cache.md) | Every authenticated request called Auth (`/auth/solve`) | Redis shared across the replicas: token → account id | Leonardo | done |
| [Vulnerability scanning](vulnerability-scanning.md) | Dependencies, images and manifests with known flaws | Trivy (SCA, image, secrets, IaC) in the pipeline + OWASP ZAP (DAST) | Leonardo | done |
| [Exchange rate cache](exchange-cache.md) | Every quote called the external API (third-party latency, rate limit) | In-memory cache per pair: 30 s TTL, lock against *stampede*, *stale-if-error* | Gabriel | done |
| [Observability](observability.md) | No visibility into behavior under load | Prometheus (discovers every replica) + Grafana with a versioned dashboard, on compose and on EKS | Gabriel | done |
| [Messaging](messaging.md) | Welcome e-mail on the sign-up path: provider latency and outages become sign-up latency and outages | Kafka: account publishes `AccountCreated` (transactional outbox) and notification-service consumes it idempotently, with retries and DLT | Nicolas | done (compose and EKS, with deploy from CI on 16/11) |
| [Authorization (RBAC)](rbac.md) | Any logged-in user could list, view and delete other people's accounts | Role (`USER`/`ADMIN`) in Account, `role` claim in the JWT and rules in the gateway: `USER` only sees their own account (403 for the rest) | Nicolas | done (compose) |
