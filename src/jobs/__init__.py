"""Background jobs: PostgreSQL holds every job's state, Redis dispatches them.

    queue.py      enqueue, claim, complete, fail, retry — the state machine
    dispatch.py   how a job id reaches a worker: Redis, or polling the table
    handlers.py   what each job type actually does
    worker.py     the long-running process that runs them
"""
