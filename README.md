# DockIt AI

DockIt is a conversational exception-management service for SetuHaul freight
drivers. It helps drivers report delays, find feasible dock options, and safely
reschedule slots without conflicting promises.

## Development setup

This project uses a local Python 3.14 virtual environment because OR-Tools
CP-SAT currently provides a compatible macOS ARM wheel through Python 3.14.

```bash
source .venv/bin/activate
python -m pip install -e '.[dev,ui]'
```

Copy `.env.example` to `.env` before configuring AWS or Upstash credentials.

The SetuHaul SQLite database is a runtime input, not a checked-in asset:
`data/*.db` is ignored. Provision a schema-compatible source database locally
or in the runtime environment, and set `DOCKIT_SOURCE_DATABASE_PATH` if it is
not at the configured default. DockIt opens that database read-only. Its
writable state database uses the separate `DOCKIT_STATE_DATABASE_PATH`.

Run the service shell with `dockit-api`, then check `GET /healthz` (the process is
alive), `GET /readyz` (source contract validated and state migrations applied),
and `GET /api/v1` (the versioned API root). Startup opens the SetuHaul source
database read-only and initializes DockIt-owned state; Redis, Cognito, and AWS
integrations arrive in later delivery steps.

## Quality checks

```bash
ruff check .
mypy src
pytest
```

## Package layout

- `src/dockit/api`: FastAPI routing and dependencies.
- `src/dockit/agent`: LangChain orchestration and Redis conversation state.
- `src/dockit/scheduler`: deterministic feasibility and CP-SAT scheduling.
- `src/dockit/db`: SQLite access, transactions, repositories, and migrations.
- `src/dockit/schemas`: Pydantic contracts.
- `src/dockit/services`: business workflows used by API endpoints and tools.
- `migrations`: additive DockIt-owned database changes.
- `tests`: unit, integration, and concurrency evidence.
