# Guide: Reading the Langfuse infra files

A companion to [`infra/langfuse/docker-compose.yml`](../infra/langfuse/docker-compose.yml)
and [`infra/langfuse/.env.example`](../infra/langfuse/.env.example). The goal is that after
reading this you can look at either file and know what every line is doing — without needing
to be a Docker expert. We build the mental model first, then explain each *kind* of line
once (so we don't repeat ourselves six times), then walk the files top to bottom.

---

## 1. The big picture (read this first)

### What we're actually doing

Langfuse isn't one program — it's a small *team* of programs that cooperate (a database, an
analytics store, a queue, a file store, a web app, a background worker). "Self-hosting
Langfuse" means running all of them on your laptop and wiring them together. Doing that by
hand would be painful, so we describe the whole team in **one file** —
`docker-compose.yml` — and a single command brings them all up.

### Three words you need: image, container, volume

- **Image** = a frozen, read-only template of a program plus everything it needs to run
  (think: a "class" in programming, or a recipe). Images live in a public library called
  **Docker Hub**; `docker.io/postgres:17` means "the Postgres image, version 17, from Docker
  Hub." The first time you boot, Docker *downloads* (pulls) these images — that's the
  several-GB download.
- **Container** = a running copy of an image (think: an "instance" of the class, or the meal
  you cooked from the recipe). Six images → six containers running side by side.
- **Volume** = a folder Docker manages on your disk that a container uses to store data that
  must **survive restarts**. Containers themselves are disposable — delete one and its
  internal files vanish — so anything you want to keep (your trace history, the database
  contents) is written to a *volume* instead. That's why the file ends with a `volumes:`
  list.

> Quick correction to a common mental model: the services are **run as containers**, not
> "mounted on your system," and the environment values come from **`.env`** (the copy you
> fill in), not from `.env.example`. `.env.example` is just the safe-to-share *template*;
> you copy it to `.env`, put real secrets in `.env`, and `.env` is gitignored so secrets
> never get committed.

### How the two files relate

```
.env.example  ──(you copy it)──►  .env  ──(Docker reads it)──►  docker-compose.yml
 (template,                       (real secrets,                 (uses ${VAR} placeholders
  committed)                       gitignored)                    that get filled from .env)
```

The compose file is full of placeholders like `${POSTGRES_PASSWORD}`. When you run the
`docker compose` command with `--env-file infra/langfuse/.env`, Docker reads your `.env`,
finds each placeholder, and substitutes the real value before starting anything. This
substitution is called **interpolation**.

### The six services and how they talk

```
        you (browser, :3000)              your Python (SDK)
                │                                  │
                ▼                                  ▼
        ┌──────────────┐  writes events   ┌────────────────┐
        │ langfuse-web │ ───────────────► │     redis      │  (a queue: a to-do list)
        └──────┬───────┘                  └───────┬────────┘
               │ reads/writes config              │ worker takes jobs off the queue
               ▼                                   ▼
        ┌──────────────┐                  ┌────────────────┐   writes analytics rows
        │   postgres   │                  │ langfuse-worker │ ─────────────────────────┐
        │ (config DB)  │                  └───────┬────────┘                           ▼
        └──────────────┘                          │ stores big payloads        ┌──────────────┐
                                                   ▼                            │  clickhouse  │
                                            ┌────────────┐                      │ (analytics)  │
                                            │   minio    │                      └──────────────┘
                                            │ (file store)│
                                            └────────────┘
```

The key idea: when a trace arrives, `langfuse-web` doesn't slowly write it to the analytics
database itself — it drops it onto a **queue** (Redis) and answers immediately, so it stays
fast. A separate **worker** picks jobs off the queue in the background and writes them to
**ClickHouse**. This "accept fast, process later" pattern is called **asynchronous
ingestion**, and it's why there are two Langfuse containers (web + worker) instead of one.

---

## 2. How to read the compose file's structure

### Top-level shape

The file has two top-level sections:

- `services:` — the list of containers to run (our six).
- `volumes:` — the named disk folders for data that must persist.

Everything indented under `services:` is one service; everything under it (like `image:`,
`ports:`) is a *setting* for that service.

### Placeholders: `${VAR:-default}` vs `${VAR:?error}`

You'll see two placeholder styles, and the difference is deliberate and important:

- `${REDIS_HOST:-redis}` — "use `REDIS_HOST` from `.env`; **if it's missing, fall back to
  `redis`**." Used for harmless, non-secret settings that have a sensible default. Example:
  the hostname `redis` is the same on every machine, so a default is fine.
- `${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in infra/langfuse/.env}` — "use
  `POSTGRES_PASSWORD` from `.env`; **if it's missing, STOP and print this error**." Used for
  real secrets. The `:?` means *fail loudly* — we never want a password silently falling back
  to a default like `mysecret`, which is how demo setups get accidentally shipped insecure.
  (The official Langfuse file ships insecure defaults here; we deliberately changed them to
  fail-fast — better practice for "real projects.")

So as you scan: `:-` = optional with a default, `:?` = required secret, no fallback.

### The YAML anchors (`&name`, `*name`, `<<: *name`)

Near the top you'll see `&langfuse-depends-on` and `&langfuse-worker-env`. These are
**anchors** — a YAML feature for "define once, reuse later," exactly like assigning a value
to a variable so you don't repeat it.

- `&langfuse-depends-on` labels the worker's `depends_on:` block. Later, the web service
  writes `depends_on: *langfuse-depends-on`, meaning "use the same block I labelled earlier."
- `&langfuse-worker-env` labels the worker's big `environment:` block. The web service then
  writes `<<: *langfuse-worker-env`, which means "**merge in** all of those environment
  variables here," and then adds a few of its own (like `NEXTAUTH_SECRET`).

Why: the web and worker containers are the *same Langfuse application* in two modes, so they
need nearly identical configuration. Anchors let us write that shared config once instead of
duplicating ~50 lines. If you ever edit a shared env var, you edit it in one place.

---

## 3. The per-service vocabulary (each key, explained once)

These keys recur across services. Learn them once here; in §4 we only call out what's
*special* per service.

- **`image:`** — which image (program) to run, and its version tag. `docker.io/redis:7` =
  Redis version 7 from Docker Hub. A tag like `:3` pins the major version so the stack
  doesn't silently jump to a breaking new release.
- **`restart: always`** — if the container crashes or you reboot your Mac, Docker restarts it
  automatically. So the stack comes back on its own.
- **`depends_on:` with `condition: service_healthy`** — start ordering. "Don't start me until
  these other services are not just *running* but *healthy*." This is why Postgres/ClickHouse/
  Redis/MinIO must report healthy before the Langfuse app boots — the app would crash if it
  tried to connect to a database that wasn't ready yet.
- **`ports:`** — exposes a port from inside the container to your machine, written
  `HOST:CONTAINER`. `3000:3000` means "connect `localhost:3000` on my Mac to port 3000 inside
  the web container." When you see `127.0.0.1:5432:5432`, the `127.0.0.1:` prefix means **only
  this machine can reach it** — other computers on your network cannot. (The web UI on `3000`
  has no such prefix, so it's reachable, which is fine for a UI; the databases are locked to
  localhost for safety.)
- **`environment:`** — the list of settings handed to the program as environment variables.
  This is where almost all configuration lives (passwords, URLs, feature flags).
- **`volumes:`** (inside a service) — connects a named volume to a path inside the container,
  written `VOLUME:PATH`. `langfuse_postgres_data:/var/lib/postgresql/data` means "store
  whatever Postgres writes to `/var/lib/postgresql/data` in the `langfuse_postgres_data`
  volume," so the database survives container restarts.
- **`healthcheck:`** — a little command Docker runs repeatedly to decide if a service is
  "healthy." E.g. Postgres runs `pg_isready`; ClickHouse pings its `/ping` URL. `interval`,
  `timeout`, `retries`, and `start_period` tune how often/patiently it checks. Other services'
  `depends_on` waits on these results.
- **`command:`** — overrides the default thing the image runs on start. We use it on Redis
  (to require a password) and MinIO (to create the storage bucket first).
- **`entrypoint:`** — the program that wraps `command:`. MinIO sets `entrypoint: sh` so its
  `command` runs as a little shell script.
- **`user: "101:101"`** — run the process as a specific (non-root) user id inside the
  container, a standard hardening practice ClickHouse expects.

---

## 4. Service by service

### `postgres` — the configuration database (OLTP)

The "normal" database. It stores the **transactional, structured config**: your
organizations, projects, users, API keys, and dataset definitions. "OLTP" (Online
Transaction Processing) just means a database optimized for lots of small reads/writes of
individual records — "fetch this user," "save this API key." Settings of note: it keeps data
in the `langfuse_postgres_data` volume, checks health with `pg_isready`, and `TZ/PGTZ: UTC`
fixes its clock to UTC so timestamps are consistent.

### `clickhouse` — the analytics store (OLAP)

A different *kind* of database, built for the opposite job: scanning and aggregating
**huge numbers of rows fast** — "average latency across the last 10,000 traces." That's
"OLAP" (Online Analytical Processing). It's *columnar*, which makes those big aggregations
much faster than a normal row database. This is where all your traces, observations, and
scores actually live. Two volumes: one for data, one for logs. We left its image untagged
(matching upstream) because Langfuse tightly couples ClickHouse compatibility to its `:3`
app images — pinning a random ClickHouse version could break the database migrations.

> Why two databases? It's a classic split: Postgres is great at "give me record #42,"
> ClickHouse is great at "summarize a million records." Using each for what it's good at is a
> real-world architecture pattern, and seeing it here is part of the point of running v3.

### `redis` — the queue and cache

An in-memory data store used here as a **queue** (a to-do list of incoming events) sitting
between the fast web service and the background worker. Its `command:` does two things:
`--requirepass …` forces a password, and `--maxmemory-policy noeviction` tells Redis *never
to silently drop queued jobs* if memory fills up (you don't want to lose traces). Persists to
`langfuse_redis_data`; health-checked with `redis-cli ping`.

### `minio` — the file/blob store (S3-compatible)

Some payloads (large request/response bodies, media) are too big to stuff into a database, so
they go into **object storage** — basically a private file bucket. MinIO is a local stand-in
for Amazon S3 that speaks the same API, so the same Langfuse code works locally and in the
cloud. Its `command:` first creates the `langfuse` bucket (`mkdir -p /data/langfuse`) and then
starts the server. Port `9090` is the storage API; `9091` is MinIO's own admin console.
Files live in `langfuse_minio_data`.

### `langfuse-web` — the UI + API (the front door)

The part you actually see: the web app at `http://localhost:3000` and the API endpoint your
Python SDK (the observability hook and the eval harness) sends data to. It reuses all the
worker's environment (`<<: *langfuse-worker-env`) plus a few of its own:
`NEXTAUTH_SECRET` (secures login sessions) and the optional `LANGFUSE_INIT_*` variables
(covered in §5C). It waits for all four backing services to be healthy before starting.

### `langfuse-worker` — the background processor (the engine room)

The same Langfuse application running in "worker" mode. It has no UI; its job is to pull
queued events off Redis and write them into ClickHouse (and large payloads into MinIO). It
holds the master `environment:` block (the anchor the web service borrows) because it needs
connection details for *every* backing service. Exposes `3030` (its internal metrics/health
port) bound to localhost only.

---

## 5. The `.env` file, variable by variable

The file is split into three labelled groups. Remember: anything not listed here uses a safe
default baked into the compose file — so this is the *minimal* set you actually set.

### Group A — server secrets (consumed by the stack)

These are the `:?` required secrets from §2. Three are 32-byte random secrets you generate
with `openssl rand -hex 32`; the rest are passwords you invent.

- **`NEXTAUTH_SECRET`** — signs your login session cookies for the web UI. Random secret.
- **`SALT`** — extra randomness mixed in when hashing sensitive values (like API keys) before
  storage, so identical inputs don't produce identical hashes. Random secret.
- **`ENCRYPTION_KEY`** — encrypts certain sensitive fields at rest. Must be **exactly 64 hex
  characters** — which is precisely what `openssl rand -hex 32` produces (32 bytes = 64 hex
  chars). Random secret.
- **`POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`** — the login, password, and
  database name for Postgres. You only need to change the password; user/db default fine.
- **`CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`** — same idea for ClickHouse.
- **`REDIS_AUTH`** — the password Redis requires (matches the `--requirepass` in its command).
- **`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`** — the admin login for MinIO.

> **The "set it once" payoff.** In the official file you'd have to repeat the Postgres
> password inside a long `DATABASE_URL`, and the MinIO password in *three* separate S3 secret
> variables — easy to get out of sync. We changed the compose to *build* `DATABASE_URL` from
> `POSTGRES_USER/PASSWORD/DB`, and to *derive* all three S3 credentials from
> `MINIO_ROOT_USER/PASSWORD`. So you set the Postgres password in one place and the MinIO
> password in one place; everything downstream follows automatically. That's why your `.env`
> is shorter than the variable list the services actually use.

### Group B — client keys (consumed by *your* Python, after boot)

These are **not** used by the Docker stack at all — they're for the eval harness here and the
observability hook in the RAG repo, which talk to Langfuse over the network using its SDK.

- **`LANGFUSE_HOST`** — where Langfuse is, i.e. `http://localhost:3000`.
- **`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`** — the API key pair. You can't know these
  until *after* the stack is up: you boot it, open the UI, create a project, and it generates
  the keys. Then you paste them back here (and into the RAG repo's env). Think of them like a
  username/password your code uses to authenticate when sending traces.

### Group C — optional auto-provision (commented out by default)

The `LANGFUSE_INIT_*` variables are an advanced shortcut: if you fill them in, Langfuse
will **create your org, project, user, and even fix the API keys automatically on first
boot**, so you can skip the manual click-through in the UI. They're left commented out on
purpose, because doing it once by hand in the UI teaches you how orgs/projects/keys fit
together — which is the point of a learning project. Once you're comfortable, uncommenting
these makes the whole stack reproducible from config alone (handy for "real projects").

---

## 6. What actually happens when you run `docker compose … up -d`

A narrated boot sequence, so the logs make sense:

1. **Read `.env`** and substitute every `${VAR}` placeholder in the compose file. If a
   required `:?` secret is missing, it stops *here* with a clear message (you saw this when we
   verified it).
2. **Pull images** from Docker Hub that you don't already have (the big first-run download).
3. **Start the four backing services** (postgres, clickhouse, redis, minio) and run their
   **healthchecks** until each reports healthy.
4. **Start langfuse-web and langfuse-worker** (their `depends_on` was waiting for step 3).
   On first boot the app runs **database migrations** — creating all its tables in Postgres
   and ClickHouse. This is why the first start takes a couple of minutes.
5. **`langfuse-web` logs `Ready`** — the UI is live at `http://localhost:3000`.
6. `-d` ("detached") just means it all runs in the background; you get your terminal back.

Useful follow-ups:
- `docker compose … ps` — see all six and whether they're healthy.
- `docker compose … logs -f langfuse-web` — watch the web logs (for the "Ready" line).
- `docker compose … down` — stop and remove the containers (your data stays in the volumes).
- `docker compose … down -v` — also delete the volumes → **wipes all traces/config**, a
  clean slate.

---

## Mini-glossary

- **Docker Hub** — the public library images are downloaded from.
- **Image / container / volume** — recipe / cooked meal / the pantry that outlives the meal.
- **Interpolation** — Docker substituting `${VAR}` with a value from `.env`.
- **OLTP (Postgres)** — database for many small record-level operations.
- **OLAP (ClickHouse)** — database for fast aggregation over huge row counts.
- **Asynchronous ingestion** — accept the event fast onto a queue, process it later via the
  worker, so the front door never blocks.
- **Object storage / S3 / MinIO** — a file-bucket service for large blobs.
- **Healthcheck** — a probe deciding if a service is ready, gating `depends_on`.
- **Anchor (`&`/`*`/`<<:`)** — YAML's "define once, reuse," used to share config between the
  web and worker services.
