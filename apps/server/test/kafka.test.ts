/**
 * Live events from Kafka (v2 phase 6), without a broker: what the feed passes
 * on, the message format Python writes (read from the shared contract file),
 * and how health reports the broker and the relay.
 */
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:net";
import type { AddressInfo } from "node:net";
import { join } from "node:path";

import { pino } from "pino";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { Db } from "../src/db/types";
import { findRepoRoot } from "../src/env";
import { KafkaEventFeed, parseEventMessage, RecentIds } from "../src/realtime/kafkaFeed";
import { buildApp, createTestDb, resetDb, signedIn } from "./helpers";

const contract = readFileSync(join(findRepoRoot(), "packages", "shared", "contracts", "event-message.json"), "utf8");

function message(id: number, extra: Record<string, unknown> = {}) {
  return JSON.stringify({ ...JSON.parse(contract), id, ...extra });
}

describe("reading an event off Kafka", () => {
  it("understands the message the Python relay writes", () => {
    expect(parseEventMessage(Buffer.from(contract))?.userId).toBe(7);
    expect(parseEventMessage(Buffer.from(contract))?.event).toEqual({
      id: 4101,
      type: "email.received",
      entityType: "email",
      entityId: "42",
      correlationId: "email:42",
      severity: "info",
      message: "Email from Priya Nair: Q3 report",
      payload: { subject: "Q3 report", vip_tier: "CRITICAL" },
      source: "worker",
      createdAt: "2026-10-08T09:30:00.123456Z",
    });
  });

  it("refuses anything that is not an event rather than crashing the consumer", () => {
    expect(parseEventMessage(null)).toBeNull();
    expect(parseEventMessage("not json")).toBeNull();
    expect(parseEventMessage("[1,2]")).toBeNull();
    expect(parseEventMessage(JSON.stringify({ id: "7", type: "x" }))).toBeNull();
    expect(parseEventMessage(message(1, { created_at: null }))).toBeNull();
  });
});

describe("the Kafka feed", () => {
  const feed = () => {
    const f = new KafkaEventFeed({ brokers: ["127.0.0.1:9"], topic: "t", groupId: "g" }, {} as Db, pino({ level: "silent" }), () => {});
    f.setFloor(100);
    return f;
  };

  it("passes on only events newer than when the server started", () => {
    const ids = feed().accept([message(99), message(100), message(101), message(102)]).map((e) => e.event.id);
    expect(ids).toEqual([101, 102]);
  });

  it("passes each event on once, though Kafka may deliver it twice", () => {
    const f = feed();
    expect(f.accept([message(101), message(102)]).map((e) => e.event.id)).toEqual([101, 102]);
    expect(f.accept([message(102), message(103), message(101)]).map((e) => e.event.id)).toEqual([103]);
  });

  it("skips a malformed message and keeps the rest of the batch", () => {
    expect(feed().accept(["garbage", message(105)]).map((e) => e.event.id)).toEqual([105]);
  });

  it("forgets the oldest ids once it has remembered enough", () => {
    const recent = new RecentIds(2);
    expect([recent.add(1), recent.add(2), recent.add(3)]).toEqual([true, true, true]);
    expect(recent.add(1)).toBe(true);    // evicted, so new again
    expect(recent.add(3)).toBe(false);
  });
});

describe("Kafka in system health", () => {
  let db: Db;
  let broker: Server;
  let brokerAddress = "";

  beforeAll(async () => {
    ({ db } = await createTestDb());
    broker = createServer((socket) => socket.end());   // accepts connections, like a broker's port
    await new Promise<void>((resolve) => broker.listen(0, "127.0.0.1", resolve));
    brokerAddress = `127.0.0.1:${(broker.address() as AddressInfo).port}`;
  });
  afterAll(() => new Promise<void>((resolve) => broker.close(() => resolve())));
  beforeEach(() => resetDb(db));

  async function kafkaHealth(brokers: string, liveEvents: "kafka" | "postgres", events: Array<[number, boolean]> = []) {
    const t = buildApp(db, { KAFKA_BROKERS: brokers });
    t.deps.liveEvents = liveEvents;
    const session = await signedIn(t.app);
    // Signing in records events of its own; count those as relayed.
    await db.query("UPDATE events SET published_at = now() at time zone 'utc' WHERE published_at IS NULL");
    for (const [minutesAgo, published] of events) await addEvent(minutesAgo, published);
    return (await session.get("/api/system/health")).body.components.kafka;
  }

  async function addEvent(minutesAgo: number, published: boolean) {
    await db.query(
      `INSERT INTO events (type, severity, message, payload, source, created_at, published_at)
       VALUES ('email.received', 'info', 'x', '{}', 'worker',
               (now() at time zone 'utc') - make_interval(mins => $1::int),
               CASE WHEN $2::boolean THEN now() at time zone 'utc' END)`,
      [minutesAgo, published],
    );
  }

  it("is healthy when the broker answers and the relay keeps up", async () => {
    const kafka = await kafkaHealth(brokerAddress, "kafka", [[10, true]]);
    expect(kafka.state).toBe("ok");
    expect(kafka.meta).toMatchObject({ waiting: 0, liveEvents: "kafka" });
  });

  it("says so when events are piling up because nothing is relaying them", async () => {
    const kafka = await kafkaHealth(brokerAddress, "kafka", [[5, false], [1, false]]);
    expect(kafka.state).toBe("degraded");
    expect(kafka.detail).toMatch(/2 event\(s\) not relayed, oldest 5 min/);
  });

  it("does not count an event written a moment ago as a backlog", async () => {
    const kafka = await kafkaHealth(brokerAddress, "kafka", [[0, false]]);
    expect(kafka.state).toBe("ok");
    expect(kafka.meta.waiting).toBe(1);
  });

  it("flags a server that fell back to Postgres while Kafka is up", async () => {
    const kafka = await kafkaHealth(brokerAddress, "postgres");
    expect(kafka.state).toBe("degraded");
    expect(kafka.detail).toMatch(/fell back to Postgres/);
  });

  it("is down when the broker does not answer", async () => {
    expect((await kafkaHealth("127.0.0.1:9", "kafka")).state).toBe("down");
  });
});
