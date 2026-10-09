/**
 * The end-to-end tests' own database — never the one with real mail in it.
 *
 * It lives on the same Postgres server as development (or CI's service
 * container) under the name commitmail_e2e, and every function that empties
 * or rebuilds it first checks that name, so a mistyped setting cannot point
 * it at anything else.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseEnv } from "node:util";

import { hash } from "@node-rs/argon2";
import pg from "pg";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const E2E_DB = "commitmail_e2e";

/** E2E_DATABASE_URL, else the development DATABASE_URL with the database renamed. */
export function databaseUrl(): string {
  const file = join(ROOT, ".env");
  const env = existsSync(file) ? parseEnv(readFileSync(file, "utf8")) : {};
  const given = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL ?? env.DATABASE_URL;
  if (!given) throw new Error("Set E2E_DATABASE_URL to a Postgres server the end-to-end tests may use.");
  const url = new URL(given.replace(/^postgresql(\+psycopg)?:/, "postgres:"));
  url.pathname = `/${E2E_DB}`;
  return url.toString();
}

function assertTestDatabase(url: string): void {
  if (new URL(url).pathname !== `/${E2E_DB}`) throw new Error(`refusing to touch ${new URL(url).pathname}: not ${E2E_DB}`);
}

async function withClient<T>(url: string, fn: (client: pg.Client) => Promise<T>): Promise<T> {
  const client = new pg.Client({ connectionString: url });
  await client.connect();
  try {
    return await fn(client);
  } finally {
    await client.end();
  }
}

/** Create the database if needed, and load the schema exactly as Alembic writes it. */
export async function rebuild(): Promise<void> {
  const url = databaseUrl();
  assertTestDatabase(url);
  const maintenance = new URL(url);
  maintenance.pathname = "/postgres";
  await withClient(maintenance.toString(), async (client) => {
    const exists = await client.query("SELECT 1 FROM pg_database WHERE datname = $1", [E2E_DB]);
    if (!exists.rowCount) await client.query(`CREATE DATABASE ${E2E_DB}`);
  });
  const schema = readFileSync(join(ROOT, "packages", "shared", "contracts", "schema.sql"), "utf8");
  await withClient(url, async (client) => {
    await client.query("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;");
    await client.query(schema);
  });
}

/** Empty every table between tests; the server keeps running. */
export async function empty(): Promise<void> {
  const url = databaseUrl();
  assertTestDatabase(url);
  await withClient(url, async (client) => {
    const { rows } = await client.query<{ name: string }>(
      "SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'",
    );
    await client.query(`TRUNCATE ${rows.map((r) => r.name).join(", ")} RESTART IDENTITY CASCADE`);
  });
}

export async function sql<T extends pg.QueryResultRow = Record<string, unknown>>(text: string, params: unknown[] = []) {
  const url = databaseUrl();
  assertTestDatabase(url);
  return withClient(url, (client) => client.query<T>(text, params)).then((r) => r.rows);
}

export const PASSWORD = "a long enough test password";

/** An account that can sign in with PASSWORD. The first one made is the admin. */
export async function createUser(email: string, displayName: string): Promise<number> {
  const passwordHash = await hash(PASSWORD);
  const [row] = await sql<{ id: number }>(
    `INSERT INTO users (email, display_name, password_hash, is_admin)
     VALUES ($1, $2, $3, NOT EXISTS (SELECT 1 FROM users)) RETURNING id`,
    [email, displayName, passwordHash],
  );
  return row!.id;
}

const day = (offset: number, time = "10:00:00") =>
  `${new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10)} ${time}`;

/** A small, believable mailbox for one user: what each test reads and changes. */
export async function seedMailbox(userId: number, owner: string): Promise<{ emails: Record<string, number> }> {
  const email = async (key: string, subject: string, sender: string, tier: string, category: string, body: string) => {
    const [row] = await sql<{ id: number }>(
      `INSERT INTO raw_emails (user_id, message_id, sender_email, sender_name, subject, body_text,
                               received_at, vip_tier, category, processed)
       VALUES ($1, $2, $3, $4, $5, $6, (now() at time zone 'utc') - interval '2 hours', $7, $8, true) RETURNING id`,
      [userId, `${key}-${userId}@e2e`, `${sender.toLowerCase().split(" ")[0]}@example.com`, sender, `${subject} (${owner})`,
       `${body} (for ${owner})`, tier, category],
    );
    return row!.id;
  };
  const emails = {
    report: await email("report", "Q3 report due Friday", "Priya Nair", "CRITICAL", "action_required",
      "Could you send me the Q3 report by Friday 5pm?"),
    logo: await email("logo", "Final logo files", "Lena Fischer", "MONITOR", "update",
      "I'll send the final logo files on Thursday."),
    sync: await email("sync", "Budget sync", "Ben Carter", "IMPORTANT", "meeting", "Budget sync tomorrow at 10."),
  };
  const commitment = async (emailId: number, subject: string, type: string, deadline: string, tier: string) => {
    const [row] = await sql<{ id: number }>(
      `INSERT INTO commitments (user_id, email_id, type, subject, deadline, evidence_quote, confidence, vip_tier)
       VALUES ($1, $2, $3, $4, $5, 'quoted from the email', 0.9, $6) RETURNING id`,
      [userId, emailId, type, subject, deadline, tier],
    );
    return row!.id;
  };
  const report = await commitment(emails.report, "Send the Q3 report", "deadline_on_you", day(3, "17:00:00"), "CRITICAL");
  await commitment(emails.logo, "Lena sends the logo files", "deadline_from_others", day(2, "12:00:00"), "MONITOR");
  const sync = await commitment(emails.sync, "Budget sync", "meeting", day(1), "IMPORTANT");
  const forwarded = await commitment(emails.sync, "Send the Q3 report again", "deadline_on_you", day(3, "12:00:00"), "CRITICAL");
  await sql(
    `INSERT INTO calendar_flags (user_id, kind, commitment_id, other_commitment_id, details, dedupe_key)
     VALUES ($1, 'duplicate', $2, $3, '{"similarity": 0.75}', 'dup'),
            ($1, 'conflict', $4, NULL, $5, 'clash')`,
    [userId, forwarded, report, sync, JSON.stringify({
      overlap: { start: day(1).replace(" ", "T"), end: day(1, "10:30:00").replace(" ", "T") },
      suggestions: [{ start: day(1, "14:00:00").replace(" ", "T"), end: day(1, "15:00:00").replace(" ", "T") }],
      items: [{}, { title: "Dentist", start: day(1).replace(" ", "T"), end: day(1, "11:00:00").replace(" ", "T"), allDay: false }],
    })],
  );
  await sql("UPDATE calendar_flags SET external_event_id = 'dentist' WHERE kind = 'conflict' AND user_id = $1", [userId]);
  return { emails };
}
