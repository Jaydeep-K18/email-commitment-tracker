import { describe, expect, it } from "vitest";

import { envSchema } from "../src/env";

const base = { DATABASE_URL: "postgres://u:p@127.0.0.1:5432/db" };

describe("configuration", () => {
  it("allows this server and the dev server by default, on the ports in use", () => {
    expect(envSchema.parse({ ...base, PORT: "4100", WEB_PORT: "5180" }).APP_ORIGINS).toEqual([
      "http://127.0.0.1:4100",
      "http://localhost:4100",
      "http://127.0.0.1:5180",
      "http://localhost:5180",
    ]);
    expect(envSchema.parse(base).APP_ORIGINS).toContain("http://localhost:5173");
  });

  it("uses an explicit origin list as given", () => {
    expect(envSchema.parse({ ...base, APP_ORIGINS: "https://mail.example.com, http://x:1" }).APP_ORIGINS).toEqual([
      "https://mail.example.com",
      "http://x:1",
    ]);
  });
});
