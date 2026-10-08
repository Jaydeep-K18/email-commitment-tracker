/**
 * The TypeScript copies must match what Python wrote to contracts/.
 *
 * A failure here means the Python side changed. Update the list or the policy
 * in src/ to match — the assertion message says which entry differs.
 */
import { describe, expect, it } from "vitest";

import catalogue from "../contracts/catalogue.json";
import metricWindows from "../contracts/metric-windows.json";
import decisions from "../contracts/sync-decisions.json";
import {
  CATEGORIES,
  CATEGORY_RANK,
  COMMITMENT_STATUSES,
  COMMITMENT_TYPES,
  EVENT_TYPES,
  JOB_STATUSES,
  JOB_TYPES,
  MATCH_TYPES,
  REDIS_KEYS,
  SEVERITIES,
  TIERS,
  BASELINE_MINUTES,
  decide,
  windowMetrics,
  type PolicyInput,
} from "../src";

describe("catalogue matches the Python worker", () => {
  it.each([
    ["eventTypes", EVENT_TYPES],
    ["severities", SEVERITIES],
    ["categories", CATEGORIES],
    ["tiers", TIERS],
    ["matchTypes", MATCH_TYPES],
    ["commitmentTypes", COMMITMENT_TYPES],
    ["commitmentStatuses", COMMITMENT_STATUSES],
    ["jobStatuses", JOB_STATUSES],
  ] as const)("%s", (key, ours) => {
    expect([...ours]).toEqual(catalogue[key]);
  });

  it("job types, as registered handlers", () => {
    expect([...JOB_TYPES].sort()).toEqual(catalogue.jobTypes);
  });

  it("category rank", () => {
    expect(CATEGORY_RANK).toEqual(catalogue.categoryRank);
  });

  it("redis keys", () => {
    expect(REDIS_KEYS).toEqual(catalogue.redisKeys);
  });
});

describe("the calendar policy agrees with Python's decide()", () => {
  it(`covers all ${decisions.length} combinations`, () => {
    expect(decisions.length).toBeGreaterThan(300);
  });

  it.each(decisions.map((c) => [JSON.stringify(c.input), c] as const))(
    "%s",
    (_label, testCase) => {
      expect(decide(testCase.input as PolicyInput)).toEqual(testCase.output);
    },
  );
});

describe("metric windows match the Flink job's rules", () => {
  it("uses the same baseline", () => {
    expect(BASELINE_MINUTES).toBe(metricWindows.baselineMinutes);
  });

  it.each(metricWindows.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(windowMetrics(c.series)).toEqual(c.metrics);
  });
});
