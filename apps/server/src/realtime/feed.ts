/**
 * New rows in the events table, delivered as they happen.
 *
 * Postgres sends a NOTIFY for every insert (a trigger in migration 0002), and
 * that wakes a read of everything past the last id seen. The notification
 * carries no data of its own and is used only as a wake-up, for two reasons:
 * NOTIFYs are not stored, so one missed while reconnecting is simply gone, and
 * reading by id from a cursor means the order and completeness come from the
 * table, not from the delivery. A slow timer re-runs the same read as a safety
 * net, so a lost wake-up costs a couple of seconds, never an event.
 *
 * When Kafka is running, a Kafka consumer replaces this with the same
 * interface (see ./kafkaFeed.ts).
 */
import type { ActivityEvent } from "@commitmail/shared";
import type { Logger } from "pino";

import type { Db } from "../db/types";
import { toActivityEvent } from "../modules/mappers";

export interface EventSource {
  start(): Promise<void>;
  stop(): Promise<void>;
}

const BATCH = 500;

export class PostgresEventFeed implements EventSource {
  private cursor = 0;
  private running = false;
  private rerun = false;
  private timer: NodeJS.Timeout | null = null;
  private unlisten: (() => Promise<void>) | null = null;

  constructor(
    private readonly db: Db,
    private readonly log: Logger,
    private readonly deliver: (events: ActivityEvent[]) => void | Promise<void>,
    private readonly pollMs = 2_000,
  ) {}

  async start(): Promise<void> {
    // Live events only: history is the REST API's job, so start at the end.
    const { rows } = await this.db.query<{ max: number | null }>("SELECT MAX(id) AS max FROM events");
    this.cursor = rows[0]?.max ?? 0;
    this.unlisten = await this.db.listen("events", () => void this.pull());
    this.timer = setInterval(() => void this.pull(), this.pollMs);
    this.timer.unref();
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    await this.unlisten?.();
  }

  /** Read and deliver everything past the cursor. Never runs twice at once. */
  async pull(): Promise<void> {
    if (this.running) {
      this.rerun = true;
      return;
    }
    this.running = true;
    try {
      do {
        this.rerun = false;
        const { rows } = await this.db.query(
          "SELECT * FROM events WHERE id > $1 ORDER BY id LIMIT $2",
          [this.cursor, BATCH],
        );
        if (rows.length === 0) break;
        this.cursor = (rows[rows.length - 1] as { id: number }).id;
        await this.deliver(rows.map(toActivityEvent));
        if (rows.length === BATCH) this.rerun = true;
      } while (this.rerun);
    } catch (error) {
      this.log.warn({ err: error }, "event feed read failed; will retry");
    } finally {
      this.running = false;
    }
  }
}
