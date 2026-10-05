/**
 * Rate limits, kept in Redis when there is one so they survive a restart and
 * apply across server processes; in memory otherwise.
 */
import rateLimit, { type Store } from "express-rate-limit";
import type { RedisReply } from "rate-limit-redis";
import { RedisStore } from "rate-limit-redis";
import type { Redis } from "ioredis";

export interface Limits {
  login: ReturnType<typeof rateLimit>;
  setup: ReturnType<typeof rateLimit>;
  sensitive: ReturnType<typeof rateLimit>;
  api: ReturnType<typeof rateLimit>;
}

export function buildLimits(redis: Redis | null, prefix: string): Limits {
  const store = (name: string): Store | undefined =>
    redis
      ? new RedisStore({
          prefix: `${prefix}:ratelimit:${name}:`,
          sendCommand: (command: string, ...args: string[]) =>
            redis.call(command, ...args) as Promise<RedisReply>,
        })
      : undefined;

  const limiter = (name: string, windowMs: number, limit: number, message: string, skipSuccessful = false) =>
    rateLimit({
      windowMs,
      limit,
      standardHeaders: "draft-7",
      legacyHeaders: false,
      skipSuccessfulRequests: skipSuccessful,
      store: store(name),
      handler: (_req, res) => {
        res.status(429).json({ error: { code: "rate_limited", message } });
      },
    });

  return {
    // Only failures count, so a user who logs in fine is never slowed down, and
    // five wrong guesses buy fifteen minutes of nothing.
    login: limiter("login", 15 * 60_000, 5, "Too many sign-in attempts. Try again in 15 minutes.", true),
    setup: limiter("setup", 60 * 60_000, 10, "Too many attempts. Try again later."),
    sensitive: limiter("sensitive", 60 * 60_000, 20, "Too many requests for this action. Try again later."),
    api: limiter("api", 60_000, 600, "Slow down — too many requests."),
  };
}
