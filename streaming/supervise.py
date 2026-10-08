"""Keeps the metrics job submitted to the Flink session cluster.

A session cluster forgets its jobs when the job manager restarts, so instead of
submitting once, this checks every CHECK_SECONDS and submits whenever the job
is not there.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

JOB_NAME = "commitmail-metrics"
REST = os.environ.get("FLINK_REST", "http://flink-jobmanager:8081")
CHECK_SECONDS = 30
ALIVE = {"INITIALIZING", "CREATED", "RUNNING", "RESTARTING", "FAILING", "CANCELLING", "RECONCILING"}


def job_alive() -> bool:
    with urllib.request.urlopen(f"{REST}/jobs/overview", timeout=5) as response:
        jobs = json.load(response)["jobs"]
    return any(job["name"] == JOB_NAME and job["state"] in ALIVE for job in jobs)


def submit() -> None:
    address = REST.split("://", 1)[-1]
    print(f"submitting {JOB_NAME} to {address}", flush=True)
    subprocess.run(
        ["flink", "run", "--detached", "-m", address, "-py", "/opt/streaming/job.py", "-pyfs", "/opt/streaming"],
        check=False,
    )


def main() -> int:
    while True:
        try:
            if not job_alive():
                submit()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"Flink not reachable yet: {exc}", file=sys.stderr, flush=True)
        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
