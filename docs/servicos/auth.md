# Auth

!!! info "Owner: Nicolas · Repositories: [pna26/auth](https://github.com/pna26/auth){target='_blank'} (interface) and [pna26/auth-service](https://github.com/pna26/auth-service){target='_blank'} (implementation)"

Auth handles the user's **identity**. It:

1. **signs up** users (`POST /auth/register`), forwarding to [Account](account.md);
2. handles **login** (`POST /auth/login`): asks Account to check the email and password and, if they match,
   generates a **JWT** and returns it in the `__store_jwt_token` cookie;
3. **resolves** the token for the gateway (`POST /auth/solve`): checks the signature and validity and returns
   the account id;
4. returns the logged-in user's data (`GET /auth/me`) and handles **logout** (deletes the cookie).

Auth **has no database**: Account stores the accounts and password hashes. It is a
**Spring Boot (Web MVC) + OpenFeign** service, with the JWT built by the **JJWT 0.13** library. It follows hands-on 3
of the course.

## Two repositories: interface and implementation { #dois-repositorios-interface-e-implementacao }

| Repository | Contents | Used by |
|---|---|---|
| `auth` | `AuthController` (interface with the routes and the cookie name) and the DTOs `RegisterIn`, `LoginIn` and `TokenOut` | `auth-service` (implements it) |
| `auth-service` | `AuthResource`, `AuthService`, `JwtService`, Dockerfile and `k8s/` | becomes the `auth-service` image |

`auth-service` also depends on the Account interface (`store:account:1.0.0`) and uses it as a Feign
client (`@EnableFeignClients(basePackages = "store.account")`): calling Account is calling a Java method,
and Feign turns it into `POST http://account:8080/accounts/login`.

## Routes { #rotas }

| Method | Route | Input | Output | Token at the gateway? |
|---|---|---|---|---|
| POST | `/auth/register` | `{ name, email, password }` | `200` | no |
| POST | `/auth/login` | `{ email, password }` | `200` + `Set-Cookie: __store_jwt_token=<jwt>` or `401` | no |
| GET | `/auth/logout` | — | `200` + empty cookie with `Max-Age=0` | no |
| GET | `/auth/me` | `id-account` header (set by the gateway) | `{ id, name, email }` | yes |
| POST | `/auth/solve` | `{ token }` | `{ idAccount }` or `401` | only the gateway calls it, internally |
| GET | `/auth/health-check` | — | `200` | yes |

## Login and token usage { #login-e-uso-do-token }

``` mermaid
sequenceDiagram
    actor U as User
    participant G as gateway
    participant A as auth
    participant C as account
    U->>G: POST /auth/login {email, password}
    G->>A: (open route)
    A->>C: POST /accounts/login (Feign)
    C-->>A: {id, name, email} or 401
    A->>A: signed JWT (HMAC-SHA256)
    A-->>U: 200 Set-Cookie: __store_jwt_token
    U->>G: GET /accounts (cookie)
    G->>A: POST /auth/solve {token}
    A->>A: checks signature, nbf and exp
    A-->>G: {idAccount}
    G->>C: GET /accounts + id-account
```

The gateway stores the `/auth/solve` result in Redis for a few minutes (see
[Cache](../bottlenecks/cache.md)), so Auth is not called on every request.

## JWT { #jwt }

| Field | Value |
|---|---|
| `jti` (id) | account id: this is what `/auth/solve` returns |
| `sub` | account name |
| `email` | account email |
| `iss` | `Insper::PMA` |
| `nbf` / `exp` | now / now + `JWT_DURATION` |
| Signature | HMAC-SHA256 with the `JWT_SECRET_KEY` key (base64 of 32 bytes) |

The token **carries no password** and is not encrypted (anyone can decode its base64 content); what
prevents forgery is the **signature**. Any change to the token, or a token signed with another
key, makes `parseSignedClaims` fail and `/auth/solve` respond `401`. The same applies to an expired
token.

### Cookie { #cookie }

| Attribute | Why |
|---|---|
| `HttpOnly` | the page's JavaScript cannot read the cookie, so an XSS cannot steal the token |
| `Secure` | the browser only sends the cookie over HTTPS (or to `localhost`) |
| `SameSite=None` | lets a front end on another origin call the API with the cookie (requires `Secure`) |
| `Path=/`, `Max-Age` = token duration | the cookie applies to all routes and expires along with the token |

`Secure` is why the browser does not store the cookie on EKS, which currently serves only over HTTP: see
[HTTPS (known limitation)](../arquitetura.md#https-limitacao-conhecida).

## Decisions { #decisoes }

| Decision | Reason |
|---|---|
| Auth without a database, checking the password in Account | A single owner of account data. Auth never sees the hash: it sends the password to Account and receives only `{ id, name, email }`. |
| *Stateless* JWT | Validation does not need a database lookup, only the key. Any Auth replica validates any token. |
| Wrong login → `401` | `AuthService` converts Account's `401`/`400` (which arrives as a `FeignException`) into `401 Invalid credentials`. |
| Invalid token → `401`, no stack trace | `JwtService` catches the `JwtException` and responds `401`; the gateway treats a `4xx` from solve as "not authenticated". |
| `JWT_SECRET_KEY` **required** | There is no default key in the code: without the variable, Auth does not start. A key in the public repository would let anyone generate valid tokens. In compose it comes from `.env` (`scripts/build.sh` generates a random one); on EKS, from the `auth-secrets` Secret. |
| JJWT pinned version (0.13.0) | The JJWT API changed a lot between versions (`parserBuilder` → `parser().verifyWith`); with a pinned version the build does not break on its own. |

## Limitations { #limitacoes }

| Limitation | How it would look in production |
|---|---|
| An Account error during sign-up (e.g. duplicate email, short password) reaches the user as `500` | handle the `FeignException` in `create` and forward Account's status (`400`, `409`) |
| `GET /auth/me` with an id that no longer exists (deleted account, token still valid) returns `500` | forward Account's `404` |
| Logout only deletes the cookie: the token remains valid until `exp` if someone has a copy | revoked token list (by `jti`) in Redis, checked by the gateway |
| ~~No roles (every logged-in user can do everything)~~ | done: `role` claim in the JWT and check in the gateway, see [Authorization (RBAC)](../bottlenecks/rbac.md) |

## Code { #codigo }

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

## Configuration { #configuracao }

| Variable | Default in code | Compose | EKS | Purpose |
|---|---|---|---|---|
| `JWT_SECRET_KEY` | — (required) | `.env` | Secret `auth-secrets` | JWT HMAC key, base64 of 32 bytes (`openssl rand -base64 32`) |
| `JWT_DURATION` | `3600000` (1 h) | `86400000` (24 h) | `3600000` (1 h) | token and cookie validity, in milliseconds |
| `JWT_HTTP_ONLY` | `true` | `true` | `true` | cookie `HttpOnly` attribute |

## How to test { #como-testar }

Through the gateway (compose at `http://localhost`):

``` bash
curl -s -X POST localhost/auth/register -H "Content-Type: application/json" \
  -d '{"name":"Ana","email":"ana@insper.edu.br","password":"segredo123"}'

curl -si -X POST localhost/auth/login -H "Content-Type: application/json" \
  -d '{"email":"ana@insper.edu.br","password":"segredo123"}' | grep -i set-cookie
# set-cookie: __store_jwt_token=eyJ...; Path=/; Max-Age=86400; ...; Secure; HttpOnly; SameSite=None

curl -s localhost/auth/me -H "Cookie: __store_jwt_token=eyJ..."
# {"id":"...","name":"Ana","email":"ana@insper.edu.br"}
```

A tampered token (change a letter in the middle) makes `/auth/me` respond `401`, because the signature
no longer matches. `scripts/smoke-test.sh` repeats this flow against compose or the EKS ELB.
