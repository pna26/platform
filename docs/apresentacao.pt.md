# Apresentação

Vídeo de **1 a 2 minutos** com o projeto e os bottlenecks, pedido pelo curso. Sem narração: cada cena tem
uma legenda curta explicando o que aparece na tela.

## Vídeo de apresentação (1–2 min)

<video controls preload="metadata" width="100%" src="../../assets/apresentacao-pna26.mp4"></video>

## Roteiro

| Tempo | Cena | O que aparece |
|---|---|---|
| 0:00–0:04 | Abertura | Grupo pna26 (Leonardo, Gabriel, Nicolas), disciplina e o projeto: loja em microsserviços |
| 0:04–0:21 | Arquitetura, CI/CD e EKS | Diagrama da [Home](index.md): Nginx → réplicas do gateway → Auth, Account, Exchange; Redis, PostgreSQL (RDS) e Kafka. Pipeline do [CI/CD](devops/ci-cd.md) e a VPC do [EKS](devops/eks.md) |
| 0:21–0:50 | Plataforma no EKS, ao vivo pelo ELB | `health-check` respondido por 2 réplicas do gateway, cadastro, login com cookie `HttpOnly` (token oculto), `GET /auth/me` com o token, `401` sem token e com `id-account` forjado, RBAC (`200` na própria conta, `403` ao listar as dos outros) |
| 0:50–1:23 | Bottlenecks | Um por página, com o número que mostra o efeito (tabela abaixo) |
| 1:23–1:35 | Teste de carga com HPA no EKS | Gravação de 19/11: k6 com 100 usuários e o HPA levando o gateway de 2 para 6 réplicas, 0 % de requisições HTTP com erro, p95 de 254 ms ([resultado](devops/load-testing.md#resultado-no-eks)) |
| 1:35–1:46 | Custos e próximos passos | ≈ US$ 0,29/h com tudo ligado ([Custos](devops/custos-sla.md)); HTTPS com domínio próprio, Multi-AZ no RDS, pool de conexões no Auth, Kafka com disco persistente |

Duração final: **1 min 45 s**. A demonstração ao vivo foi gravada em 20/11/2026 com o `eks-store` no ar
(deploy pelo CI); o câmbio ficou fora dela porque o exchange publicado ainda usava a AwesomeAPI, que
responde `429` para a AWS (ver [Exchange](servicos/exchange.md)).

## Bottlenecks no vídeo

| Bottleneck | Quem | Número mostrado | Página |
|---|---|---|---|
| Balanceamento de carga | Leonardo | Nginx distribuindo entre N réplicas do gateway; réplicas novas entram sem `reload` | [Balanceamento](bottlenecks/load-balancing.md) |
| Cache do token (Redis) | Leonardo | 3.001 requisições autenticadas → **1** chamada ao Auth (3.000 hits) | [Cache](bottlenecks/cache.md) |
| Varredura de vulnerabilidades | Leonardo | Trivy bloqueia CVE CRITICAL corrigível; imagem final com 0 HIGH/CRITICAL; OWASP ZAP no gateway | [Varredura](bottlenecks/vulnerability-scanning.md) |
| Cache da cotação | Gabriel | p95 da cotação de **49 ms → 4 ms** (−92 %) | [Cache da cotação](bottlenecks/exchange-cache.md) |
| Observabilidade | Gabriel | Grafana por réplica: com o HPA, as réplicas novas recebem carga só ≈ 1,5 min depois de prontas | [Observabilidade](bottlenecks/observability.md) |
| Mensageria (Kafka) | Nicolas | Cadastro em ~15 ms com o consumidor **ou** o Kafka fora; 0 eventos perdidos, 0 e-mails duplicados | [Mensageria](bottlenecks/messaging.md) |
| Autorização (RBAC) | Nicolas | Conta `USER` recebe `403` ao listar as contas dos outros e `200` na própria (ao vivo, pelo ELB) | [RBAC](bottlenecks/rbac.md) |

## Como o vídeo foi feito

As cenas das páginas desta documentação foram gravadas com **Playwright** (navegador automatizado, com a
legenda posta na própria página); a demonstração no EKS usa só HTTP pelo ELB público, como um usuário
qualquer, com o JWT e a senha da conta de demonstração ocultos na tela. O **ffmpeg** junta as cenas num
MP4 (H.264, 1280×720).
