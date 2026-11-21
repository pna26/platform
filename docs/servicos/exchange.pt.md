# Exchange

!!! info "Responsável: Gabriel — repositório [pna26/exchange-service](https://github.com/pna26/exchange-service){target="_blank"}"

Microsserviço de **câmbio** em **Python (FastAPI)**. Recebe um par de moedas, busca a cotação na
**PTAX do Banco Central** (API Olinda do BACEN, pública e sem cadastro) e devolve o preço de compra e de
venda para o usuário logado. Segue o [serviço do BACEN](https://insper.github.io/platform/pt/2026.2/exercises/exchange/bacen/){target="_blank"}
sugerido pelo professor: as APIs de câmbio gratuitas (a AwesomeAPI, usada antes) não respondem para IPs
da AWS, e no EKS devolviam `429` para o IP do NAT.

``` mermaid
flowchart LR
    u([Usuário]) -->|"GET /exchange/USD/BRL<br/>cookie JWT"| lb[lb · Nginx] --> gw[gateway]
    gw -->|"+ header id-account"| ex[exchange]
    ex <-->|"hit (30 s)"| cache[(cache em memória)]
    ex -->|"miss"| api([PTAX · BACEN])
```

## Contrato

| Método | Rota | Entrada | Saída |
|---|---|---|---|
| GET | `/exchange/{from}/{to}` | header `id-account` (posto pelo gateway) | `{ sell, buy, date, id-account }` |
| GET | `/exchange/currencies` | — | lista de `symbol` e `name`: as moedas que a PTAX cota (além do BRL) |

O gateway também aceita `/exchanges/{from}/{to}` e reescreve para `/exchange/...` (o serviço também
aceita o alias). Toda resposta de cotação traz o header **`X-Cache`**: `miss` (a PTAX foi chamada),
`hit` (memória) ou `stale` (a PTAX falhou e a última cotação foi servida).

``` bash
curl -H "Cookie: __store_jwt_token=$TOKEN" localhost:8080/exchange/USD/BRL
```

``` json
{ "sell": 5.8889, "buy": 5.8883, "date": "2026-11-19 13:04:18", "id-account": "8f1c..." }
```

| Status | Quando |
|---|---|
| 200 | cotação (do cache ou da PTAX) |
| 400 | moeda que não tem 3 letras (`/exchange/US/BRL`) |
| 401 | sem token (barrado no gateway) ou sem `id-account` (não passou pelo gateway) |
| 404 | moeda que a PTAX não cota (`/exchange/XYZ/BRL`) |
| 503 | PTAX fora do ar **e** sem cotação recente em cache (o roteiro do curso usa `502`; mantivemos o `503` do contrato da plataforma) |

### Como a taxa é calculada

Para cada moeda, o serviço consulta `CotacaoMoedaPeriodo` numa janela de **7 dias** (fins de semana e
feriados não têm cotação) e usa o **último boletim** da lista. A PTAX só cota moeda → BRL, então o real
vale 1 e qualquer par sai pela taxa cruzada via BRL, com `c` = compra e `v` = venda em BRL:

`sell = v(from) / c(to)` · `buy = c(from) / v(to)` (6 casas decimais)

São 10 moedas (AUD, CAD, CHF, DKK, EUR, GBP, JPY, NOK, SEK e USD, mais o BRL). Chamada real em
20/11/2026: `USD/BRL` → `sell 5.8889`, `buy 5.8883` (boletim de 19/11 13:04); `USD/EUR` → `sell 0.910763`,
`buy 0.910487`.

!!! warning "Limites da PTAX"

    - Só há boletim em dias úteis, entre ≈ 10h e 13h (Brasília): fora disso a resposta é o último
      boletim publicado, não uma cotação em tempo real.
    - É um serviço público do BCB: **não fazer teste de carga nela**. O cache fica sempre ligado; num
      teste de carga o k6 bate na memória (conte os `X-Cache: miss`).

## Decisões

| Decisão | Motivo |
|---|---|
| FastAPI + `httpx` assíncrono | Enquanto espera a API externa, o processo atende outras requisições (o serviço é quase só I/O). |
| PTAX do BACEN como provedor (`EXCHANGE_PROVIDER=ptax`) | Pública, sem chave, responde para IPs da AWS (a AwesomeAPI devolvia `429` no EKS). Venda → `sell`, compra → `buy` (por isso `sell ≥ buy`). |
| AwesomeAPI só como opção (`EXCHANGE_PROVIDER=awesomeapi`) | O modo antigo continua disponível, com a PTAX como fallback quando a AwesomeAPI falha (`EXCHANGE_FALLBACK`). |
| `pip` + `requirements.txt`, não `uv` | O roteiro do curso usa `uv`; o pipeline reutilizável do `platform` instala dependências Python com `pip`, então mantivemos o mesmo Dockerfile. |
| Cache da cotação por 30 s | É o bottleneck deste serviço: ver [Cache da cotação](../bottlenecks/exchange-cache.md). |
| Mesma moeda (`BRL/BRL`) responde `1.0` sem chamar a PTAX | Evita uma chamada externa inútil. |
| `401` sem `id-account` | O serviço só deve ser alcançado pelo gateway; sem o header, a requisição veio de outro lugar. |
| 1 processo `uvicorn` por container | As métricas do `prometheus_client` ficam na memória do processo; com vários workers cada scrape veria um diferente. Escala com réplicas (`EXCHANGE_REPLICAS` / `replicas` no k8s). |
| Porta 8080 e nome `exchange` | O gateway encaminha para `http://exchange:8080` no compose e no k8s. |
| `/metrics` fora de `/exchange/**` | O gateway só encaminha `/exchange/**`, então as métricas ficam só na rede interna (o Prometheus raspa direto). |
| Imagem `python:3.12-slim`, usuário 1001, FS somente leitura no k8s | Menos pacotes para o Trivy e mesmo padrão de segurança dos outros serviços. |

## Configuração

| Variável | Padrão | |
|---|---|---|
| `EXCHANGE_PROVIDER` | `ptax` | `ptax` ou `awesomeapi` |
| `PTAX_URL` | `https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata` | API PTAX do BACEN |
| `CACHE_TTL` | `30` | segundos que uma cotação vale (`0` desliga o cache) |
| `CACHE_STALE_TTL` | `600` | por quanto tempo a última cotação serve se a PTAX cair |
| `UPSTREAM_TIMEOUT` | `3` | timeout das chamadas externas, em segundos |
| `EXCHANGE_API_URL` | `https://economia.awesomeapi.com.br/json/last` | só no modo `awesomeapi` |
| `EXCHANGE_API_TOKEN` | — | só no modo `awesomeapi` (opcional) |
| `EXCHANGE_FALLBACK` | `ptax` | só no modo `awesomeapi`: usa a PTAX se a AwesomeAPI falhar (`none` desliga) |

A métrica `exchange_upstream_request_seconds{outcome}` (painel "API externa" do Grafana) mede o provedor
principal, então passa a mostrar as chamadas à PTAX; um par sem BRL (`USD/EUR`) faz duas chamadas.

No compose, `EXCHANGE_CACHE_TTL` e `EXCHANGE_REPLICAS` (no `.env`) viram `CACHE_TTL` e o número de réplicas.

## Código

=== "app/main.py"

    ``` { .python .copy linenums="1" }
    --8<-- "api/exchange-service/app/main.py"
    ```

=== "app/provider.py"

    ``` { .python .copy linenums="1" }
    --8<-- "api/exchange-service/app/provider.py"
    ```

=== "app/cache.py"

    ``` { .python .copy linenums="1" }
    --8<-- "api/exchange-service/app/cache.py"
    ```

=== "Dockerfile"

    ``` { .dockerfile .copy }
    --8<-- "api/exchange-service/Dockerfile"
    ```

=== "k8s/k8s.yaml"

    ``` { .yaml .copy }
    --8<-- "api/exchange-service/k8s/k8s.yaml"
    ```

## Testes

`pytest` com as APIs externas simuladas (`httpx.MockTransport`), rodando no pipeline de CI
([CI/CD](../devops/ci-cd.md)): PTAX (último boletim, taxa cruzada, X→BRL e BRL→X, moeda desconhecida →
404, PTAX fora → 503, `X-Cache` miss/hit/stale, `/exchange/currencies`, alias `/exchanges`); no modo
`awesomeapi`: contrato da resposta, `sell ≥ buy`, 400/401/404/503, `429` virando fallback para a PTAX ou `503`, mesma moeda sem chamada externa, 50 requisições → 1 chamada externa, e o cache (TTL,
*stale-if-error*, 100 requisições simultâneas → 1 chamada). O [smoke test](../como-rodar.md#smoke-test)
confere a rota de ponta a ponta pelo gateway, inclusive que o `id-account` que chega é o do usuário logado.

Verificado em 20/11/2026: **44 testes passando**; serviço rodando localmente contra a PTAX real
(`USD/BRL` → 200 com `X-Cache: miss` e depois `hit`, `XYZ/BRL` → 404, sem `id-account` → 401).
Antes, em 05/11/2026, com a AwesomeAPI: 21 testes; serviço rodando contra a AwesomeAPI real (200 com
`sell ≥ buy` para `USD/BRL` e `EUR/BRL`, 400, 401 e 404 corretos); smoke test completo na plataforma
local (`GET /exchange/USD/BRL → 200` com o `id-account` do usuário). Desempenho no
[teste de carga](../devops/load-testing.md) e em [Cache da cotação](../bottlenecks/exchange-cache.md#resultados).

!!! note "API externa no CI e no EKS"

    Com a AwesomeAPI, o runner do GitHub Actions e o NAT do EKS recebiam erro (`429`) e o Exchange
    devolvia `503` (o smoke test trata isso como aviso). É o motivo da troca para a PTAX. Falta
    confirmar a partir da AWS: de um pod do cluster, `curl` uma vez na URL da PTAX (passo do roteiro do
    curso) e `GET /exchange/USD/BRL` pelo ELB.

``` bash
cd api/exchange-service
pip install -r requirements.txt pytest && pytest -q
```
