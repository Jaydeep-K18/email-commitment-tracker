import type { z, ZodTypeAny } from "zod";

import { AppError } from "./errors";

/** Validate untrusted input against a shared schema, or answer 400. */
export function parse<T extends ZodTypeAny>(schema: T, value: unknown): z.infer<T> {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new AppError(400, "validation_error", "Some fields are invalid", result.error.flatten());
  }
  return result.data;
}
