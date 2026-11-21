# Auth

!!! info "Responsável: Nicolas · Repositórios: [pna26/auth](https://github.com/pna26/auth){target='_blank'} (interface) e [pna26/auth-service](https://github.com/pna26/auth-service){target='_blank'} (implementação)"

O Auth cuida da **identidade** do usuário. Ele:

1. **cadastra** (`POST /auth/register`), repassando ao [Account](account.md);
2. faz o **login** (`POST /auth/login`): pede ao Account para conferir e-mail e senha e, se der certo,
   gera um **JWT** e o devolve no cookie `__store_jwt_token`;
3. **resolve** o token para o gateway (`POST /auth/solve`): confere a assinatura e a validade e devolve
   o id da conta;
4. devolve os dados de quem está logado (`GET /auth/me`) e faz o **logout** (apaga o cookie).

O Auth **não tem banco**: quem guarda as contas e os hashes das senhas é o Account. É um serviço
**Spring Boot (Web MVC) + OpenFeign**, com o JWT feito pela biblioteca **JJWT 0.13**. Segue o hands-on 3
do curso.

## Dois repositórios: interface e implementação

| Repositório | Conteúdo | Quem usa |
|---|---|---|
| `auth` | `AuthController` (interface com as rotas e o nome do cookie) e os DTOs `RegisterIn`, `LoginIn` e `TokenOut` | o `auth-service` (implementa) |
| `auth-service` | `AuthResource`, `AuthService`, `JwtService`, Dockerfile e `k8s/` | vira a imagem `auth-service` |

O `auth-service` também depende da interface do Account (`store:account:1.0.0`) e a usa como cliente
Feign (`@EnableFeignClients(basePackages = "store.account")`): chamar o Account é chamar um método Java,
e o Feign transforma isso em `POST http://account:8080/accounts/login`.

## Rotas

| Método | Rota | Entrada | Saída | Token no gateway? |
|---|---|---|---|---|
| POST | `/auth/register` | `{ name, email, password }` | `200` | não |
| POST | `/auth/login` | `{ email, password }` | `200` + `Set-Cookie: __store_jwt_token=<jwt>` ou `401` | não |
| GET | `/auth/logout` | — | `200` + cookie vazio com `Max-Age=0` | não |
| GET | `/auth/me` | header `id-account` (posto pelo gateway) | `{ id, name, email }` | sim |
| POST | `/auth/solve` | `{ token }` | `{ idAccount }` ou `401` | só o gateway chama, por dentro |
| GET | `/auth/health-check` | — | `200` | sim |

## Login e uso do token

``` mermaid
sequenceDiagram
    actor U as Usuário
    participant G as gateway
    participant A as auth
    participant C as account
    U->>G: POST /auth/login {email, password}
    G->>A: (rota aberta)
    A->>C: POST /accounts/login (Feign)
    C-->>A: {id, name, email} ou 401
    A->>A: JWT assinado (HMAC-SHA256)
    A-->>U: 200 Set-Cookie: __store_jwt_token
    U->>G: GET /accounts (cookie)
    G->>A: POST /auth/solve {token}
    A->>A: confere assinatura, nbf e exp
    A-->>G: {idAccount}
    G->>C: GET /accounts + id-account
```

O gateway guarda o resultado do `/auth/solve` no Redis por alguns minutos (ver
[Cache](../bottlenecks/cache.md)); assim o Auth não é chamado em toda requisição.

## JWT

| Campo | Valor |
|---|---|
| `jti` (id) | id da conta: é o que o `/auth/solve` devolve |
| `sub` | nome da conta |
| `email` | e-mail da conta |
| `iss` | `Insper::PMA` |
| `nbf` / `exp` | agora / agora + `JWT_DURATION` |
| Assinatura | HMAC-SHA256 com a chave `JWT_SECRET_KEY` (base64 de 32 bytes) |

O token **não leva senha** e não é criptografado (qualquer um decodifica o conteúdo em base64); o que
impede a falsificação é a **assinatura**. Qualquer alteração no token, ou um token assinado com outra
chave, faz o `parseSignedClaims` falhar e o `/auth/solve` responder `401`. O mesmo vale para um token
vencido.

### Cookie

| Atributo | Por quê |
|---|---|
| `HttpOnly` | JavaScript da página não lê o cookie, então um XSS não consegue roubar o token |
| `Secure` | o navegador só manda o cookie por HTTPS (ou para `localhost`) |
| `SameSite=None` | permite que um front-end em outra origem chame a API com o cookie (exige `Secure`) |
| `Path=/`, `Max-Age` = duração do token | o cookie vale para todas as rotas e some junto com o token |

O `Secure` é o motivo de o navegador não guardar o cookie no EKS, que hoje atende só por HTTP: ver
[HTTPS (limitação conhecida)](../arquitetura.md#https-limitacao-conhecida).

## Decisões

| Decisão | Motivo |
|---|---|
| Auth sem banco, conferindo a senha no Account | Um só dono dos dados das contas. O Auth nunca vê o hash: manda a senha ao Account e recebe só `{ id, name, email }`. |
| JWT *stateless* | Para validar não precisa consultar banco, só a chave. Qualquer réplica do Auth valida qualquer token. |
| Login errado → `401` | O `AuthService` converte o `401`/`400` do Account (que chega como `FeignException`) em `401 Invalid credentials`. |
| Token inválido → `401`, sem stack trace | O `JwtService` captura a `JwtException` e responde `401`; o gateway trata `4xx` do solve como "não autenticado". |
| `JWT_SECRET_KEY` **obrigatória** | Não existe chave padrão no código: sem a variável, o Auth não sobe. Uma chave no repositório público deixaria qualquer pessoa gerar tokens válidos. No compose ela vem do `.env` (o `scripts/build.sh` gera uma aleatória); no EKS, do Secret `auth-secrets`. |
| JJWT com versão fixa (0.13.0) | A API do JJWT mudou bastante entre versões (`parserBuilder` → `parser().verifyWith`); com a versão fixa o build não quebra sozinho. |

## Limitações

| Limitação | Como ficaria em produção |
|---|---|
| Erro do Account no cadastro (ex.: e-mail duplicado, senha curta) chega ao usuário como `500` | tratar a `FeignException` no `create` e repassar o status do Account (`400`, `409`) |
| `GET /auth/me` com um id que não existe mais (conta apagada, token ainda válido) devolve `500` | repassar o `404` do Account |
| Logout só apaga o cookie: o token continua válido até o `exp` se alguém tiver uma cópia | lista de tokens revogados (por `jti`) no Redis, consultada pelo gateway |
| ~~Sem papéis (todo usuário logado pode tudo)~~ | feito: claim `role` no JWT e checagem no gateway, ver [Autorização (RBAC)](../bottlenecks/rbac.md) |

## Código

=== "AuthController.java (interface)"

    ``` { .java .copy linenums="1" }
    --8<-- "api/auth/src/main/java/store/auth/AuthController.java"
    ```

=== "AuthResource.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/auth-service/src/main/java/store/auth/AuthResource.java"
    ```

=== "AuthService.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/auth-service/src/main/java/store/auth/AuthService.java"
    ```

=== "JwtService.java"

    ``` { .java .copy linenums="1" }
    --8<-- "api/auth-service/src/main/java/store/auth/JwtService.java"
    ```

=== "application.yaml"

    ``` { .yaml .copy linenums="1" }
    --8<-- "api/auth-service/src/main/resources/application.yaml"
    ```

=== "Dockerfile"

    ``` { .dockerfile .copy linenums="1" }
    --8<-- "api/auth-service/Dockerfile"
    ```

=== "k8s/k8s.yaml"

    ``` { .yaml .copy linenums="1" }
    --8<-- "api/auth-service/k8s/k8s.yaml"
    ```

## Configuração

| Variável | Padrão no código | Compose | EKS | Uso |
|---|---|---|---|---|
| `JWT_SECRET_KEY` | — (obrigatória) | `.env` | Secret `auth-secrets` | chave HMAC do JWT, base64 de 32 bytes (`openssl rand -base64 32`) |
| `JWT_DURATION` | `3600000` (1 h) | `86400000` (24 h) | `3600000` (1 h) | validade do token e do cookie, em milissegundos |
| `JWT_HTTP_ONLY` | `true` | `true` | `true` | atributo `HttpOnly` do cookie |

## Como testar

Pelo gateway (compose em `http://localhost`):

``` bash
curl -s -X POST localhost/auth/register -H "Content-Type: application/json" \
  -d '{"name":"Ana","email":"ana@insper.edu.br","password":"segredo123"}'

curl -si -X POST localhost/auth/login -H "Content-Type: application/json" \
  -d '{"email":"ana@insper.edu.br","password":"segredo123"}' | grep -i set-cookie
# set-cookie: __store_jwt_token=eyJ...; Path=/; Max-Age=86400; ...; Secure; HttpOnly; SameSite=None

curl -s localhost/auth/me -H "Cookie: __store_jwt_token=eyJ..."
# {"id":"...","name":"Ana","email":"ana@insper.edu.br"}
```

Um token alterado (troque uma letra do meio) faz o `/auth/me` responder `401`, porque a assinatura
deixa de bater. O `scripts/smoke-test.sh` repete esse roteiro contra o compose ou o ELB do EKS.
