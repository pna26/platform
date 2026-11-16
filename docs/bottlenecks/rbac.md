# Autorização (RBAC)

!!! info "Responsável: Nicolas"

## Problema

O gateway já garantia **quem** é o usuário (JWT válido → header `id-account`), mas não **o que** ele
podia fazer. Qualquer pessoa com uma conta, logada com o próprio token, conseguia pelo gateway:

| Requisição | O que acontecia |
|---|---|
| `GET /accounts` | listava **todas** as contas, com nome e e-mail de todo mundo |
| `GET /accounts/{id de outro}` | via a conta de outra pessoa |
| `DELETE /accounts/{id de outro}` | **apagava** a conta de outra pessoa |
| `POST /accounts/login` | usava uma rota interna, feita só para o Auth |

É o gargalo *Authentication & Authorization* da tabela do curso: falta controle de acesso. A
mitigação sugerida é **RBAC** (*role-based access control*): cada conta tem um papel e a permissão
depende do papel.

## Solução

Dois papéis, `USER` e `ADMIN`. O papel mora no Account, viaja **dentro do JWT assinado** e é conferido
no gateway, o único ponto de entrada:

``` mermaid
sequenceDiagram
    autonumber
    actor u as Usuário
    participant gw as gateway
    participant auth as auth
    participant acc as account
    u->>auth: POST /auth/login
    auth->>acc: POST /accounts/login
    acc-->>auth: id, nome, e-mail, role
    auth-->>u: cookie com o JWT (claim role)
    u->>gw: GET /accounts (cookie)
    gw->>auth: POST /auth/solve (ou cache Redis)
    auth-->>gw: idAccount + role
    alt papel não pode
        gw-->>u: 403 Forbidden
    else papel pode
        gw->>acc: repassa com id-account
        acc-->>u: 200
    end
```

### Regras

| Rota escolhida pelo gateway | `USER` | `ADMIN` |
|---|---|---|
| `account` (`/accounts/**`) | só `GET /accounts/{o próprio id}` | tudo |
| `auth`, `exchange` e as demais | tudo (basta estar autenticado) | tudo |
| rotas abertas (`register`, `login`, `logout`) | sem token | sem token |

Sem token ou com token inválido continua sendo **401**; token válido sem permissão é **403**.

### Onde está cada parte

| Repositório | Mudança |
|---|---|
| [`account`](https://github.com/pna26/account){target="_blank"} | `AccountOut` ganhou o campo `role` |
| [`account-service`](https://github.com/pna26/account-service){target="_blank"} | coluna `role` (`NOT NULL DEFAULT 'USER'`, `CHECK` só `USER`/`ADMIN`) na migration `V2026.11.16.001__add_role.sql`; toda conta criada é `USER`, e o papel nunca é lido da requisição |
| [`auth-service`](https://github.com/pna26/auth-service){target="_blank"} | claim `role` no JWT; `POST /auth/solve` devolve `idAccount` **e** `role`; testes em `JwtServiceTest` |
| [`gateway-service`](https://github.com/pna26/gateway-service){target="_blank"} | `AccessPolicy` (as regras acima), chamada pelo `AuthorizationFilter` depois de validar o token; o cache Redis passou a guardar `id papel` |

### Decisões

| Decisão | Motivo |
|---|---|
| Papel **no JWT**, conferido no **gateway** | O gateway já resolve o token em toda requisição (com cache): a checagem não acrescenta nenhuma chamada de rede. Os serviços continuam simples e só recebem o que podem receber. |
| A regra olha a **rota escolhida** pelo gateway, não o texto da URL | Variações como `/accounts;x=1`, `/accounts/` ou `%34%32` caem na mesma rota e na mesma regra. Comparar strings da URL deixaria brechas. |
| **Negado por padrão** no `account` | O `USER` tem uma única exceção (a própria conta). Qualquer outra coisa, inclusive uma rota nova que o account ganhar, exige `ADMIN` sem precisar lembrar de atualizar a regra. |
| O "dono" é comparado com o segmento **decodificado e sem parâmetros**, e só em `/accounts/{id}` exato | `/accounts/42/x`, `/accounts/42;x=1` e `/accounts/42/../43` são negados. |
| Conta nova é sempre `USER`; `ADMIN` só **direto no banco** | Ninguém consegue se autopromover pela API: um `"role": "ADMIN"` no corpo do cadastro é ignorado (testado). |
| Token sem o claim `role` (emitido antes do RBAC) vale `USER` | Na dúvida, o menor acesso. Valor antigo no cache (só o id) conta como *miss* e é resolvido de novo. |
| Métrica `gateway_rbac_decision_total{result=allow\|deny}` | Mostra no Prometheus quantas requisições o RBAC barrou. |

### Promover uma conta a ADMIN

Não existe rota para isso, de propósito. No compose:

``` bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "UPDATE accounts.accounts SET role = '"'ADMIN'"' WHERE email = '"'admin@exemplo.com'"'"'
```

O novo papel vale a partir do **próximo login**, porque o papel que conta é o que está no token.

## Resultado

Testado no compose local (16/11/2026), com duas contas novas (Ana e Bruno):

| Teste | Esperado | Obtido |
|---|---|---|
| Cadastro do Bruno com `"role": "ADMIN"` no corpo | conta `USER` | `USER` |
| Ana (`USER`): `GET /accounts` | 403 | 403 |
| Ana (`USER`): `GET /accounts/{própria}` | 200 | 200 |
| Ana (`USER`): `GET /accounts/{Bruno}` | 403 | 403 |
| Ana (`USER`): `DELETE /accounts/{Bruno}` | 403 | 403 |
| Ana (`USER`): `GET /accounts;x=1` | 403 | 403 |
| Ana (`USER`): `GET /auth/me` | 200 | 200 |
| Ana promovida no banco, ainda com o token antigo: `GET /accounts` | 403 | 403 |
| Ana (`ADMIN`, novo login): `GET /accounts` | 200 | 200 |
| Ana (`ADMIN`): `DELETE /accounts/{Bruno}` | 204 | 204 |

- `scripts/smoke-test.sh` ganhou as três checagens de RBAC (`USER` listando, vendo conta de outro e
  apagando → 403) e passou inteiro.
- Testes automatizados: 9 novos no gateway (`GatewayRoutingTest`, `AccessPolicyTest`,
  `TokenCacheTest`), 31 no total, e 4 no auth-service (`JwtServiceTest`, incluindo a troca de `USER` por
  `ADMIN` no payload, que invalida a assinatura → 401). Todos passam no CI.

## Limites conhecidos

| Limite | Como ficaria em produção |
|---|---|
| Rebaixar um `ADMIN` só vale quando o token dele expira (até 1 h) | token de vida curta com *refresh token*, ou uma lista de revogação consultada pelo gateway |
| Só dois papéis fixos, sem permissões finas | papéis com permissões (ex. `accounts:read`, `accounts:delete`) ou um servidor OAuth2/OIDC (Keycloak, Cognito) |
| Promoção a `ADMIN` é manual, no banco | rota administrativa com auditoria, protegida pelo próprio RBAC |
| A checagem acontece só no gateway | dentro do cluster, os serviços confiam em quem os chama (são `ClusterIP`). Em produção, *network policies* ou mTLS entre serviços |
