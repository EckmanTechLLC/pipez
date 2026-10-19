Build me a URL shortener API with Node.js and Express.
Storage should be SQLite, a single file is fine, I don't want to run a database server.
It needs rate limiting per client IP, say 60 requests a minute, returning 429 when exceeded.
Ship it as a Docker image I can run with one command, listening on port 3000.
Endpoints: POST /shorten with a URL, GET /:code redirecting to the original.
Short codes should be 7 characters, base62.
Keep it small, no user accounts or auth for now.
