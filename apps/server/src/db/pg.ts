import pg from "pg";
import type { Logger } from "pino";

import { PG_TYPES, type Db, type Queryable } from "./types";

const types = new pg.TypeOverrides();
types.setTypeParser(PG_TYPES.INT8, (value) => Number(value));
types.setTypeParser(PG_TYPES.NUMERIC, (value) => Number(value));
types.setTypeParser(PG_TYPES.TIMESTAMP, (value) => value);

function wrap(client: pg.Pool | pg.PoolClient): Queryable {
  return {
    async query<T>(sql: string, params: unknown[] = []) {
      const result = await client.query(sql, params as unknown[]);
      return { rows: result.rows as T[], rowCount: result.rowCount ?? 0 };
    },
  };
}

export function createPgDb(connectionString: string, log: Logger): Db {
  const pool = new pg.Pool({
    connectionString,
    types,
    max: 10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
  });
  // An idle client losing its connection (a Postgres restart) must not crash
  // the process; the pool replaces it on next use.
  pool.on("error", (error) => log.warn({ err: error }, "idle database connection lost"));

  const root = wrap(pool);

  return {
    query: root.query,

    async transaction(fn) {
      const client = await pool.connect();
      try {
        await client.query("BEGIN");
        const result = await fn(wrap(client));
        await client.query("COMMIT");
        return result;
      } catch (error) {
        await client.query("ROLLBACK").catch(() => undefined);
        throw error;
      } finally {
        client.release();
      }
    },

    async listen(channel, onMessage) {
      if (!/^[a-z_][a-z0-9_]*$/.test(channel)) throw new Error(`bad channel name: ${channel}`);
      // LISTEN needs one connection held for as long as we want notifications.
      const client = await pool.connect();
      const handler = (message: pg.Notification) => {
        if (message.channel === channel) onMessage(message.payload ?? "");
      };
      client.on("notification", handler);
      await client.query(`LISTEN ${channel}`);
      return async () => {
        client.off("notification", handler);
        await client.query(`UNLISTEN ${channel}`).catch(() => undefined);
        client.release();
      };
    },

    async close() {
      await pool.end();
    },
  };
}
