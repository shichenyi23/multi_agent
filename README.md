# LLM4EDA Multi-Agent Scaffold

This repository is a minimal but executable scaffold for the workflow:

`Spec Analyst -> RTL Coder -> Testbench Agent -> Simulation Agent -> Synthesis Agent`

The goal is to keep every handoff structured. Agents exchange JSON-backed artifacts instead of raw natural-language chat.

## Layout

```text
schemas/          JSON Schemas for the main artifacts
agents/           Agent entry points and generation logic
tools/            Verilator / Icarus Verilog / Yosys wrappers
workflow/         State machine, artifact store, CLI runner
artifacts/        Per-module working directories and generated files
contracts.py      Shared data contracts used across the pipeline
```

## Current Scope

This scaffold is intentionally conservative:

- `SpecAnalystAgent` validates and normalizes a draft spec into `ModuleSpec`.
- `RTLCoderAgent` emits a compileable RTL stub plus lint metadata.
- `TestbenchAgent` emits a compileable testbench skeleton plus lint metadata.
- `SimulationAgent` and `SynthesisAgent` run local tools when available and always return structured reports.
- `WorkflowOrchestrator` persists state under `artifacts/<module>/`.
- A pluggable LLM backend layer supports `rule-based` generation today and `openai-compatible` APIs when configured.
- The orchestrator now includes lint-repair and simulation-repair loops.

The code is ready for the next step: replacing template generation with an actual LLM backend.

## First MVP

1. Edit [artifacts/ram_sp/spec.json](/media/scy/661F-C410/windows_linux/multi-agent/artifacts/ram_sp/spec.json) until it reflects the intended module.
2. Generate RTL/TB only:

```bash
python -m workflow.runner artifacts/ram_sp --generate-only
```

3. Attempt the full flow if `verilator`, `iverilog`, `vvp`, and `yosys` are installed:

```bash
python -m workflow.runner artifacts/ram_sp --backend rule-based
```

4. Inspect the generated artifacts:

- `generated_rtl.v`
- `generated_tb.v`
- `rtl_meta.json`
- `tb_meta.json`
- `sim.json`
- `synth.json`
- `workflow_state.json`

The CLI prints a condensed summary by default. Use `--full-json` if you want the full structured state in stdout.

## Backend Selection

`rule-based` is the default backend and is intended for local smoke tests. It currently knows how to generate a working single-port RAM flow for the `ram_sp` example.

To use a chat-completions-compatible API instead, set:

```bash
export LLM4EDA_BACKEND=openai-compatible
export LLM4EDA_API_KEY=...
export LLM4EDA_MODEL=...
export LLM4EDA_API_BASE=https://api.openai.com/v1
```

Then run:

```bash
python -m workflow.runner artifacts/ram_sp --backend openai-compatible
```

If the backend is unavailable, the orchestrator falls back to the rule-based backend.

## Recommended Next Implementation Steps

1. Strengthen the rule-based testbench/reference-model library so more module classes can run end-to-end without a remote LLM.
2. Add prompt/result persistence per attempt so each repair step is auditable.
3. Extend `SimulationAgent` to patch `$display` and `$monitor` automatically when the coder asks for more observability.
4. Add a project-level scheduler that fans out submodules before top-level integration.
5. Add branch-based PPA comparison so the synthesis agent can keep or discard optimization attempts automatically.
