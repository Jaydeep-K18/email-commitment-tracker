/**
 * The System page's one-minute metrics, from per-minute counts.
 *
 * A copy of streaming/window_metrics.py, which the Flink job runs. The server's
 * fallback uses this one when Flink is not running, and contracts/metric-windows.json
 * (written from the Python) keeps the two giving the same numbers.
 */
import type { MetricWindow } from "./api";

/** Spikes and anomalies are judged against the trailing hour, this minute included. */
export const BASELINE_MINUTES = 60;

export interface MinuteCounts {
  events: number;
  processed: number;
  succeeded: number;
  failures: number;
  durationsMs: number[];
}

/** Postgres's percentile_cont: linear interpolation between neighbours. */
export function percentileCont(values: number[], fraction: number): number {
  const ordered = [...values].sort((a, b) => a - b);
  const position = fraction * (ordered.length - 1);
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return ordered[lower]! + (ordered[upper]! - ordered[lower]!) * (position - lower);
}

/**
 * Metrics for each minute of a contiguous series, oldest first. A minute's
 * baseline is the BASELINE_MINUTES ending with it, as far as the series reaches,
 * so pass BASELINE_MINUTES - 1 extra minutes in front of the ones you want.
 */
export function windowMetrics(series: MinuteCounts[]): MetricWindow["metrics"][] {
  return series.map((minute, i) => {
    const baseline = series.slice(Math.max(0, i - BASELINE_MINUTES + 1), i + 1);
    const meanFailures = baseline.reduce((sum, m) => sum + m.failures, 0) / baseline.length;
    const meanEvents = baseline.reduce((sum, m) => sum + m.events, 0) / baseline.length;
    const sd = Math.sqrt(baseline.reduce((sum, m) => sum + (m.events - meanEvents) ** 2, 0) / baseline.length);
    const attempts = minute.succeeded + minute.failures;
    const durations = minute.durationsMs;
    return {
      events: minute.events,
      emailsProcessed: minute.processed,
      throughputPerMinute: minute.processed,
      avgLatencyMs: durations.length ? Math.round(durations.reduce((sum, d) => sum + d, 0) / durations.length) : null,
      p95LatencyMs: durations.length ? Math.round(percentileCont(durations, 0.95)) : null,
      successRate: attempts ? Math.round((minute.succeeded / attempts) * 1000) / 10 : null,
      failures: minute.failures,
      errorSpike: minute.failures >= 3 && minute.failures >= 3 * meanFailures,
      volumeAnomaly: sd > 0 && minute.events >= 10 && (minute.events - meanEvents) / sd > 3,
    };
  });
}
