/**
 * Every query a signed-in request makes runs as that user, in the database.
 *
 * Postgres row-level security (migration 0005) shows the `commitmail_tenant`
 * role only the rows whose user_id matches `app.user_id`. So each request
 * query runs in a transaction that first switches to that role and sets the
 * user — both `LOCAL`, so they end with the transaction and can never leak
 * into the next request that borrows the same pooled connection.
 *
 * The user comes from AsyncLocalStorage, set once the session is known
 * (auth/middleware.ts), so route code queries as before and cannot forget to
 * filter. With no user set the query still runs as the tenant role and
 * simply sees nothing: a lost context fails closed, never open.
 *
 * Code that must see across users — sign-in, the notifier, the live feeds,
 * system health — uses the separate system Db (Deps.systemDb) instead.
 */
import { AsyncLocalStorage } from "node:async_hooks";

import type { Db, Queryable } from "./types";

export const TENANT_ROLE = "commitmail_tenant";

const actor = new AsyncLocalStorage<{ userId: number }>();

/** Run `fn` (and everything it awaits) on behalf of one user. */
export function runAs<T>(userId: number, fn: () => T): T {
  if (!Number.isSafeInteger(userId) || userId <= 0) throw new Error(`not a user id: ${userId}`);
  return actor.run({ userId }, fn);
}

export function currentUserId(): number | null {
  return actor.getStore()?.userId ?? null;
}

async function enter(q: Queryable): Promise<void> {
  await q.query(`SET LOCAL ROLE ${TENANT_ROLE}`);
  await q.query("SELECT set_config('app.user_id', $1, true)", [String(currentUserId() ?? "")]);
}

/** A Db whose every query and transaction acts for the current user. */
export function tenantDb(system: Db): Db {
  const scoped = <T>(fn: (q: Queryable) => Promise<T>) =>
    system.transaction(async (q) => {
      await enter(q);
      return fn(q);
    });
  return {
    query: (sql, params) => scoped((q) => q.query(sql, params)),
    transaction: scoped,
    listen: () => Promise.reject(new Error("LISTEN is for the system connection")),
    close: () => system.close(),
  };
}
