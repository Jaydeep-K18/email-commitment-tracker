/**
 * The database interface every module uses.
 *
 * Deliberately tiny — a query, a transaction, a LISTEN — so it has two honest
 * implementations: node-postgres against the real server, and PGlite (Postgres
 * compiled to WebAssembly) in the tests. Both are real Postgres, so the tests
 * exercise the actual SQL: full-text search, JSONB, triggers and all.
 *
 * Type handling is pinned in both adapters, identically:
 *   int8 (COUNT, BIGSERIAL ids)  -> number   (ids here never approach 2^53)
 *   numeric (AVG, ROUND)         -> number
 *   timestamp                    -> the raw text, converted by ./time.ts
 * Timestamps are left as text because this schema stores naive values with a
 * known meaning (UTC, or wall clock for deadlines) that a driver's automatic
 * Date conversion would get wrong by applying the server's local zone.
 */
export interface QueryResult<T> {
  rows: T[];
  rowCount: number;
}

export interface Queryable {
  // Rows are loosely typed at the boundary; the mappers in modules/ give them shape.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  query<T = Record<string, any>>(sql: string, params?: unknown[]): Promise<QueryResult<T>>;
}

export interface Db extends Queryable {
  /** Runs `fn` in a transaction: commit if it resolves, roll back if it throws. */
  transaction<T>(fn: (tx: Queryable) => Promise<T>): Promise<T>;
  /** Subscribe to a NOTIFY channel. Resolves to an unsubscribe function. */
  listen(channel: string, onMessage: (payload: string) => void): Promise<() => Promise<void>>;
  close(): Promise<void>;
}

/** Postgres type OIDs the adapters configure. */
export const PG_TYPES = { INT8: 20, NUMERIC: 1700, TIMESTAMP: 1114 } as const;
