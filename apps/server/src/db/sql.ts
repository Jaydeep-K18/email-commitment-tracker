/**
 * A small builder for queries whose WHERE clause depends on the request.
 *
 * Every value goes through `param()` and becomes a numbered placeholder, so
 * user input never reaches SQL text. Identifiers (sort columns, directions)
 * are never taken from input at all — callers map validated enum values to
 * fixed SQL fragments.
 */
export class QueryParts {
  readonly params: unknown[] = [];
  private readonly conditions: string[] = [];

  param(value: unknown): string {
    this.params.push(value);
    return `$${this.params.length}`;
  }

  where(condition: string): this {
    this.conditions.push(condition);
    return this;
  }

  get whereSql(): string {
    return this.conditions.length ? `WHERE ${this.conditions.join(" AND ")}` : "";
  }

  /** A copy with the same params and conditions, to branch a query from. */
  clone(): QueryParts {
    const copy = new QueryParts();
    copy.params.push(...this.params);
    copy.conditions.push(...this.conditions);
    return copy;
  }
}

/** Escape LIKE's wildcards so user text matches literally. */
export function likeLiteral(text: string): string {
  return text.replace(/[\\%_]/g, (char) => `\\${char}`);
}
