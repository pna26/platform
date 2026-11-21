# Messaging (Kafka)

!!! info "Owner: Nicolas"

    Follows the course's [Hands-on 7 — Mensageria assíncrona](https://insper.github.io/platform/pt/2027.1/hands-on/7/){target="_blank"}
    (Kafka in compose, *transactional outbox*, idempotent consumer and DLT), adapted from BoxOffice's
    ticket sales to the store's account sign-up.

## Problem { #problema }

After creating an account, the store wants to send a **welcome email**. If `account` called the
email service inside `POST /accounts`, sign-up would be **coupled** to it:

- the user would wait for the email provider's latency (tens to hundreds of ms per send);
- an email outage would become a sign-up outage;
- a spike in sign-ups would hit the email provider at the same moment, at the same pace.

Work the user **does not need to wait for** leaves the request path and goes to a broker.

## Solution { #solucao }

``` mermaid
flowchart LR
    u([User]) -->|"POST /auth/register"| auth --> account
    subgraph tx ["one PostgreSQL transaction"]
        account -->|"INSERT"| acc[("accounts.accounts")]
        account -->|"INSERT AccountCreated"| ob[("accounts.outbox")]
    end
    relay["OutboxRelay<br/>(every 200 ms)"] -->|"reads pending"| ob
    relay -->|"key = account id<br/>acks=all"| k[["Kafka<br/>account-events (6 partitions)"]]
    k -->|"notification group"| n["notification-service"]
    n -->|"dedupe by eventId"| pe[("notification.processed_events")]
    n -->|SMTP| mail(["Mailpit"])
    n -. "permanent failure" .-> dlt[["account-events-dlt"]]
    dlt --> dl[("notification.dead_letters")]
```

The user's request **ends at the database commit**. Publishing, sending the email and retrying
happen afterwards, asynchronously, and can be slow, fail or repeat without the user noticing.

| Decision | Reason |
|---|---|
| **Kafka** (4.3, a single node in KRaft mode) | It is the broker from the course material (Hands-on 7). The log is retained after consumption: messages can be reprocessed and a new consumer can read the history. |
| **Transactional outbox** in `account` | Saving the account and then publishing to Kafka are two writes to different systems: if the process dies between them, the event is lost (or an event is published for an account that does not exist). With the outbox, the account and the event go **into the same transaction**; a relay publishes after the commit. A duplicate email (rollback) leaves no orphan event. |
| Relay with `FOR UPDATE SKIP LOCKED` | Several `account` replicas can run the relay without publishing the same row twice at the same time. |
| Producer with `acks=all` and `enable.idempotence` | The broker only acknowledges after writing; a producer resend does not duplicate the message in the log. |
| Message key = **account id** | Events for the same account land in the same partition: Kafka only guarantees ordering within a partition. |
| **6 partitions**, created by `kafka-init` | The number of partitions is the cap on the group's parallel consumers. Auto-creation is turned off (it would give 1 partition). |
| **Idempotent** consumer | Delivery is *at-least-once*: the same event can arrive twice (the consumer died after sending and before committing the offset, or reprocessing). The `eventId` is saved in `processed_events` in the same transaction as the email record; if it already exists, the event is ignored. |
| 3 retries with *backoff* (0.5 s, 1 s, 2 s) and **DLT** | A temporary failure (SMTP down) is retried; after that the message goes to `account-events-dlt` and the partition keeps moving. Invalid JSON or an event missing required fields goes **straight** to the DLT: retrying would not help. |
| The event carries no password or hash | Only `eventId`, `eventType`, `accountId`, `name`, `email` and `occurredAt`: the minimum for the email. |
| Its own schema (`notification`) | The consumer keeps its state in the same PostgreSQL, without touching `account`'s tables. |
| Kafka with no published port; Kafka UI and Mailpit only on `127.0.0.1` | The broker has no authentication: it stays in the trusted layer (compose network / ClusterIP on k8s), like Redis. |

## Code { #codigo }

`AccountService` saves the account and the event in the same transaction:

``` { .java .copy title="AccountService.java (excerpt)" }
--8<-- "api/account-service/src/main/java/store/account/AccountService.java:26:60"
```

``` { .java .copy linenums="1" title="OutboxRelay.java" }
--8<-- "api/account-service/src/main/java/store/account/outbox/OutboxRelay.java"
```

`notification-service` deduplicates by `eventId` before sending:

``` { .java .copy linenums="1" title="NotificationService.java" }
--8<-- "api/notification-service/src/main/java/store/notification/NotificationService.java"
```

``` { .java .copy linenums="1" title="KafkaConfig.java (retries and DLT)" }
--8<-- "api/notification-service/src/main/java/store/notification/KafkaConfig.java"
```

| Where | File |
|---|---|
| Outbox table | `api/account-service/src/main/resources/db/migration/V2026.11.08.001__create_outbox.sql` |
| Consumer tables | `api/notification-service/src/main/resources/db/migration/V2026.11.08.001__create_notification.sql` |
| Compose | `compose.yaml` (`kafka`, `kafka-init`, `kafka-ui`, `mailpit`, `notification`) |
| Kubernetes | `k8s/kafka.yaml`, `k8s/mailpit.yaml`, `api/notification-service/k8s/k8s.yaml` |
| Test | `scripts/messaging-test.sh` |

## How to verify { #como-verificar }

``` { .bash .copy }
./scripts/build.sh
docker compose up -d --build
./scripts/messaging-test.sh
```

- Inbox: <http://localhost:8025> (Mailpit).
- Topics, messages and lag of the `notification` group: <http://localhost:8089> (Kafka UI).
- From the command line:

``` { .bash .copy }
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --group notification
```

## Results { #resultados }

`scripts/messaging-test.sh` on local compose (08/11/2026), 20 sign-ups per experiment, simulated email
latency of 50 ms, 3 consumer threads:

| Experiment | What happened |
|---|---|
| 1. Happy path | 20 sign-ups, **average of 16 ms** per `POST /auth/register`; 20 welcome emails in Mailpit. |
| 2. `notification` stopped | Sign-up kept responding (**15 ms**). The group lag rose to **20 messages**, held in Kafka. When it came back, the backlog drained in **9 s** (including JVM startup) and the 20 emails arrived. |
| 3. Kafka stopped | Sign-up kept responding (**15 ms**): the **20 events stayed in the outbox**. With Kafka back, the relay published everything and the 20 emails arrived, **no event lost**. |
| 4. Reprocessing (group offsets reset to the beginning) | The whole topic was reread and **no new email** was sent: every event was already in `processed_events`. |
| 5. Poison message (text that is not JSON) | Went to `account-events-dlt` **without retry** (`StreamReadException`) and stayed in `dead_letters`. The next 20 sign-ups produced the 20 emails: the partition did not get stuck. |

Also checked: a second sign-up with the same email (500, the course's behavior) does **not** leave a row
in the outbox, because the rollback undoes the account and the event together.

!!! note "With the email in the synchronous path"

    With the send inside the request, each sign-up would add the provider's latency (50 ms simulated here, and
    hundreds of ms with real providers) and would fail along with it. In experiments 2 and 3 sign-up took
    the same time with the consumer or the broker down: that is the decoupling the bottleneck calls for.

## On EKS { #no-eks }

Ran on `eks-store` on **16/11/2026**, deployed by CI ([evidence](../devops/ci-cd.md#deploy-no-eks-1611)):

- `k8s/kafka.yaml`: 1-replica Deployment (KRaft), `kafka` Service (ClusterIP) and the `kafka-init` Job
  that creates the topics. The `infra.yaml` workflow applies the `k8s/` folder along with the other manifests.
- `k8s/mailpit.yaml`: fake SMTP; the inbox is reachable only via `kubectl port-forward -n store svc/mailpit 8025:8025`.
- `api/notification-service/k8s/k8s.yaml`: the consumer (no Service: nobody calls it), published by the
  `notification-service` pipeline ([run](https://github.com/pna26/notification-service/actions/runs/43856129429){target="_blank"}).
  `account`'s ConfigMap gained `KAFKA_BOOTSTRAP_SERVERS`.
- `notification-secrets` Secret (RDS user and password) created by hand, once per cluster, like
  `account-secrets`. It **never** goes to Git.

**Result (16/11):** a sign-up through Auth's `kubectl port-forward` (the gateway had not been
published yet that day) returned `200`; Flyway created the schema in RDS, the *relay* published the
`AccountCreated` from the outbox to the `account-events` topic and `notification-service` logged the
welcome email send (see [EKS](../devops/eks.md#testar)). The containers run with the same restrictions
tested locally (non-root user, `capabilities` dropped, read-only root on Mailpit and on
notification). The failure experiments (2 to 5 above) were done on compose.

!!! warning "Known limitations"

    - **Kafka without a persistent disk on EKS:** the cluster does not have the EBS CSI driver, so the data lives in an
      `emptyDir`. If the Kafka pod restarts, messages not yet consumed are lost. In production:
      a PersistentVolume, 3 brokers with `replication-factor 3`, or Amazon MSK (managed Kafka).
    - **A single broker** (`replication-factor 1`): no tolerance to broker failure. Events wait in the
      outbox while it is down (experiment 3), but the broker is a single point of failure for messaging.
    - **No authentication on Kafka** (PLAINTEXT): acceptable only because it is not reachable from outside the
      internal network.
    - Consumer lag is not in Prometheus/Grafana yet; today it is seen through Kafka UI or the CLI.
