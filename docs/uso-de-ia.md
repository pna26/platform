# Uso de IA

O enunciado permite o uso **parcial** de IA na implementação, desde que documentado: o que foi feito
com ajuda de IA, o que foi feito pelo grupo, e todos precisam saber explicar e defender qualquer parte
(inclusive a gerada por IA). Esta página é esse registro — cada integrante preenche a sua parte.

!!! danger "Regra do grupo"

    Nada gerado por IA entra sem que o responsável tenha lido, rodado e entendido. Antes da
    apresentação fazemos um walkthrough interno em que cada um explica as partes dos outros.

## Leonardo

**Ferramenta:** Claude Code (modelo Claude Opus 5.5, Anthropic), rodando no terminal com acesso ao
GitHub da organização.

**Como foi usado:** a partir do quadro do projeto e do material do curso (hands-on 2, 3, 4, 7 e 8 e da
página de bottlenecks), pedi para a IA implementar as minhas tarefas. A IA gerou o código, rodou os
testes e a plataforma localmente, e abriu os repositórios/PRs. O que eu revisei e alterei está registrado
abaixo.

| Parte | Feito com IA? | Observações |
|---|---|---|
| Gateway (`pna26/gateway-service`) | Sim — código gerado pela IA a partir do hands-on 3 | Mudanças em relação ao hands-on (ordem do filtro, 401/503, `AuthClient` com timeout) estão justificadas em [Gateway](servicos/gateway.md). 20 testes automatizados. |
| Cache do token no Redis | Sim | A escolha de cachear o `/auth/solve` no gateway e as regras de TTL/chave estão em [Cache](bottlenecks/cache.md). Testado com o Redis ligado e desligado. |
| Nginx (balanceamento) | Sim — a partir do hands-on 7 | Acrescentado `resolve` (escala sem reload) e bloqueio do actuator. |
| Pipeline de CI/CD reutilizável | Sim | Passos de Trivy simulados localmente antes de publicar. |
| Varredura de vulnerabilidades | Sim | As correções do Dockerfile e do manifesto k8s vieram dos próprios achados do Trivy. |
| `compose.yaml`, scripts e manifestos k8s do `platform` | Sim | Manifestos validados com Trivy e `nginx -t`. Em 12/11 rodaram num cluster kind igual ao `eks-store` (ensaio do Gabriel, ver [Testes de carga](devops/load-testing.md#ensaio-local-kind)); **no EKS real ainda não**. |
| Esta documentação (estrutura e textos das minhas partes) | Sim | Baseada no template do professor. |
| Medições (balanceamento, cache) | Executadas pela IA na minha máquina | Os números são os que saíram dos comandos mostrados nas páginas. |
| AWS no pipeline via OIDC (`service.yaml`, `infra.yaml`, [CI/CD](devops/ci-cd.md)) | Sim — a partir da proposta do Nicolas (role `github-actions-pna26`) | Validado com `actionlint`. Na revisão com a IA apareceram três pontos que não estavam no plano: a ordem do merge (o workflow reutilizável não pode pedir mais permissão que quem o chama), os repositórios `account`, `auth` e `notification-service` também precisarem de `id-token: write`, e a `AmazonEKSEditPolicy` não deixar o `infra` criar o `Role`/`RoleBinding` do Prometheus. |
| Revisão dos PRs do Nicolas (EKS e mensageria) | Sim — leitura dos diffs com a IA | A decisão de mergear e os comentários de revisão são meus. |
| Revisão do teste com HPA do Gabriel (#9) e ajustes (#10) | Sim | A IA buildou a doc, conferiu o painel novo contra o Prometheus do k8s e achou que o `kubectl autoscale --cpu=50%` não existe antes do kubectl 1.34 (o meu era 1.33). O `keepalive_time 10s` no `lb` veio do achado do Gabriel; validado com `nginx -t` na imagem do manifesto. |
| HTTPS na [Arquitetura](arquitetura.md), status da Home e roteiro da [Apresentação](apresentacao.md) | Sim | Montados a partir do código (cookie do Auth), do enunciado do curso e do estado dos repositórios. |
| Vídeo de apresentação e entrega de 20/11 (roteiro, gravação das páginas com Playwright, montagem com ffmpeg, `scripts/eks-hpa.sh` e `scripts/fill-eks-hpa.py`) | Sim | A IA escreveu os scripts e gravou as cenas da documentação. A demonstração no EKS só usa HTTP pelo ELB, com JWT e senha ocultos na tela. A tabela do EKS só recebe números do resumo do k6 e do HPA: o que não foi medido fica "—". |

**O que eu (Leonardo) fiz sem IA:**

- **Organização do projeto:** criei o [quadro do projeto](https://github.com/orgs/pna26/projects/1){target="_blank"}
  (26/10/2026) com os 22 cards de tarefa: a divisão do trabalho entre os três integrantes, o peso de cada
  entrega e o responsável por cada item.
- **Escopo do grupo:** defini o que cada um entrega (serviços, bottlenecks, partes da infra e da
  documentação) e as tarefas compartilhadas (compose local, walkthrough interno, apresentação).
- **Contrato da API:** escrevi o card "Contrato de API Gateway/Auth/Account", base da página de
  [Arquitetura](arquitetura.md): rotas do Gateway, Auth, Account e Exchange, rotas abertas, claims e
  duração do JWT, configuração do cookie e os pontos em aberto, cada um com responsável (rota de login no
  Account, `/exchange` × `/exchanges`, nomes dos serviços no compose/k8s, HTTPS no EKS).
- **Revisão do código** gerado pela IA: gateway, pipeline de CI/CD, `compose.yaml`, Nginx e manifestos k8s.
- **Direção do trabalho com a IA:** escolhi quais tarefas seriam feitas e aprovei cada ação externa:
  criação dos repositórios `gateway-service` e `platform`, PRs de CI nos repositórios do Nicolas (por fork,
  para ele revisar) e atualização do quadro.

**Onde a IA errou e foi corrigido:**

- O primeiro benchmark com/sem cache saiu inválido: a variável `TOKEN_CACHE_ENABLED` não estava sendo
  repassada pelo compose para o gateway, então as duas rodadas foram com cache. Foi corrigido e medido de
  novo; a conclusão honesta é que localmente a latência fica igual e o ganho é tirar carga do Auth.
- O Trivy aborta quando o `--ignorefile` aponta para um arquivo que não existe: o pipeline passou a só
  usar o `.trivyignore` quando ele existe (os repositórios do Account/Auth não têm).
- O `WebClient.Builder` não vem mais automático no Spring Boot 4 sem o `spring-boot-starter-webclient`
  (o teste de integração pegou isso).

## Nicolas

**Ferramenta:** Claude Code (modelo Claude Opus 5.5, Anthropic), no terminal e no app desktop, com acesso
ao clone local do `platform`, ao GitHub da organização e, nas etapas da AWS, ao AWS CLI logado por SSO.

**Como foi usado:** usei a IA para **consultar o que deveria ser feito corretamente** e para **estruturar o
código**. Antes de cada tarefa, ela lia o material do curso (Hands-on 1, 3 e 7 e as páginas de EKS e de
bottlenecks) e explicava o que o professor espera e qual é a forma certa de fazer. Depois montava a
estrutura (classes, migrations, manifestos, comandos) seguindo os padrões do curso e dos repositórios do
grupo. **Eu revisava o que a IA fazia, entendendo cada passo**: lia cada mudança, pedia a explicação do que
não estava claro e só seguia adiante quando entendia o que tinha sido feito e por quê.

| Parte | Feito com IA? | Observações |
|---|---|---|
| [Account](servicos/account.md) (`account` + `account-service`) | Sim — estrutura a partir do Hands-on 1 | Resource, Service, Parser, Model, Repository, Flyway e Dockerfile. A rota de login no Account (`POST /accounts/login`) veio do Hands-on 3. Todas as rotas testadas no compose. |
| [Auth](servicos/auth.md) (`auth` + `auth-service`) | Sim — estrutura a partir do Hands-on 3 | `JwtService` (JJWT) e `AuthService` chamando o Account via Feign. Testados register, login, token adulterado (401), `me` e logout. |
| [AWS](devops/aws.md) | Sim — explicação de cada passo; **os comandos e os cliques no console fui eu que fiz** | Budget, região, SSO no AWS CLI, ECR e a role OIDC do GitHub Actions. A IA explicava antes e conferia o resultado depois. |
| [EKS](devops/eks.md) | Sim — manifestos, `setup/eks-store.yaml`, RDS e a ordem de criar e apagar | Primeiro deploy à mão em 08/11. Em 16/11 a IA subiu o ambiente com a minha autorização, o deploy pelo CI funcionou nos meus três serviços e tudo foi apagado depois. |
| [Mensageria](bottlenecks/messaging.md) (Kafka) | Sim — código, compose, manifestos k8s, script de teste e a página | Adaptado do Hands-on 7 (transactional outbox, consumidor idempotente, DLT), trocando pedidos por contas. `scripts/messaging-test.sh` passando no compose; no EKS, o evento chegou ao `notification-service` em 16/11. |
| `notification-service` (repositório novo) | Sim | Consumidor Spring Kafka no padrão do Hands-on 7 e dos repositórios do grupo (Dockerfile, CI, k8s). |
| [Autorização (RBAC)](bottlenecks/rbac.md) | Sim — a IA achou a falha lendo o gateway e o account, e estruturou a solução | Qualquer usuário logado listava, via e apagava contas alheias. Papel `USER`/`ADMIN` no Account, claim `role` no JWT e regras no gateway. Testado no compose com duas contas e no CI (13 testes novos). A mudança no gateway do Leonardo foi por PR. |

**O que eu (Nicolas) fiz sem IA:**

- **AWS na mão:** rodei os comandos e fiz os cliques no console (budget, SSO, ECR, role do CI), conferindo
  cada resultado com a IA.
- **Decisões de projeto:** região `us-east-2` e alerta de custo de US$ 20/mês; RDS PostgreSQL para o banco
  no EKS; nós só em sub-redes privadas e RDS sem acesso público; apagar o ambiente sempre que não está em
  uso; na mensageria, o e-mail de boas-vindas como caso de uso, o consumidor num **serviço separado** e
  seguir o material do professor (Kafka) em vez do RabbitMQ do plano inicial; no RBAC, conta nova sempre
  `USER` e `ADMIN` só pelo banco.
- **Revisão:** li e entendi cada mudança antes de ir para o GitHub, e testei no compose e no cluster.
- **Regras para a IA:** nenhum segredo no Git, na tela ou em logs; commits sem coautoria da IA; mudanças
  no repositório de outro integrante só por PR, para o dono revisar. Aprovei cada ação externa.

**Onde a IA errou e foi corrigido:**

- **OIDC recusado no primeiro CI** (`Not authorized to perform sts:AssumeRoleWithWebIdentity`): a trust
  policy esperava `repo:pna26/*`, mas os repositórios usam o *subject* imutável do GitHub
  (`repo:pna26@<id>/...`). Corrigido e registrado em [AWS](devops/aws.md).
- **Nomes do ECR:** os primeiros repositórios (`gateway`, `auth`...) não batiam com os nomes que o pipeline
  usa (`*-service`). Foram recriados com o nome certo e os antigos, vazios, apagados.
- **`docker login` no ECR falhava no Windows:** o token passa do tamanho que o Gerenciador de Credenciais
  aceita. Resolvido com o `docker-credential-ecr-login`.
- **Permissão do CI no cluster:** a `AmazonEKSEditPolicy` não deixava criar o `Role` do Prometheus, e nem a
  `AmazonEKSAdminPolicy` resolveu, porque a checagem contra escalada de privilégio do Kubernetes não
  enxerga a política da AWS. A correção foi um `RoleBinding` para o papel `admin`, só no namespace `store`.
- **Chave reserva do JWT** no `application.yaml` do Auth: saiu na revisão de segurança, e agora o Auth não
  sobe sem `JWT_SECRET_KEY`.
- Usou `saveAndFlush`, que não existe no `CrudRepository` do account: o build pegou e virou `save` (a
  transação continua garantindo que conta e evento entram juntos).
- O script de mensageria falhou no Git Bash do Windows: `/opt/kafka/...` virava
  `C:/Program Files/Git/opt/...`. Corrigido com `MSYS_NO_PATHCONV=1`.
- O `smoke-test.sh` esperava que qualquer usuário conseguisse `GET /accounts` (200); com o RBAC passou a
  ser 403, e o script ganhou as novas checagens. O primeiro teste do `JwtService` não compilava (escape
  inválido numa expressão regular), e o build pegou antes do commit.

## Gabriel

**Ferramenta:** Claude Code (modelo Claude Opus 5.5, Anthropic), no VS Code, com acesso ao repositório
local.

**Como foi usado:** pedi para a IA olhar o projeto, achar as tarefas que eram minhas no
[quadro](https://github.com/orgs/pna26/projects/1){target="_blank"} e na página inicial da documentação, e
implementá-las com um commit por tarefa, seguindo os padrões que o grupo já usava (pipeline reutilizável,
manifestos k8s do gateway, estilo da documentação).

| Parte | Feito com IA? | Observações |
|---|---|---|
| Exchange (`pna26/exchange-service`) | Sim — código, testes, Dockerfile, k8s e CI | FastAPI + `httpx`. 21 testes passando; testado contra a AwesomeAPI real e de ponta a ponta pelo gateway (smoke test). |
| Bottleneck: cache da cotação | Sim | TTL, lock contra *stampede* e *stale-if-error*; medido no teste de carga (p95 49 → 4 ms). |
| Observabilidade (Prometheus + Grafana) | Sim | Configs do compose e do EKS, dashboard em JSON. Verificado no compose local e, em 12/11, no ensaio em kind com os manifestos do EKS; **no EKS real ainda não testado** (o cluster não estava no ar). |
| Testes de carga (k6) | Scripts sim; **números não** | Os resultados são os que saíram da bateria rodada na minha máquina em 05/11/2026 (`load/results/RESULTS.md`). A IA executou os comandos e escreveu a análise a partir dos números, do Prometheus e dos logs. |
| PaaS, custos e SLA | Sim — texto e contas | Preços conferidos em 05/11/2026 (EKS, EC2, Fargate, Classic LB, IPv4). |
| Teste com HPA: vídeo e prints do Grafana | Sim — scripts, execução e análise | Feito em 12/11 num cluster kind igual ao `eks-store` (o EKS não estava acessível), com `scripts/kind-up.sh` e `scripts/hpa-record.py`. A IA rodou o teste, gravou o vídeo (Edge sem janela, sem capturar a minha tela) e escreveu a análise a partir do k6, do Prometheus e dos logs. **É ensaio local, não o EKS:** a tabela do EKS é preenchida pelo `scripts/eks-hpa.sh` na rodada no cluster. |

**O que eu (Gabriel) fiz sem IA:**

- **Direção do trabalho:** pedi as tarefas, exigi um commit por tarefa e aprovei cada ação externa:
  criação do repositório `pna26/exchange-service` (público, como os outros), o PR por fork, a atualização
  do quadro e as instalações com permissão de administrador.
- **Ambiente:** login no GitHub CLI, autorização do escopo do Projects, ativação da virtualização/WSL e
  reinicialização para o Docker funcionar.
- _A completar pelo Gabriel: revisão do código, decisões próprias e o que estudou para o walkthrough._

**Onde a IA errou e foi corrigido:**

- **Perfil `smoke` do k6 não rodava nenhuma iteração:** subia de 0 a 1 usuário nos próprios 20 s.
  Percebido na primeira execução; virou 1 usuário fixo.
- **As 60 requisições do `setup()` entravam nas estatísticas** (cadastro/login das contas de teste),
  distorcendo rodadas curtas. Corrigido com a tag `phase=setup|test`.
- **O experimento de réplicas não achou ponto de quebra:** com a pausa entre ações, 400 usuários geram
  só ≈ 290 req/s. Foi criado o experimento de saturação (`THINK=0`), que achou o gargalo real no Auth.
- **A IA editou o `load-matrix.sh` enquanto ele rodava:** o bash lê o script aos poucos e passou a
  executar trechos errados, corrompendo a rodada `exchange-cache-off`. O experimento 3 foi rodado de novo.
- Pegos antes do commit: `head -n -1` (não funciona no macOS do grupo), 2 workers do `uvicorn` (cada
  scrape do Prometheus veria um processo diferente) e os `requests` do `lb` errados na conta de custos.
- **Faltava o alerta do suporte estendido do EKS** (US$ 0,60/h): apareceu ao conferir os preços.
