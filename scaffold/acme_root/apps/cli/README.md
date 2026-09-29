# Acme CLI

Acme from the terminal. A command does one thing and returns; `listen`
stays and prints every change in the org as it happens.

```bash
uv run acme login --org ajax        # confirm the code in the browser
uv run acme login --dev-email bob@example.test --org ajax   # local stack only
uv run acme whoami
uv run acme orgs                    # * marks the session's org
uv run acme switch fabrikam         # the old session ends
uv run acme upload spec.pdf         # --type, --json
uv run acme listen                  # who did what to which record
uv run acme logout
```

## Conventions

- A dumb client: every command calls the API through `acme.client` and
  shows the answer. A refusal prints the API's code, message, and request id.
- Exit codes: 0 done, 1 the API refused, 2 usage, 3 not signed in, 4 the
  API is unreachable. A setting the environment got wrong is usage.
- The session lives in `$ACME_HOME/session.json` (default `~/.config/acme`,
  mode 600) and goes only to the API that issued it. `ACME_TOKEN` wins over
  it. `ACME_API_URL` or `--api` names the API. `ACME_HTTP_TIMEOUT_SECONDS`,
  `ACME_HTTP_RETRIES`, and `ACME_HTTP_RETRY_BACKOFF_SECONDS` tune the client.

## Test

```bash
uv run pytest -q apps/cli/tests
```
