# Mensageria (Kafka)

!!! info "Responsável: Nicolas"

    Segue o [Hands-on 7 — Mensageria assíncrona](https://insper.github.io/platform/pt/2027.1/hands-on/7/){target="_blank"}
    do curso (Kafka no compose, *transactional outbox*, consumidor idempotente e DLT), adaptado da venda de
    ingressos do BoxOffice para o cadastro de contas da loja.

## Problema

Depois de criar uma conta, a loja quer mandar um **e-mail de boas-vindas**. Se o `account` chamasse o
serviço de e-mail dentro do `POST /accounts`, o cadastro ficaria **acoplado** a ele:

- o usuário esperaria a latência do provedor de e-mail (dezenas a centenas de ms por envio);
- uma queda do e-mail viraria uma queda do cadastro;
- um pico de cadastros bateria no provedor de e-mail na mesma hora, no mesmo ritmo.

O trabalho que o usuário **não precisa esperar** sai do caminho da requisição e vai para um broker.

## Solução

``` mermaid
flowchart LR
    u([Usuário]) -->|"POST /auth/register"| auth --> account
    subgraph tx ["uma transação no PostgreSQL"]
        account -->|"INSERT"| acc[("accounts.accounts")]
        account -->|"INSERT AccountCreated"| ob[("accounts.outbox")]
    end
    relay["OutboxRelay<br/>(a cada 200 ms)"] -->|"lê pendentes"| ob
    relay -->|"chave = id da conta<br/>acks=all"| k[["Kafka<br/>account-events (6 partições)"]]
    k -->|"grupo notification"| n["notification-service"]
    n -->|"dedupe por eventId"| pe[("notification.processed_events")]
    n -->|SMTP| mail(["Mailpit"])
    n -. "falha permanente" .-> dlt[["account-events-dlt"]]
    dlt --> dl[("notification.dead_letters")]
```

A requisição do usuário **termina no commit do banco**. Publicar, enviar o e-mail e tentar de novo
acontece depois, de forma assíncrona, e pode ser lento, falhar ou se repetir sem que o usuário perceba.

| Decisão | Motivo |
|---|---|
| **Kafka** (4.3, um nó em modo KRaft) | É o broker do material do curso (Hands-on 7). O log é retido depois do consumo: dá para reprocessar e um consumidor novo pode ler o histórico. |
| **Transactional outbox** no `account` | Gravar a conta e depois publicar no Kafka são duas escritas em sistemas diferentes: se o processo cair entre elas, perde-se o evento (ou publica-se um evento de uma conta que não existe). Com o outbox, a conta e o evento entram **na mesma transação**; um relay publica depois do commit. E-mail duplicado (rollback) não deixa evento órfão. |
| Relay com `FOR UPDATE SKIP LOCKED` | Várias réplicas do `account` podem rodar o relay sem publicar a mesma linha duas vezes ao mesmo tempo. |
| Produtor com `acks=all` e `enable.idempotence` | O broker só confirma depois de gravar; um reenvio do produtor não duplica a mensagem no log. |
| Chave da mensagem = **id da conta** | Eventos da mesma conta caem na mesma partição: o Kafka só garante ordem dentro da partição. |
| **6 partições**, criadas pelo `kafka-init` | O número de partições é o teto de consumidores paralelos do grupo. A criação automática está desligada (daria 1 partição). |
| Consumidor **idempotente** | A entrega é *at-least-once*: o mesmo evento pode chegar duas vezes (consumidor caiu depois de enviar e antes do commit do offset, ou reprocessamento). O `eventId` é gravado em `processed_events` na mesma transação do registro do e-mail; se já existe, o evento é ignorado. |
| 3 retries com *backoff* (0,5 s, 1 s, 2 s) e **DLT** | Falha temporária (SMTP fora) é tentada de novo; depois disso a mensagem vai para `account-events-dlt` e a partição continua andando. JSON inválido ou evento sem campos obrigatórios vão **direto** para a DLT: tentar de novo não adianta. |
| O evento não leva senha nem hash | Só `eventId`, `eventType`, `accountId`, `name`, `email` e `occurredAt`: o mínimo para o e-mail. |
| Schema próprio (`notification`) | O consumidor guarda o seu estado no mesmo PostgreSQL, sem tocar nas tabelas do `account`. |
| Kafka sem porta publicada; Kafka UI e Mailpit só em `127.0.0.1` | O broker não tem autenticação: fica na camada confiável (rede do compose / ClusterIP no k8s), como o Redis. |

## Código

O `AccountService` grava a conta e o evento na mesma transação:

``` { .java .copy title="AccountService.java (trecho)" }
--8<-- "api/account-service/src/main/java/store/account/AccountService.java:26:60"
```

``` { .java .copy linenums="1" title="OutboxRelay.java" }
--8<-- "api/account-service/src/main/java/store/account/outbox/OutboxRelay.java"
```

O `notification-service` deduplica pelo `eventId` antes de enviar:

``` { .java .copy linenums="1" title="NotificationService.java" }
--8<-- "api/notification-service/src/main/java/store/notification/NotificationService.java"
```

``` { .java .copy linenums="1" title="KafkaConfig.java (retries e DLT)" }
--8<-- "api/notification-service/src/main/java/store/notification/KafkaConfig.java"
```

| Onde | Arquivo |
|---|---|
| Tabela do outbox | `api/account-service/src/main/resources/db/migration/V2026.11.08.001__create_outbox.sql` |
| Tabelas do consumidor | `api/notification-service/src/main/resources/db/migration/V2026.11.08.001__create_notification.sql` |
| Compose | `compose.yaml` (`kafka`, `kafka-init`, `kafka-ui`, `mailpit`, `notification`) |
| Kubernetes | `k8s/kafka.yaml`, `k8s/mailpit.yaml`, `api/notification-service/k8s/k8s.yaml` |
| Teste | `scripts/messaging-test.sh` |

## Como verificar

``` { .bash .copy }
./scripts/build.sh
docker compose up -d --build
./scripts/messaging-test.sh
```

- Caixa de entrada: <http://localhost:8025> (Mailpit).
- Tópicos, mensagens e lag do grupo `notification`: <http://localhost:8089> (Kafka UI).
- Pela linha de comando:

``` { .bash .copy }
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --group notification
```

## Resultados

`scripts/messaging-test.sh` no compose local (08/11/2026), 20 cadastros por experimento, latência simulada
do e-mail de 50 ms, 3 threads consumidoras:

| Experimento | O que aconteceu |
|---|---|
| 1. Caminho feliz | 20 cadastros, **média de 16 ms** por `POST /auth/register`; 20 e-mails de boas-vindas no Mailpit. |
| 2. `notification` parado | O cadastro continuou respondendo (**15 ms**). O lag do grupo subiu para **20 mensagens**, guardadas no Kafka. Ao religar, o backlog drenou em **9 s** (incluindo subir a JVM) e chegaram os 20 e-mails. |
| 3. Kafka parado | O cadastro continuou respondendo (**15 ms**): os **20 eventos ficaram no outbox**. Com o Kafka de volta, o relay publicou tudo e chegaram os 20 e-mails, **nenhum evento perdido**. |
| 4. Reprocessamento (offsets do grupo voltados ao início) | O tópico inteiro foi relido e **nenhum e-mail novo** foi enviado: todos os eventos já estavam em `processed_events`. |
| 5. Mensagem envenenada (texto que não é JSON) | Foi para `account-events-dlt` **sem retry** (`StreamReadException`) e ficou em `dead_letters`. Os 20 cadastros seguintes geraram os 20 e-mails: a partição não travou. |

Também conferido: um segundo cadastro com o mesmo e-mail (500, comportamento do curso) **não** deixa linha
no outbox, porque o rollback desfaz a conta e o evento juntos.

!!! note "Com o e-mail no caminho síncrono"

    Com o envio dentro da requisição, cada cadastro somaria a latência do provedor (50 ms simulados aqui, e
    centenas de ms em provedores reais) e falharia junto com ele. Nos experimentos 2 e 3 o cadastro ficou
    no mesmo tempo com o consumidor ou o broker fora do ar: é o desacoplamento que o gargalo pede.

## No EKS

Rodou no `eks-store` em **16/11/2026**, com o deploy feito pelo CI ([evidência](../devops/ci-cd.md#deploy-no-eks-1611)):

- `k8s/kafka.yaml`: Deployment de 1 réplica (KRaft), Service `kafka` (ClusterIP) e o Job `kafka-init`
  que cria os tópicos. O workflow `infra.yaml` aplica a pasta `k8s/` junto com os outros manifestos.
- `k8s/mailpit.yaml`: SMTP falso; a caixa de entrada só por `kubectl port-forward -n store svc/mailpit 8025:8025`.
- `api/notification-service/k8s/k8s.yaml`: o consumidor (sem Service: ninguém o chama), publicado pelo
  pipeline do `notification-service` ([run](https://github.com/pna26/notification-service/actions/runs/43856129429){target="_blank"}).
  O ConfigMap do `account` ganhou `KAFKA_BOOTSTRAP_SERVERS`.
- Secret `notification-secrets` (usuário e senha do RDS) criado à mão, uma vez por cluster, como o
  `account-secrets`. **Nunca** vai para o Git.

**Resultado (16/11):** um cadastro pelo `kubectl port-forward` do Auth (o gateway ainda não tinha sido
publicado nesse dia) respondeu `200`; o Flyway criou o schema no RDS, o *relay* publicou o
`AccountCreated` do outbox no tópico `account-events` e o `notification-service` registrou o envio do
e-mail de boas-vindas (ver [EKS](../devops/eks.md#testar)). Os containers rodam com as mesmas restrições
testadas localmente (usuário sem root, `capabilities` removidas, raiz somente leitura no Mailpit e no
notification). Os experimentos de falha (2 a 5 acima) foram feitos no compose.

!!! warning "Limites conhecidos"

    - **Kafka sem disco persistente no EKS:** o cluster não tem o driver EBS CSI, então os dados ficam num
      `emptyDir`. Se o pod do Kafka reiniciar, as mensagens ainda não consumidas se perdem. Em produção:
      PersistentVolume, 3 brokers com `replication-factor 3` ou o Amazon MSK (Kafka gerenciado).
    - **Um broker só** (`replication-factor 1`): sem tolerância a falha do broker. Os eventos esperam no
      outbox enquanto ele está fora (experimento 3), mas o broker é ponto único de falha da mensageria.
    - **Sem autenticação no Kafka** (PLAINTEXT): aceitável só porque ele não é alcançável de fora da
      rede interna.
    - O lag do consumidor ainda não está no Prometheus/Grafana; hoje é visto pelo Kafka UI ou pela CLI.
