# Staged Information Exposure: Capability Gateways for Multi-Agent Systems

This repository contains the implementation used in our paper on multi-agent pipeline architecture evaluation.
In short, this codebase lets you compare two architectural families under the same deterministic evaluation setup:
- simple family with direct MCP and A2A exposure
- gateway family with unified capability abstraction over MCP and A2A and staged information exposure

[Paper reference](paper/main.pdf)

## Project Structure (Project Root)

```text
project-root/
├── evaluation/                         # Deterministic evaluation framework
│   ├── config/                         # Scenario sources and evaluation configuration
│   ├── data/                           # Persistent data/caches and temporary run artifacts
│   └── src/                            # Evaluation CLI, orchestration, scoring, summaries
├── mcp-server/                         # Shared MCP servers, including the synthetic bloat MCP server
├── pipelines/                          # Architecture implementations
│   ├── simple-crew/                    # Simple multi-agent pipeline
│   │   ├── agents/                     # Agent service implementations
│   │   └── tools/                      # Tool interfaces used by simple pipeline agents
│   └── gateway/                        # Gateway-based multi-agent pipeline
│       ├── agents/                     # Gateway-enabled agents
│       ├── gateway/                    # Gateway service, APIs, routing, orchestration
│       ├── mcp-server/                 # Gateway-specific MCP integration layer
│       └── vectorstore/                # Capability indexing and retrieval resources
├── scripts/                            # Operational entrypoints and run orchestration scripts
└── shared/                             # Shared configuration, logging, run context, and utilities
```

### Architecture Scope

The `pipeline` entrypoint supports four architecture labels:
- `simple`: baseline simple pipeline mode
- `simple-crew`: CrewAI-style baseline mode
- `gateway`: gateway architecture
- `gateway-rag`: gateway architecture with retrieval-augmented capability routing

Evaluation is architecture-agnostic: scenario preparation and scoring are the same regardless of the selected architecture.
Each architecture is treated as a black-box runtime target.

### Evaluation Configuration and Scenario Model

Primary user-editable files for evaluation behavior:

| File | Purpose |
|---|---|
| [scenarios](evaluation/config/scenarios.json) | Main scenario set used for evaluation |
| [test-scenarios](evaluation/config/test-scenarios.json) | Smaller scenario set intended for quick testing |
| [docker-compose.yml](docker-compose.yml) | Local Langfuse stack config |

Each scenario has the same schema:

```json
{
  "id": "S01",                          # scenario identifier
  "num_packages": 20,                   # total number of packages in the generated scenario SBOM
  "vulnerable_packages": 4,             # number of vulnerable packages in that SBOM
  "vulnerable_severity_counts": {       # severity distribution, sum must match 'vulnerable_packages'
    "critical": 1,
    "high": 1,
    "medium": 1,
    "low": 1
  }
}
```

This lets you model realistic risk profiles as they would occur in corporate software stacks.
All scenarios are generated in a preparation phase and then consumed in the evaluation phase.

For a more detailed information over the general pipeline evaluation loop, please refer to the paper.

## Setup

### Prerequisites

- `uv` package manager (install from [here](https://docs.astral.sh/uv/getting-started/installation/))
- OpenAI API access
- For Langfuse integration:
    - Docker (self-hosting) OR
    - Langfuse account

### First Steps

1. Create your local environment file:
    ```bash
    cp .env.example .env
    ```

2. Choose how you want to run Langfuse:
- Option A (self-hosted, local): start Langfuse via Docker
    ```bash
    docker compose up
    ```
- Option B (Langfuse Cloud): create a free account/project in Langfuse Web UI (no local Docker required)

3. If you run locally, open [http://localhost:3000](http://localhost:3000) and complete initial Langfuse onboarding:
- create a local user (any name/password)
- create an organization (any name)
- create a project (any name)

4. In Langfuse, go to Tracing and create API keys (local or cloud):
- `Tracing` -> `Configure Tracing` -> `Create Public Keys`
- copy the bottom box (public/secret keys and host) into [.env](.env) 

5. In [.env](.env), make sure that the following variables are set:
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST` (for local setup usually [http://localhost:3000](http://localhost:3000), for cloud your Langfuse project host URL)
- `OPENAI_API_KEY`
- `OPENAI_API_BASE`

6. If you change the Langfuse UI port in [docker-compose.yml](docker-compose.yml) (for example from `3000` to another port), also update `LANGFUSE_HOST` in [.env](.env) accordingly.

7. Install Python/runtime dependencies:
    ```bash
    uv sync
    ```

After that, tracing data from pipeline runs appears in the configured Langfuse project (local or cloud).

## Running Pipeline Evaluation

### 1) Preparation Phase (required before evaluation)

Generate prepared scenarios and caches from the default scenario source (first run might take while):
```bash
uv run pipeline --prep
```

For quick testing, you can also use the alternative test scenario source instead:
```bash
uv run pipeline --prep --test
```

Behavior:
- `--prep` uses [scenarios](evaluation/config/scenarios.json)
- `--prep --test` uses [test-scenarios](evaluation/config/test-scenarios.json)
- a manifest of all generated scenarios is created [here](evaluation/data/tmp/scenarios/manifest.json)

### 2) Evaluation Phase

Run one architecture against the prepared manifest:
```bash
uv run pipeline --arch <simple|simple-crew|gateway|gateway-rag>
```

Example:
```bash
uv run pipeline --arch gateway-rag
```

### 3) (Optional) MCP Bloat Mode

Enable synthetic MCP tool bloat during evaluation:
```bash
uv run pipeline --arch gateway-rag --mcp-bloat
```

You can also explicitly set the number of bloat tools added per agent:
```bash
uv run pipeline --arch gateway-rag --mcp-bloat --mcp-bloat-tools 1
```

Current bloat tool pool size is 15 tools total (5 per agent), tool descriptions length is standardized.

To evaluate how the metrics behave between `gateway-rag` and `gateway` when scaling the ecosystem, we used this script:
```bash
uv run bloat-sweep --start 0 --end 5 --step 1
```
The script will run the pipeline multiple times, effectively increasing tool bloat each run.
`--start` and `--end` refer to the amount of bloat tools of the first and last run, respectively, while `--step` determines how many tools are added per agent each iteration.
By default, `gateway` and `gateway-rag` will be evaluated, but you can also run only one of them using the `--arch` flag.
The results are displayed in figure 5 in the paper

### 4) Cleanup

Make sure to shut down the langfuse docker setup after evaluation:
```bash
docker compose down
```

Reset Langfuse state completely:
```bash
docker compose down -v
```

## Observability and Outputs

### Langfuse (Agent Trajectory and Tuning)

Langfuse is the primary place to inspect agent behavior and improve prompts/flows:
- end-to-end run trajectory
- path decisions / reasoning
- tool-calls, failures & related context

Use this to understand why an agent chose a path, where it failed, and where prompts should be refined.

### Runtime Logs (Local)

- Terminal output: high-level flow only (pipeline progress/status checkpoints).
- `logs/` folder: detailed component logs, debug-level diagnostics, timings, and stack traces.

In short: terminal logs are intentionally concise, file logs are for deep debugging and incident analysis.

### Evaluation Run Artifacts
| Location | Purpose |
|---|---|
| `evaluation/data/tmp/results/runs/<scenario_id>/<run_id>.json` | Contains scenario-level run metadata and scoring metrics |
| `evaluation/data/tmp/results/runs/summary/<run_id>.json` | A summary that averages metrics over all generated scenarios |
