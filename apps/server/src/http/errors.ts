import type { ErrorRequestHandler, RequestHandler } from "express";
import type { Logger } from "pino";
import { ZodError } from "zod";

/** An error meant for the client: its status, code and message are sent as-is. */
export class AppError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
  }
}

export const notFound = (what: string) => new AppError(404, "not_found", `${what} not found`);
export const badRequest = (message: string, details?: unknown) =>
  new AppError(400, "bad_request", message, details);

export const unknownRoute: RequestHandler = (req, _res, next) => {
  next(new AppError(404, "not_found", `No route for ${req.method} ${req.path}`));
};

/**
 * The one place errors become responses.
 *
 * Anything that is not an AppError is a bug or an outage: it is logged in full
 * and the client gets a generic message, because a stack trace or a SQL error
 * in a response tells an attacker about the internals and tells a user nothing.
 */
export function errorHandler(log: Logger): ErrorRequestHandler {
  return (error, req, res, _next) => {
    if (error instanceof ZodError) {
      res.status(400).json({
        error: { code: "validation_error", message: "Some fields are invalid", details: error.flatten() },
      });
      return;
    }
    if (error instanceof AppError) {
      res.status(error.status).json({
        error: { code: error.code, message: error.message, details: error.details },
      });
      return;
    }
    // body-parser's own errors (malformed JSON, payload too large) carry a status.
    const status = typeof error?.status === "number" ? error.status : 500;
    if (status < 500) {
      res.status(status).json({ error: { code: error.type ?? "bad_request", message: error.message } });
      return;
    }
    log.error({ err: error, method: req.method, path: req.path }, "unhandled error");
    res.status(500).json({ error: { code: "internal_error", message: "Something went wrong on our side." } });
  };
}
