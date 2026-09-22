# arch-scaffold-new: the infrastructure distribution

Step 1 of `arch-scaffold-new` reads this file. It is the file-by-file
list of the infra distribution, under `<target-dir>/infra/`. The
conventions in `../_shared/scaffold-conventions.md` hold throughout, and
what must never be missed is in the skill's `Created` section, not here.

## Files

| File                                        | Holds                                                                                   |
|---------------------------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`                            | `<root>-infra`; `valkey-glide`, `aioboto3`, `opentelemetry-sdk`, `prometheus-client`, `sentry-sdk`, `truststore`; no dependency on `<root>-om`: `TopicPayload` is a frozen base declared here with `extra="ignore"`, and the system scope is the zero UUID checked by value |
| `src/<root>/infra/root.py`                  | `InfraInterface` with `start` and `close`                                                |
| `src/<root>/infra/exceptions.py`            | `InfraException` with `http_status` and `code`, the root of every exception infra raises, since infra imports nothing from the OM, as Cross-Cutting Conventions (Exceptions) states |
| `src/<root>/infra/{cache,buckets,topics,queues,secrets}/` | each: the interface (with `start()` and `close()`, returning `None` where an impl holds nothing), a memory or local impl that behaves like the hosted one (a queue twin does not deduplicate), the cloud impl translating driver errors into an `InfraException` and counting outcomes; `topics/` defines `Topics.WORK_AVAILABLE` and `Topics.ENTITY_CHANGED` with their payloads, the latter's `EntityChangedPayload` carrying `org_id` (from the base), `kind`, `target_id`, `seq`, and `actor_id`, and its one control kind, `SESSION_REVOKED`, carrying the session id as `target_id`, the identity id as `actor_id`, and `seq` as `None`, as the guideline's Topics names them, the two every later step produces or routes |
| `src/<root>/infra/observability.py`, `trust.py` | logging with the filter that puts the service, the environment, the request id, and the causing request on every record, error reporting (on only when the DSN is set and not `off`; events tagged with service, release, request id), tracing, the OS trust store                    |
| `src/<root>/infra/impl/settings.py`, `impl/configured.py`, `impl/local.py` | settings, the configured root that picks impls and refuses unsafe combinations (a `redis://` `cache_url` outside `local` among them), the all-local root |
| `tests/`                                    | every capability over the local impls, one test per operation of its interface; and the settings check: every field of `InfraSettings` appears in `.env.example` under its prefix (the test reads the file, so a knob added to settings and not documented fails the fast gate) |
