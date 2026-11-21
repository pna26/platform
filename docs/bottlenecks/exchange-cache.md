# Cache da cotação (Exchange)

!!! info "Responsável: Gabriel"

!!! note "Provedor"

    As medições desta página foram feitas com a AwesomeAPI. Desde 20/11 o provedor padrão é a PTAX do
    Banco Central ([Exchange](../servicos/exchange.md)); o cache (TTL de 30 s, lock por par e
    *stale-if-error*) é o mesmo e continua necessário: a PTAX é um serviço público, sem teste de carga nela.

## Problema

O Exchange depende de uma **API de terceiros** (AwesomeAPI). Sem cache, toda requisição
`GET /exchange/USD/BRL` vira uma chamada HTTP para fora da nossa rede:

- a **latência** de um serviço que não controlamos (dezenas a centenas de ms, às vezes segundos) entra
  no caminho de toda requisição;
- a API gratuita tem **limite de requisições**: sob carga ela passa a responder `429`, e aí o Exchange
  cai para **todos** os usuários, não só para quem gerou a carga;
- se a API externa sai do ar, o Exchange sai junto.

É o mesmo padrão do [cache do token](cache.md) no gateway, mas o gargalo aqui está fora da plataforma.

## Solução

Cache **em memória** no próprio Exchange, por par de moedas:

``` mermaid
flowchart LR
    r["N requisições<br/>USD → BRL"] --> ex[exchange]
    ex -->|"cotação com &lt; 30 s?"| hit{hit}
    hit -->|sim| resp([resposta])
    hit -->|não| lock["lock do par<br/>(só 1 busca)"]
    lock -->|"1 chamada"| api([AwesomeAPI])
    api -->|ok| save["guarda no cache"] --> resp
    api -->|"falha / 429"| stale{"cotação de<br/>&lt; 10 min?"}
    stale -->|sim| resp
    stale -->|não| e503([503])
```

| Decisão | Motivo |
|---|---|
| TTL de **30 s** (`CACHE_TTL`) | Cotação de 30 s atrás é aceitável para uma loja; com isso cada réplica faz no máximo 2 chamadas/min por par, independente da carga. |
| Memória do processo, não Redis | São poucos pares (cardinalidade minúscula) e o dado é público: perder o cache num restart custa 1 chamada. Um Redis compartilhado economizaria só `réplicas − 1` chamadas a cada 30 s. |
| **Um lock por par** | Quando o cache expira com 200 usuários pedindo USD-BRL ao mesmo tempo, só **uma** requisição chama a API e as outras esperam e leem o cache (evita *cache stampede*). |
| ***Stale-if-error*** por até 10 min (`CACHE_STALE_TTL`) | Se a API externa falhar ou devolver `429`, servimos a última cotação conhecida em vez de `503`. |
| Par inexistente (`404`) **não** vai para o cache | Não guarda erro do cliente. |
| Timeout de 3 s na chamada externa | Uma API lenta não prende as conexões do Exchange indefinidamente. |
| `CACHE_TTL=0` desliga | Para comparar no [teste de carga](../devops/load-testing.md). |

## Código

``` { .python .copy linenums="1" title="app/cache.py" }
--8<-- "api/exchange-service/app/cache.py"
```

## Como verificar

Os testes do serviço provam o comportamento sem depender da API real (`tests/test_cache.py`):

| Teste | Resultado esperado |
|---|---|
| 50 requisições seguidas a `/exchange/USD/BRL` | **1** chamada à API externa |
| 100 requisições **simultâneas** com o cache vazio | **1** chamada à API externa |
| API fora do ar 2 min depois da última cotação | `200` com a cotação anterior (*stale*) |
| API fora do ar 11 min depois | `503` |

Na plataforma rodando, a métrica `exchange_quote_cache_total{result="hit|miss|stale|error"}` mostra a
taxa de acerto no Grafana ([Observabilidade](observability.md)).

## Resultados

Medidos em 05/11/2026 pelo [teste de carga](../devops/load-testing.md) (experimento 3: 10 usuários,
30 s, `GET /exchange/USD/BRL` e `EUR/BRL` pelo `lb` → gateway → exchange, com a AwesomeAPI real):

| `EXCHANGE_CACHE_TTL` | Requisições | p50 | p95 | p99 | Erros |
|---|---:|---:|---:|---:|---:|
| `0` (sem cache) | 445 | 23 ms | 49 ms | 69 ms | 0 % |
| `30` (com cache) | 454 | **3 ms** | **4 ms** | 17 ms | 0 % |

O p95 cai **92 %**: sem cache, a latência de toda cotação é a da API externa; com cache, só a primeira
cotação de cada par a cada 30 s paga essa latência. O ganho maior não aparece na tabela: sem cache, a
API externa recebeu ≈ 7 chamadas por segundo de só 10 usuários; com cache, recebe no máximo 2 por
minuto por par e por réplica, independente da carga — longe do limite de requisições da API gratuita.

Antes, rodando o serviço sozinho contra a API real: primeira consulta de `USD/BRL` em 60 ms (chamada
externa), a seguinte em **1,5 ms** (cache).
