/**
 * Password hashing with argon2id.
 *
 * argon2id rather than bcrypt: it is memory-hard, so guessing at scale needs
 * memory as well as compute, which is what makes GPU cracking expensive. The
 * parameters are OWASP's baseline recommendation (19 MiB, 2 passes, 1 lane).
 */
import { hash, verify } from "@node-rs/argon2";

const PARAMS = { memoryCost: 19_456, timeCost: 2, parallelism: 1 } as const;

export function hashPassword(password: string): Promise<string> {
  return hash(password, PARAMS);
}

export async function verifyPassword(stored: string, password: string): Promise<boolean> {
  try {
    return await verify(stored, password);
  } catch {
    return false;   // a malformed stored hash is a failed login, not a crash
  }
}

/**
 * A hash nobody knows the password to, verified against when there is no
 * account to check. Without it, "no such account" answers instantly while a
 * wrong password takes ~50ms — and that difference tells an attacker whether
 * the email they tried is the owner's.
 */
let decoy: Promise<string> | null = null;
export function decoyHash(): Promise<string> {
  decoy ??= hashPassword(`decoy-${Math.random()}-${Date.now()}`);
  return decoy;
}
