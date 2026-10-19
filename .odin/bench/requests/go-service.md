I need a webhook relay service written in Go. It receives webhooks over HTTP
and forwards them to one or more configured destination URLs.
Failed deliveries must be retried with exponential backoff, up to 6 attempts,
then dropped to a log line.
Expose Prometheus metrics at /metrics (deliveries, failures, retries, latency).
Configuration comes from a single YAML file, passed with a --config flag,
listing the routes and their destinations.
Single static binary, no database; queued deliveries can live in memory.
Run it as a systemd service on a small Linux VM.
