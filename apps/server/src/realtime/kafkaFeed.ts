/**
 * New events, delivered from Kafka — the same job as PostgresEventFeed, fed by
 * the worker's outbox relay instead of by polling the table.
 *
 * It replaces the Postgres feed rather than running beside it, so each event
 * reaches a browser once. Two things make that safe:
 *
 * - **Live only.** Like the Postgres feed it starts at the newest event id and
 *   drops anything at or below it: history is the REST API's job. Because of
 *   that floor it can read the topic from the beginning on a first start, so
 *   no event written while the consumer group was forming is skipped.
 * - **Once.** Kafka delivers at least once (a restart, a rebalance), so ids
 *   already passed on are remembered and dropped. The notifier needs none of
 *   this: it reads the table and inserts idempotently.
 *
 * The message is an events-table row (see src/events/relay.py), so it is
 * mapped by the same function the Postgres feed uses.
 */
import { Kafka, logLevel, type Consumer, type LogEntry } from "kafkajs";
import type { Logger } from "pino";

import type { Db } from "../db/types";
import { toLiveEvent, type EventSource, type LiveEvent } from "./feed";

export interface KafkaFeedOptions {
  brokers: string[];
  topic: string;
  groupId: string;
}

/** A Kafka message value as an event, or null if it is not one. */
export function parseEventMessage(value: Buffer | string | null | undefined): LiveEvent | null {
  if (!value) return null;
  let row: unknown;
  try {
    row = JSON.parse(value.toString());
  } catch {
    return null;
  }
  if (!row || typeof row !== "object") return null;
  const r = row as Record<string, unknown>;
  const valid =
    typeof r.id === "number" &&
    typeof r.type === "string" &&
    typeof r.message === "string" &&
    typeof r.severity === "string" &&
    typeof r.created_at === "string";
  return valid ? toLiveEvent(r) : null;
}

/** Ids already passed on, forgetting the oldest beyond `limit`. */
export class RecentIds {
  private readonly seen = new Set<number>();
  constructor(private readonly limit = 10_000) {}

  /** True the first time an id is offered; false for a repeat. */
  add(id: number): boolean {
    if (this.seen.has(id)) return false;
    this.seen.add(id);
    if (this.seen.size > this.limit) this.seen.delete(this.seen.values().next().value!);
    return true;
  }
}

function kafkaLogs(log: Logger) {
  return () =>
    ({ namespace, level, log: entry }: LogEntry) => {
      const { message, ...extra } = entry;
      const method = level <= logLevel.ERROR ? "error" : level === logLevel.WARN ? "warn" : "debug";
      log[method]({ kafka: namespace, ...extra }, message);
    };
}

export class KafkaEventFeed implements EventSource {
  private readonly consumer: Consumer;
  private readonly recent = new RecentIds();
  private floor = 0;
  private stopped = false;

  constructor(
    private readonly options: KafkaFeedOptions,
    private readonly db: Db,
    private readonly log: Logger,
    private readonly deliver: (events: LiveEvent[]) => void | Promise<void>,
  ) {
    const kafka = new Kafka({
      clientId: "commitmail-server",
      brokers: options.brokers,
      logLevel: logLevel.WARN,
      logCreator: kafkaLogs(log),
      // Fail fast at startup, so the server can fall back to Postgres instead
      // of waiting out a long retry schedule. Once running, kafkajs restarts
      // the consumer itself after a broker hiccup.
      retry: { retries: 3, initialRetryTime: 300 },
    });
    // A server killed without leaving the group holds its partitions until
    // this lapses; the default 30 s outlasts the startup wait in index.ts.
    this.consumer = kafka.consumer({ groupId: options.groupId, sessionTimeout: 10_000, heartbeatInterval: 2_000 });
  }

  async start(): Promise<void> {
    const { rows } = await this.db.query<{ max: number | string | null }>("SELECT MAX(id) AS max FROM events");
    this.floor = Number(rows[0]?.max ?? 0);
    await this.consumer.connect();
    await this.consumer.subscribe({ topic: this.options.topic, fromBeginning: true });
    // Gave up on while connecting (the server fell back to Postgres): never
    // start consuming as well, or every live event would arrive twice.
    if (this.stopped) {
      await this.consumer.disconnect();
      return;
    }
    await this.consumer.run({
      eachBatch: async ({ batch }) => {
        const events = this.accept(batch.messages.map((message) => message.value));
        if (events.length) await this.deliver(events);
      },
    });
  }

  async stop(): Promise<void> {
    this.stopped = true;
    await this.consumer.disconnect();
  }

  /** Message values -> the events to pass on: valid, live, and not seen yet. */
  accept(values: Array<Buffer | string | null | undefined>): LiveEvent[] {
    const events: LiveEvent[] = [];
    for (const value of values) {
      const live = parseEventMessage(value);
      if (!live) {
        this.log.warn("skipped a Kafka message that is not an event");
        continue;
      }
      if (live.event.id > this.floor && this.recent.add(live.event.id)) events.push(live);
    }
    return events;
  }

  /** For tests: pretend `start` found this newest id. */
  setFloor(id: number): void {
    this.floor = id;
  }
}
