# Queueing System Notes

## Supported queue modes
- `QUEUE_MODE=memory` (default): uses an in-process `queue.Queue` and is suitable for local development.
- `QUEUE_MODE=redis`: stores jobs as JSON blobs in a Redis list identified by `REDIS_QUEUE_KEY` (defaults to `wordflux:jobs`). Connection settings come from `REDIS_URL` or `REDIS_HOST`/`REDIS_PORT`/`REDIS_DB`/`REDIS_PASSWORD`.

## Default loader behaviour
- The first call to `src.core.queue.load_default_queue()` decides the mode and queue instance; the result is cached globally.
- Subsequent calls return the cached instance even if environment variables are changed afterwards. This avoids subtle bugs from multiple queue instances in the same process, but it means that changing `QUEUE_MODE` requires restarting the process (or calling `set_default_queue()` manually during tests).
- Tests that need a fresh queue should reset the cache with `set_default_queue(None)` before reconfiguring environment variables.

## Error handling
- Jobs retrieved from Redis are parsed from JSON. Invalid payloads (non-JSON, non-mapping, or missing `agent`) are dropped and logged with a warning (`redis_queue_decode_error`, `redis_queue_payload_not_mapping`, or `redis_queue_missing_agent`).
- `job_id` and `enqueued_at` are auto-populated when missing to keep workers resilient to older producers.

## Durability roadmap
- Current Redis implementation offers at-most-once delivery: jobs are removed from the Redis list when claimed.
- Near-term enhancement: introduce a processing list + acknowledgement pattern (e.g. `BRPOPLPUSH`/`BLMOVE` paired with `LREM`) so failed workers can replay unacknowledged jobs.
- Longer-term option: migrate to Redis Streams with consumer groups for built-in pending lists and acknowledgements. Either approach will be documented here once implemented.
