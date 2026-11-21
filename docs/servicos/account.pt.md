# Account

!!! info "Responsável: Nicolas · Repositórios: [pna26/account](https://github.com/pna26/account){target='_blank'} (interface) e [pna26/account-service](https://github.com/pna26/account-service){target='_blank'} (implementação)"

O Account é o dono do **cadastro de contas** da loja. Ele:

1. cria, lista, busca e apaga contas (`/accounts`);
2. guarda a senha só como **hash** (a senha nunca é salva nem devolvida);
3. confere e-mail e senha no **login**, a pedido do Auth (`POST /accounts/login`);
4. registra um evento `AccountCreated` no **outbox**, na mesma transação da conta, para o
   [notification-service](../bottlenecks/messaging.md) mandar o e-mail de boas-vindas.

É um serviço **Spring Boot (Web MVC) + Spring Data JPA** sobre **PostgreSQL**, com o schema criado pelo
**Flyway**. Segue o hands-on 1 do curso (e a rota de login do hands-on 3).

``` mermaid
flowchart LR
    gw[gateway] -->|"/accounts/**<br/>+ id-account"| acc[account]
    auth -->|"Feign<br/>POST /accounts e /accounts/login"| acc
    acc -->|JPA| db[("PostgreSQL<br/>schema accounts")]
    acc -.->|"outbox → Kafka"| k[["account-events"]]
```

## Dois repositórios: interface e implementação

Como no curso, cada serviço Java tem **dois** repositórios:

| Repositório | Conteúdo | Quem usa |
|---|---|---|
| `account` | `AccountController` (interface com as rotas, anotada com `@FeignClient`) e os DTOs `AccountIn` e `AccountOut` | o `account-service` (implementa) e o `auth-service` (chama) |
| `account-service` | a implementação: `AccountResource`, `AccountService`, JPA, Flyway, Dockerfile e `k8s/` | vira a imagem `account-service` |

A interface é publicada como biblioteca Maven (`store:account:1.0.0`). Assim o **contrato fica num
lugar só**: o `AccountResource` implementa o `AccountController`, e o Auth usa a mesma interface como
cliente Feign (`http://account:8080`). Se uma rota mudar, os dois lados deixam de compilar até serem
atualizados.

## Rotas

| Método | Rota | Entrada | Saída |
|---|---|---|---|
| POST | `/accounts` | `{ name, email, password }` | `201` + `Location: /accounts/{id}` |
| GET | `/accounts` | — | `[{ id, name, email }]` |
| GET | `/accounts/{id}` | — | `{ id, name, email }` ou `404` |
| DELETE | `/accounts/{id}` | — | `204` |
| POST | `/accounts/login` | `{ email, password }` | `{ id, name, email }` ou `401` (interno, quem chama é o Auth) |
| GET | `/accounts/health-check` | — | `200` |

Pelo gateway, todas as rotas de `/accounts/**` exigem o cookie do token. Para criar conta sem estar
logado, o caminho é o `POST /auth/register` do [Auth](auth.md), que chama o `POST /accounts`.

| Status | Quando |
|---|---|
| `400` | senha vazia ou com menos de 6 caracteres; e-mail vazio |
| `401` | login com e-mail ou senha errados |
| `404` | `GET /accounts/{id}` de um id que não existe |
| `500` | e-mail já cadastrado (ver [Limitações](#limitacoes)) |

## Fluxo do cadastro

``` mermaid
sequenceDiagram
    participant A as auth
    participant R as AccountResource
    participant S as AccountService
    participant DB as PostgreSQL
    A->>R: POST /accounts {name, email, password}
    R->>S: create(Account)
    S->>S: valida senha (≥ 6) e e-mail
    S->>S: hash = Base64(SHA-256(senha))
    S->>DB: BEGIN
    S->>DB: INSERT accounts.accounts
    S->>DB: INSERT accounts.outbox (AccountCreated)
    S->>DB: COMMIT
    R-->>A: 201 Location: /accounts/{id}
```

O login faz o caminho inverso: calcula o hash da senha recebida e procura uma conta com aquele e-mail
**e** aquele hash (`findByEmailAndHashPassword`). Se não achar, responde `401` sem dizer se o erro
foi no e-mail ou na senha.

## Decisões

| Decisão | Motivo |
|---|---|
| Camadas `Resource` → `Service` → `Repository`, com `Account` (domínio), `AccountModel` (JPA) e `AccountParser` | Padrão do curso. A entidade JPA não sai do serviço e o DTO de saída (`AccountOut`) não tem campo de senha, então não há como vazar o hash por engano. |
| Id `UUID` gerado pelo JPA | O id não revela quantas contas existem nem a ordem de cadastro. |
| `email` com `UNIQUE` no banco | A regra "um e-mail, uma conta" vale mesmo com várias réplicas do account criando ao mesmo tempo. |
| Schema `accounts` (e não `public`) criado pelo Flyway | Cada serviço tem o seu schema no mesmo PostgreSQL (o notification usa `notification`). As migrações ficam versionadas no repositório e rodam sozinhas no boot, inclusive no RDS. |
| `404` quando o id não existe | O cliente distingue "conta não existe" de erro do servidor. |
| `401` no login errado, sem dizer se errou o e-mail ou a senha | Não ajuda quem tenta descobrir quais e-mails estão cadastrados. O Auth repassa como `401`. |
| Conta e evento na mesma transação (`@Transactional`) | É o [transactional outbox](../bottlenecks/messaging.md) da mensageria: se o INSERT da conta falhar (e-mail duplicado), o evento também é desfeito. |
| Banco e Kafka por variável de ambiente | O mesmo jar roda no compose (`db`), no Kubernetes local e no EKS (endpoint do RDS no ConfigMap, usuário e senha no Secret `account-secrets`). |

## Limitações

Comportamentos que vieram do código do curso e ficaram como estão. Fazem parte do que explicaríamos
numa versão de produção:

| Limitação | Como ficaria em produção |
|---|---|
| Hash `SHA-256` sem *salt*: duas contas com a mesma senha têm o mesmo hash | `BCrypt` ou `Argon2` (lentos de propósito e com *salt*), por exemplo o `PasswordEncoder` do Spring Security |
| E-mail duplicado devolve `500` | tratar a `DataIntegrityViolationException` e devolver `409 Conflict` |
| Qualquer usuário logado pode listar e apagar qualquer conta (`GET /accounts`, `DELETE /accounts/{id}`) | autorização por papel ou dono do recurso: comparar o `id-account` do gateway com o `{id}` da rota, ou exigir papel de administrador |
| `POST /accounts/login` também passa pelo gateway (com token) | deixar a rota só na rede interna, como o `/auth/solve` |

## Código

=== "AccountController.java (interface)"

    ``` { .java .copy linenums="1" }
    --8<-- "api/account/src/main/java/store/account/AccountController.java"
    ```

=== "AccountResource.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/account-service/src/main/java/store/account/AccountResource.java"
    ```

=== "AccountService.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/account-service/src/main/java/store/account/AccountService.java"
    ```

=== "AccountModel.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/account-service/src/main/java/store/account/AccountModel.java"
    ```

=== "Migrações (Flyway)"

    ``` { .sql .copy title="V2026.08.24.001__create_schema.sql" }
    --8<-- "api/account-service/src/main/resources/db/migration/V2026.08.24.001__create_schema.sql"
    ```

    ``` { .sql .copy title="V2026.08.24.002__create_table.sql" }
    --8<-- "api/account-service/src/main/resources/db/migration/V2026.08.24.002__create_table.sql"
    ```

=== "application.yaml"

    ``` { .yaml .copy linenums="1" }
    --8<-- "api/account-service/src/main/resources/application.yaml"
    ```

=== "Dockerfile"

    ``` { .dockerfile .copy linenums="1" }
    --8<-- "api/account-service/Dockerfile"
    ```

=== "k8s/k8s.yaml"

    ``` { .yaml .copy linenums="1" }
    --8<-- "api/account-service/k8s/k8s.yaml"
    ```

## Configuração

| Variável | Compose | EKS | Uso |
|---|---|---|---|
| `DATABASE_HOST` | `db` | endpoint do RDS (ConfigMap) | host do PostgreSQL |
| `DATABASE_PORT` | `5432` | `5432` | porta |
| `DATABASE_DB` | `store` | `store` | nome do banco |
| `DATABASE_USERNAME` / `DATABASE_PASSWORD` | `.env` | Secret `account-secrets` | credenciais do banco |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | `kafka:9092` | Kafka do outbox |

## Como testar

Direto no account, sem o gateway (por exemplo com `kubectl port-forward svc/account 8080` ou expondo
a porta no compose):

``` bash
curl -i -X POST localhost:8080/accounts -H "Content-Type: application/json" \
  -d '{"name":"Ana","email":"ana@insper.edu.br","password":"segredo123"}'
# HTTP/1.1 201  Location: http://localhost:8080/accounts/<id>

curl -s -X POST localhost:8080/accounts/login -H "Content-Type: application/json" \
  -d '{"email":"ana@insper.edu.br","password":"segredo123"}'
# {"id":"<id>","name":"Ana","email":"ana@insper.edu.br"}
```

Pela plataforma inteira, o `scripts/smoke-test.sh` cadastra, faz login e chama `/accounts` pelo gateway.
