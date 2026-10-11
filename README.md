# Trustvian + vLLM — Quick Start (step by step)

Goal: run a small **framework-free** tool-calling agent — a plain loop on the
OpenAI SDK, with the model served by **vLLM** — under `trustvian dev` without
changing the agent's code, watch the live behavior graph, then give the agent
a new tool and watch Trustvian report the behavioral change and fail the gate.

Same scenario as the CrewAI quick start (support triage, fake CRM, the
candidate gains `export_customer`); what changes is the model server and the
absence of an agent framework.

Time: about 15 minutes once vLLM is serving.

## 0. Prerequisites

### vLLM (on a Linux machine with an NVIDIA GPU)

vLLM has no GPU support on macOS. Run it on a Linux box or a cloud GPU and
point `VLLM_BASE_URL` at it; the agent and Trustvian run on your Mac.

Either with Docker:

```bash
docker run --gpus all --ipc=host -p 8000:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-7B-Instruct --served-model-name qwen2.5-7b \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --max-model-len 8192
```

or with pip:

```bash
pip install vllm
vllm serve Qwen/Qwen2.5-7B-Instruct --served-model-name qwen2.5-7b \
  --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 8192
```

Notes:

- `--enable-auto-tool-choice` and `--tool-call-parser` are required for
  `tool_choice="auto"`; without them vLLM returns 400. The parser depends on
  the model family: `hermes` for Qwen 2.5/3, `llama3_json` for Llama 3.x,
  `mistral` for Mistral.
- `--served-model-name qwen2.5-7b` is what the agent sends as `model` and what
  Trustvian shows as the behavior (`llm · qwen2.5-7b`). Change `LLM_MODEL` if
  you serve under another name.
- Qwen2.5-7B-Instruct in bf16 needs ~16 GB of VRAM. On a smaller GPU use
  `Qwen/Qwen2.5-3B-Instruct` (same parser).

Check from your Mac:

```bash
export VLLM_BASE_URL=http://<gpu-host>:8000/v1
curl -s $VLLM_BASE_URL/models | head
```

### On your Mac

| What | How |
|---|---|
| Python 3.11+ | `python3 --version` |
| Trustvian binaries | **(a)** release archive: download the macOS/Linux archive, extract it, add the directory to `PATH` — `trustvian`, `trustvian-local` and `trustvian-collector` ship side by side. **(b)** from a checkout: `git clone https://github.com/trustvian/trustvian && cd trustvian && make build dev-binaries && export PATH="$PWD/bin:$PATH"` |
| Check | `trustvian dev --help` must work. |

`dev` does not run on Windows; use WSL2.

## 1. Project files

```text
requirements.txt     OpenAI SDK + OpenInference + OTel SDK
mock_services.py     fake CRM on 127.0.0.1:8081 (standard library, no dependencies)
tools.py             tool functions + JSON schemas — imports NEITHER OTel NOR Trustvian
agent.py             the tool-calling loop on vLLM — imports NEITHER OTel NOR Trustvian
run.py               instrumentation harness; runs agent.py unchanged
live.py              live mode (step 7): starts and supervises three agents
roles.py             live mode's roles, tickets and per-worker loop
tests/               live mode and content checks; no model needed
```

`agent.py` and `tools.py` are your application. `run.py` stands in for the
instrumentation a framework would ship: it builds the SDK from the
environment variables `trustvian dev` hands the child process, turns on the
OpenAI and requests instrumentors, wraps every tool in `tools.REGISTRY` with
an OpenInference TOOL span, and then runs `agent.py` as-is.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Start the fake CRM (Terminal A)

```bash
python mock_services.py
# listening on http://127.0.0.1:8081
```

## 3. Reference run (Terminal B)

```bash
export VLLM_BASE_URL=http://<gpu-host>:8000/v1
export LLM_MODEL=qwen2.5-7b                 # the --served-model-name from step 0
export OPENINFERENCE_HIDE_INPUTS=true       # keep prompt/output content out of spans
export OPENINFERENCE_HIDE_OUTPUTS=true

trustvian dev --instrumentation existing \
  --agent support-agent --candidate ref \
  -- python run.py
```

The banner shows `Run dev-ref-...` (note the run id) and `Web http://127.0.0.1:<port>/`.
Open the web address: in the live view, under the `support-agent` actor, you
should see

```text
tool · lookup_customer      (GET → 127.0.0.1:8081 beneath it)
tool · send_reply           (POST → 127.0.0.1:8081 beneath it)
llm  · qwen2.5-7b
```

All of them are NEW on the first run. When the agent finishes, dev prints
"Run ... complete".

## 4. Candidate run: add a tool to the agent

`AGENT_VARIANT=candidate` offers a third tool (`export_customer`) and the task
text nudges the agent to use it. No code change, only an environment variable;
in real life this would be a pull request.

```bash
AGENT_VARIANT=candidate \
trustvian dev --instrumentation existing \
  --agent support-agent --candidate cand \
  -- python run.py
```

Note the run id (`dev-cand-...`).

## 5. Compare

```bash
trustvian eval compare \
  --reference-run dev-ref-<...> --candidate-run dev-cand-<...> \
  --max-added-behaviors 0 --max-block-decisions 0 --max-critical-risk-observations 0
echo "exit: $?"
```

Expected: `Behaviors: +1 / …`, `FAIL Added behaviors: 1 (maximum 0)`,
`Gate: FAIL`, exit code 1. The added behavior is the `export_customer` tool;
`--json` lists it under `behavior_diff`. Run the same command with
`--max-added-behaviors 1` and it passes. The Compare tab in
the WebUI shows the same result; clicking a finding jumps to the observations
that are its evidence.

## 6. What is different from the CrewAI quick start

- **No framework.** `agent.py` is ~100 lines: send messages + tool schemas,
  execute whatever `tool_calls` come back, repeat until the model answers in
  text. This is the shape most in-house agents have.
- **TOOL spans come from the harness.** CrewAI's OpenInference instrumentor
  emits them for you; a hand-written loop has nobody to do that, so `run.py`
  wraps `tools.REGISTRY` entries with a span that carries
  `openinference.span.kind=TOOL` and `tool.name`. Those two attributes are what
  Trustvian's semantic normalization (task 075) reads. Nothing else — no
  arguments, no results — goes into the span.
- **LLM spans come from the OpenAI instrumentor.** The OpenAI SDK with a custom
  `base_url` is still the OpenAI SDK, so `openinference-instrumentation-openai`
  sees every chat completion against vLLM.
- **vLLM's own tracing is not used.** vLLM can emit OpenTelemetry spans itself
  (`--otlp-traces-endpoint`), but the server is a separate process with its
  own `service.name`; it would show up as a second actor, not as part of this
  agent. Leave it off for this exercise.

## 7. Live mode: a fleet that keeps working

Steps 3–5 are runs that end. Live mode is one that does not: three agents,
each its own process and its own actor, work a ticket every few seconds until
you stop them, so the live view fills continuously.

| Actor (`service.name`) | Tools | Tickets (eight each, e.g.) |
|---|---|---|
| `support-agent` | `lookup_customer`, `send_reply` | "How do I reset my password?" |
| `billing-agent` | `lookup_customer`, `lookup_invoice`, `issue_refund` | "I was charged twice this month." |
| `accounts-agent` | `lookup_customer`, `change_plan` | "Please upgrade me to Pro." |

`live.py` starts one worker per role, each `python run.py` with
`OTEL_SERVICE_NAME=<role>-agent` and `LIVE_ROLE=<role>`; `agent.py` sees
`LIVE_ROLE` and works that role's tickets with the same tool-calling loop. Two things
happen on purpose, and rarely:

- **Drift.** Every 20th billing ticket also binds `export_customer` and asks
  for an export: a new behavior on an actor Trustvian has already learned.
- **Failed calls.** The fake CRM answers about 5% of requests with `503`
  (`429` for refunds). The tool returns a short failure to the model, which
  usually tries again.

With the CRM from step 2 running (Terminal A) and step 3's exports
(`VLLM_BASE_URL`, `LLM_MODEL` and the two `OPENINFERENCE_HIDE_*`):

```bash
trustvian dev --instrumentation existing \
  --agent support-fleet --candidate live \
  -- python live.py
```

`live.py` prints one line per ticket, never its text:

```text
live: 3 agents (support-agent, billing-agent, accounts-agent), one ticket each every ~10 s, until stopped (Ctrl-C)
accounts-agent  ticket 1    tools: lookup_customer, change_plan  (4.1 s)
billing-agent   ticket 20   tools: lookup_customer, lookup_invoice, issue_refund, export_customer  (6.6 s)  [drift]
support-agent   ticket 4    tools: lookup_customer, send_reply, send_reply  (6.4 s)
```

Stop it with **Ctrl-C**: `live.py` stops every worker (SIGTERM, SIGKILL after
5 s), prints the tickets per agent and exits 0, and dev prints `Run
dev-live-… complete.`

What a 5½-minute run showed (Trustvian v0.10.0, `qwen2.5:3b` served by
Ollama's OpenAI-compatible endpoint — `VLLM_BASE_URL=http://localhost:11434/v1`,
no GPU at hand — on an Apple M3 Pro laptop, `OLLAMA_NUM_PARALLEL=1`):

- **One agent in the live view, three actors in the evidence.** The live
  view's Agent, its Behavior flow and every Live stream row name the run's
  agent, `support-fleet`. Which worker did what is in each observation's
  `actor_id` (`support-agent`, `billing-agent`, `accounts-agent`), which
  `GET /v1/evaluation-runs/<run>/observations` returns. `support-agent` is
  also the `--agent` of steps 3–5; it is a different run and candidate.
- **A steady stream.** 76 tickets (26, 24 and 26), about 550 observations;
  a ticket took 3–3.5 s at the median and 7.3 s at most, so the 10 s interval
  keeps up even with the three workers queueing on one model.
- **9 behaviors.** `external/qwen2.5:3b → openai`, `http/GET` and
  `http/POST → 127.0.0.1`, and six tools, each under the actor whose role has
  it: `lookup_customer`, `send_reply`, `lookup_invoice`, `issue_refund`,
  `change_plan`, and the drift's `export_customer`.
- **The drift** appeared once, at billing ticket 20, about four and a half
  minutes in: `tool/export_customer`, NEW.
- **Failed calls:** 11 HTTP observations with an error status, each the
  `503` or `429` of one CRM request; the run went on.

The knobs, all environment variables:

| Variable | Default | |
|---|---|---|
| `LIVE_AGENTS` | `support,billing,accounts` | the roles to run |
| `LIVE_INTERVAL_SECONDS` | `10` | one ticket per agent this often, ±50% jitter |
| `LIVE_DRIFT_EVERY` | `20` | every Nth billing ticket also exports; `0` = never |
| `LIVE_MAX_TICKETS` | `0` | tickets per agent, then stop; `0` = until stopped |
| `CRM_ERROR_RATE` | `0.05` | share of CRM requests that fail; `0` = none. Read by `mock_services.py`, so set it in Terminal A |

The three workers share one model server. vLLM batches concurrent requests
by itself; Ollama serves `OLLAMA_NUM_PARALLEL` at a time and queues the
rest — at `1`, as measured above, a ticket waits for the other agents' model
calls, which is what the median includes. A slower model needs a longer
`LIVE_INTERVAL_SECONDS`.

With Docker Compose, live mode is a service of its own, attached to the
stack's control plane:

```bash
docker compose --profile live up -d       # add --profile vllm on the GPU machine
# open http://127.0.0.1:8080/
docker compose logs -f live               # the per-ticket lines
docker compose --profile live down        # stops it; dev completes the run
```

`down` keeps the volumes, so the next `up` is scored against what this one
taught; `down -v` forgets it. The knobs go in `.env`; `TRUSTVIAN_LIVE_AGENT`
renames `support-fleet`. The `agent` service and its subcommands are
unchanged. The tests cover `live.py`, every role's tool calls and that no
ticket text, reply or tool argument reaches a span; they need no model:

```bash
pip install -r requirements-dev.txt
pytest
```

## Running it with Docker Compose

The same steps with nothing on the machine but Docker — and, on a GPU box,
vLLM itself as part of the stack.

| Service | What it is |
|---|---|
| `control-plane` | `trustvian-local` with its SQLite database in a named volume; WebUI and API on `http://127.0.0.1:8080/` |
| `crm` | `mock_services.py` |
| `agent` | one-shot container: `trustvian dev --api-url http://control-plane:8080 … -- python run.py`. Used with `docker compose run`, never started by `up` |
| `vllm` | optional (`--profile vllm`): `vllm/vllm-openai` on this machine's NVIDIA GPU, started with the tool-calling flags from step 0. Without the profile the agent talks to `VLLM_BASE_URL` |

Why one long-lived control plane instead of letting `dev` start its own: `dev`
starts a private runtime per invocation and stops it when the run ends. Two
runs that need comparing must share one database, and the browser needs the
server to outlive the run that produced it — so the stack runs one control
plane and every run attaches with `--api-url`, the shape Trustvian's
`docs/local-development.md` calls "running the parts separately" and the one CI
uses.

Files: `compose.yaml`, `Dockerfile` (Trustvian build, release collector image,
control plane, agent runner), `compose/control-plane.sh`, `compose/agent-entrypoint.sh`,
`.env.example`.

### 1. Build and start

On a Linux machine with an NVIDIA GPU and the NVIDIA Container Toolkit:

```bash
cp .env.example .env                       # optional
docker compose --profile agent --profile vllm build
docker compose --profile vllm up -d
docker compose logs -f vllm                # until "Application startup complete"
```

From a Mac, with vLLM running elsewhere (step 0 of the native flow):

```bash
cp .env.example .env
# VLLM_BASE_URL=http://<gpu-host>:8000/v1 in .env
docker compose --profile agent build
docker compose up -d
```

`--profile agent` matters: the agent is not started by `up`, so a plain
`docker compose up --build` never rebuilds it and a changed
`requirements.txt` would not reach the image.

The first build clones Trustvian (`TRUSTVIAN_REF`, default `v0.10.0`) and
compiles `trustvian` and `trustvian-local`; `trustvian-collector` is copied
out of the signed release image
`ghcr.io/trustvian/trustvian-collector:v0.10.0`, pinned by digest
(`TRUSTVIAN_COLLECTOR_IMAGE`). To move to another release, change both
values in `.env` together. `vllm/vllm-openai`
is a large image and the model weights download into the `hf-cache` volume on
first start — plan for several minutes. Afterwards `http://127.0.0.1:8080/` is
the WebUI, published on the host's loopback only.

### 2–5. Reference, candidate, compare

```bash
docker compose run --rm agent reference    # waits for /v1/models, then runs; prints "run id ref-<timestamp>"
docker compose run --rm agent candidate    # "run id cand-<timestamp>"
docker compose run --rm agent compare      # gates the last two; exit 1 = FAIL
echo "exit: $?"
```

Expected result is the same as in the native flow: `Behaviors: +1 / …`,
`FAIL Added behaviors: 1 (maximum 0)`, `Gate: FAIL`, exit 1, with
`export_customer` as the added behavior. `compare` also accepts two explicit
ids (`docker compose run --rm agent compare ref-… cand-…`), and the gate
limits come from `MAX_ADDED_BEHAVIORS`, `MAX_BLOCK_DECISIONS`,
`MAX_CRITICAL_RISK_OBSERVATIONS` (all default 0), passed with `-e`:
`docker compose run --rm -e MAX_ADDED_BEHAVIORS=1 agent compare`.

Two things read differently from the native flow:

- `dev`'s banner prints `Web http://control-plane:8080/` — that is the name
  inside the Compose network. On the host, open `http://127.0.0.1:8080/`.
- Run ids are `ref-<utc time>` / `cand-<utc time>`, chosen by the entrypoint
  so `compare` can find the last pair without parsing `dev`'s output.

### Other commands

```bash
docker compose run --rm agent trustvian eval get --id ref-…   # any CLI command, --api-url pre-set
docker compose run --rm agent shell                           # bash in the agent container
docker compose run --rm agent python agent.py                 # the agent without tracing
docker compose logs control-plane                             # the runtime's log
docker compose down                                           # stop; runs and baselines are kept
docker compose down -v                                        # stop and discard all state (and the model cache)
```

Editing `agent.py` or `tools.py` needs no rebuild (the directory is
bind-mounted); changing `requirements.txt` does: `docker compose --profile agent build agent`.

### Compose troubleshooting

| Symptom | Cause / fix |
|---|---|
| `the control plane did not answer at http://control-plane:8080` | `docker compose ps` — is `control-plane` healthy? `docker compose logs control-plane` has the reason |
| `no model server answered at …/models` | With the profile: `docker compose logs vllm` (weights still loading, out of GPU memory → smaller `VLLM_MODEL` or lower `VLLM_MAX_MODEL_LEN`). Without it: `VLLM_BASE_URL` must be reachable *from a container* — `localhost` there is the container, not your Mac; use the GPU host's name or `host.docker.internal` for a tunnel |
| `could not select device driver "nvidia"` | The NVIDIA Container Toolkit is not installed, or you are on a Mac: vLLM has no GPU support there — run it on a Linux GPU machine and set `VLLM_BASE_URL` |
| Build fails while fetching Go modules or cloning | Network/proxy on the build host; `TRUSTVIAN_REF` must be a branch, tag or full SHA that exists |
| Build fails pulling `ghcr.io/trustvian/trustvian-collector` | Registry unreachable, or `TRUSTVIAN_COLLECTOR_IMAGE` names a digest that does not exist; `docker pull` it by hand to see the error |
| `compare` says no run recorded | Run `reference` and `candidate` first, or pass the two ids explicitly |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| vLLM returns 400 mentioning `tool_choice` | Start vLLM with `--enable-auto-tool-choice --tool-call-parser <parser>` |
| 404 "model not found" | `LLM_MODEL` must equal `--served-model-name` (or the HF id when that flag is absent); `curl $VLLM_BASE_URL/models` lists what is served |
| Connection refused / timeout | `VLLM_BASE_URL` wrong, or port 8000 not reachable from the Mac (firewall, SSH tunnel: `ssh -L 8000:localhost:8000 <gpu-host>` then use `http://localhost:8000/v1`) |
| Model prints JSON like `{"name": "lookup_customer", ...}` as text instead of calling | Wrong `--tool-call-parser` for the model family |
| dev says "cannot establish instrumentation ownership" | `--instrumentation existing` is missing |
| dev says "cannot determine which agent" | `--agent support-agent` is missing, or `OTEL_SERVICE_NAME` is unset |
| Live view is empty | Does `run.py` print "exporter → http://127.0.0.1:.../v1/traces" at startup? If not, `opentelemetry-exporter-otlp-proto-http` is not installed in the venv |
| `llm ·` rows missing | `openinference-instrumentation-openai` not installed; check with `pip show` |
| `tool ·` rows missing, only HTTP | You ran `python agent.py` instead of `python run.py` — only the harness adds TOOL spans |
| Tool calls show up but all go to one host | Expected: 075 reads the tool name, so the behavior is `tool · <name>`; the HTTP child is not counted separately (#132) |

## Running the CrewAI quick start against vLLM

The CrewAI example from `trustvian-crewai-quickstart/` works unchanged against
the same vLLM server. CrewAI 1.x reaches vLLM through the openai SDK under the
`hosted_vllm/` provider prefix:

```bash
cd ../trustvian-crewai-quickstart
export LLM_MODEL=hosted_vllm/qwen2.5-7b
export LLM_BASE_URL=http://<gpu-host>:8000/v1
# VLLM_API_KEY is needed only if vLLM was started with --api-key
# then steps 3–5 of that README exactly as written
```
