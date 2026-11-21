# Authorization (RBAC)

!!! info "Owner: Nicolas"

## Problem { #problema }

The gateway already guaranteed **who** the user is (valid JWT → `id-account` header), but not **what** they
could do. Anyone with an account, logged in with their own token, could do the following through the gateway:

| Request | What happened |
|---|---|
| `GET /accounts` | listed **all** accounts, with everyone's name and email |
| `GET /accounts/{id de outro}` | saw someone else's account |
| `DELETE /accounts/{id de outro}` | **deleted** someone else's account |
| `POST /accounts/login` | used an internal route, meant only for Auth |

This is the *Authentication & Authorization* bottleneck from the course table: access control is missing. The
suggested mitigation is **RBAC** (*role-based access control*): each account has a role and permissions
depend on the role.

## Solution { #solucao }

Two roles, `USER` and `ADMIN`. The role lives in Account, travels **inside the signed JWT** and is checked
at the gateway, the single entry point:

``` mermaid
sequenceDiagram
    autonumber
    actor u as User
    participant gw as gateway
    participant auth as auth
    participant acc as account
    u->>auth: POST /auth/login
    auth->>acc: POST /accounts/login
    acc-->>auth: id, name, email, role
    auth-->>u: cookie with the JWT (role claim)
    u->>gw: GET /accounts (cookie)
    gw->>auth: POST /auth/solve (or Redis cache)
    auth-->>gw: idAccount + role
    alt role not allowed
        gw-->>u: 403 Forbidden
    else role allowed
        gw->>acc: forwards with id-account
        acc-->>u: 200
    end
```

### Rules { #regras }

| Route chosen by the gateway | `USER` | `ADMIN` |
|---|---|---|
| `account` (`/accounts/**`) | only `GET /accounts/{o próprio id}` | everything |
| `auth`, `exchange` and the others | everything (being authenticated is enough) | everything |
| open routes (`register`, `login`, `logout`) | no token | no token |

No token or an invalid token is still **401**; a valid token without permission is **403**.

### Where each part lives { #onde-esta-cada-parte }

| Repository | Change |
|---|---|
| [`account`](https://github.com/pna26/account){target="_blank"} | `AccountOut` gained the `role` field |
| [`account-service`](https://github.com/pna26/account-service){target="_blank"} | `role` column (`NOT NULL DEFAULT 'USER'`, `CHECK` only `USER`/`ADMIN`) in migration `V2026.11.16.001__add_role.sql`; every account created is `USER`, and the role is never read from the request |
| [`auth-service`](https://github.com/pna26/auth-service){target="_blank"} | `role` claim in the JWT; `POST /auth/solve` returns `idAccount` **and** `role`; tests in `JwtServiceTest` |
| [`gateway-service`](https://github.com/pna26/gateway-service){target="_blank"} | `AccessPolicy` (the rules above), called by `AuthorizationFilter` after validating the token; the Redis cache now stores `id papel` |

### Decisions { #decisoes }

| Decision | Reason |
|---|---|
| Role **in the JWT**, checked at the **gateway** | The gateway already resolves the token on every request (with a cache): the check adds no network call. The services stay simple and only receive what they are allowed to receive. |
| The rule looks at the **route chosen** by the gateway, not the URL text | Variations such as `/accounts;x=1`, `/accounts/` or `%34%32` land on the same route and the same rule. Comparing URL strings would leave gaps. |
| **Deny by default** in `account` | `USER` has a single exception (their own account). Anything else, including a new route that account may gain, requires `ADMIN` without anyone having to remember to update the rule. |
| The "owner" is compared with the **decoded, parameter-free** segment, and only on an exact `/accounts/{id}` | `/accounts/42/x`, `/accounts/42;x=1` and `/accounts/42/../43` are denied. |
| A new account is always `USER`; `ADMIN` only **directly in the database** | Nobody can promote themselves through the API: a `"role": "ADMIN"` in the sign-up body is ignored (tested). |
| A token without the `role` claim (issued before RBAC) counts as `USER` | When in doubt, the least access. An old value in the cache (id only) counts as a *miss* and is resolved again. |
| Metric `gateway_rbac_decision_total{result=allow\|deny}` | Shows in Prometheus how many requests RBAC blocked. |

### Promote an account to ADMIN { #promover-uma-conta-a-admin }

There is no route for this, on purpose. On compose:

``` bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "UPDATE accounts.accounts SET role = '"'ADMIN'"' WHERE email = '"'admin@exemplo.com'"'"'
```

The new role takes effect from the **next login**, because the role that counts is the one in the token.

## Result { #resultado }

Tested on local compose (16/11/2026), with two new accounts (Ana and Bruno):

| Test | Expected | Got |
|---|---|---|
| Bruno's sign-up with `"role": "ADMIN"` in the body | `USER` account | `USER` |
| Ana (`USER`): `GET /accounts` | 403 | 403 |
| Ana (`USER`): `GET /accounts/{própria}` | 200 | 200 |
| Ana (`USER`): `GET /accounts/{Bruno}` | 403 | 403 |
| Ana (`USER`): `DELETE /accounts/{Bruno}` | 403 | 403 |
| Ana (`USER`): `GET /accounts;x=1` | 403 | 403 |
| Ana (`USER`): `GET /auth/me` | 200 | 200 |
| Ana promoted in the database, still with the old token: `GET /accounts` | 403 | 403 |
| Ana (`ADMIN`, new login): `GET /accounts` | 200 | 200 |
| Ana (`ADMIN`): `DELETE /accounts/{Bruno}` | 204 | 204 |

- `scripts/smoke-test.sh` gained the three RBAC checks (`USER` listing, viewing someone else's account and
  deleting → 403) and passed in full.
- Automated tests: 9 new ones in the gateway (`GatewayRoutingTest`, `AccessPolicyTest`,
  `TokenCacheTest`), 31 in total, and 4 in auth-service (`JwtServiceTest`, including swapping `USER` for
  `ADMIN` in the payload, which invalidates the signature → 401). All pass in CI.

## Known limitations { #limites-conhecidos }

| Limitation | How it would look in production |
|---|---|
| Demoting an `ADMIN` only takes effect when their token expires (up to 1 h) | short-lived token with a *refresh token*, or a revocation list checked by the gateway |
| Only two fixed roles, no fine-grained permissions | roles with permissions (e.g. `accounts:read`, `accounts:delete`) or an OAuth2/OIDC server (Keycloak, Cognito) |
| Promotion to `ADMIN` is manual, in the database | an administrative route with auditing, protected by RBAC itself |
| The check happens only at the gateway | inside the cluster, services trust whoever calls them (they are `ClusterIP`). In production, *network policies* or mTLS between services |
