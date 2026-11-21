# Presentation

A **1 to 2 minute** video showing the project and the bottlenecks, as required by the course. No narration: each scene has
a short caption explaining what is on screen.

## Presentation video (1–2 min) { #video-de-apresentacao-12-min }

<video controls preload="metadata" width="100%" src="../assets/apresentacao-pna26.mp4"></video>

## Script { #roteiro }

| Time | Scene | What is shown |
|---|---|---|
| 0:00–0:04 | Opening | Group pna26 (Leonardo, Gabriel, Nicolas), the course, and the project: a microservices store |
| 0:04–0:21 | Architecture, CI/CD, and EKS | Diagram from the [Home](index.md) page: Nginx → gateway replicas → Auth, Account, Exchange; Redis, PostgreSQL (RDS), and Kafka. The [CI/CD](devops/ci-cd.md) pipeline and the [EKS](devops/eks.md) VPC |
| 0:21–0:50 | Platform on EKS, live through the ELB | `health-check` answered by 2 gateway replicas, sign-up, login with an `HttpOnly` cookie (token hidden), `GET /auth/me` with the token, `401` without a token and with a forged `id-account`, RBAC (`200` on one's own account, `403` when listing other users' accounts) |
| 0:50–1:23 | Bottlenecks | One per page, with the number that shows the effect (table below) |
| 1:23–1:35 | Load test with HPA on EKS | Recording from 19/11: k6 with 100 users and the HPA scaling the gateway from 2 to 6 replicas, 0 % failed HTTP requests, p95 of 254 ms ([result](devops/load-testing.md#resultado-no-eks)) |
| 1:35–1:46 | Costs and next steps | ≈ US$ 0.29/h with everything running ([Costs](devops/custos-sla.md)); HTTPS with a custom domain, Multi-AZ on RDS, connection pooling in Auth, Kafka with persistent storage |

Final length: **1 min 45 s**. The live demo was recorded on 20/11/2026 with `eks-store` up
(deployed by CI); the exchange rate was left out of it because the deployed exchange service still used AwesomeAPI, which
returns `429` to AWS (see [Exchange](servicos/exchange.md)).

## Bottlenecks in the video { #bottlenecks-no-video }

| Bottleneck | Who | Number shown | Page |
|---|---|---|---|
| Load balancing | Leonardo | Nginx distributing across N gateway replicas; new replicas join without a `reload` | [Load balancing](bottlenecks/load-balancing.md) |
| Token cache (Redis) | Leonardo | 3,001 authenticated requests → **1** call to Auth (3,000 hits) | [Cache](bottlenecks/cache.md) |
| Vulnerability scanning | Leonardo | Trivy blocks fixable CRITICAL CVEs; final image with 0 HIGH/CRITICAL; OWASP ZAP on the gateway | [Scanning](bottlenecks/vulnerability-scanning.md) |
| Exchange rate cache | Gabriel | Exchange rate p95 from **49 ms → 4 ms** (−92 %) | [Exchange rate cache](bottlenecks/exchange-cache.md) |
| Observability | Gabriel | Grafana per replica: with the HPA, new replicas only receive load ≈ 1.5 min after becoming ready | [Observability](bottlenecks/observability.md) |
| Messaging (Kafka) | Nicolas | Sign-up in ~15 ms with the consumer **or** Kafka down; 0 lost events, 0 duplicate emails | [Messaging](bottlenecks/messaging.md) |
| Authorization (RBAC) | Nicolas | A `USER` account gets `403` when listing other users' accounts and `200` on its own (live, through the ELB) | [RBAC](bottlenecks/rbac.md) |

## How the video was made { #como-o-video-foi-feito }

The scenes from this documentation's pages were recorded with **Playwright** (an automated browser, with the
caption placed on the page itself); the EKS demo uses only HTTP through the public ELB, like any
regular user, with the JWT and the demo account's password hidden on screen. **ffmpeg** stitches the scenes into an
MP4 (H.264, 1280×720).
