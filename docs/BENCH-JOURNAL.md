# Benchmark Journal

> Pre-registration + journal per the agent-pipeline-rigor lesson. Write the
> hypothesis/questions HERE before running the benchmark. After each run,
> append results — never rewrite the pre-registration.

## Pre-registration: vision slot decision (2026-09-09)

**Questions before running:**
1. Does the golden grid separate competent vision models at the top? (expect: no)
2. Do dense-text probes separate them? (expect: yes, font-scale stress)
3. Does MiniMax-M3 hold up at font scale? (expect: no)
4. Is Gemini reasoning truly mandatory on OpenRouter?

**Decisions pre-committed:**
- If grid ties ≥2 models at 100%, grid is validation-only → adopt dense probes as primary instrument.
- Vision slot = highest dense-probe score with $/call ≤ $0.01 (cost is non-factor at ≤50 calls/day).

**Result:** Gemini 3.8 Flash (medium) won 14/15; Qwen3.8 Flash (low) chosen at user's
absolute-minimum-cost direction ($0.001/call, 12/15). Grid confirmed validation-only.
Full data: `GOLDEN-GRID-REPORT.md` §5–6, `aux_bench_vision_results.json`.

## Journal rules (standing)

1. Before any new benchmark: append a `## Pre-registration: <topic> (<date>)` block
   above the line — questions, decision criteria, BEFORE seeing data.
2. After the run: append one `## Run: <name> (<date>)` block — results file path,
   headline numbers, deviations from the pre-registered plan, and any claim that
   failed verification.
3. Model claims need the route template fields (route, model ID, reasoning mode,
   $ cache-miss/hit, tokens) — see vault page [[concept-agent-benchmark-route-template]].
4. Pre-flight: cheap 1-probe sanity check against known-good published/self numbers
   before any multi-model run. Log the probe result here.

## Standing pre-registration template (grid spec v4, committed 2026-09-16)

Every future aux-model benchmark pre-registers against this template (source:
`concepts/aa-benchmark-task-quality.md`, GOLDEN-GRID-REPORT §10):

1. **Stage 0 — AA mining (free, before any own runs):** longlist pruned by AA
   index ceiling + Omniscience non-halluc rate + speed metrics; per-model effort
   coverage checked (AA = capability ceiling at one effort, never an OFF/low curve).
   Never spend own runs on a model whose AA ceiling is below slot threshold.
2. **Stage 1 — degradation probes (own, cheap):** 1 probe × {OFF, low, production
   config} per surviving model at aux scale. Doubles as the pre-flight starvation
   check. Budget is NOT a grid dimension (9/16 finding: config artifact, 29.8% of
   golden-grid runs wasted on starvation empties).
3. **Stage 2 — construct grid (own, small):** review + vision-dense only (the two
   constructs no industry benchmark covers). Production config + uncapped. Rules:
   - ≥20 provenance-real snippets (own-repo reverted PRs), held-out split, disclosed curation.
   - Saturation rule pre-committed: pooled task mean >85% → calibration anchor, not discriminator.
   - Per-criterion blind judging: one planted bug per judge call, judge blind to model identity.
   - Executable verification where possible: bug location+class regex-checked vs key; LLM arbitrates residue.
   - Hallucination zero-credit gate pre-registered (AutomationBench style).
   - Cluster-bootstrap by snippet; select by CI lower bound or "top tier", never argmax over cells.
   - Fleiss' kappa reported; κ<~0.6 = ensemble unreliable. Judge panel refreshed + 30-run recalibration every grid.
   - Quarterly drift re-run of champion + judges on held-out split; trigger early on AA re-baselines.

## Run log

- 2026-09-09 | vision-slot | dense probes + MMMU-Pro | instruments: golden_grid.py,
  vision_probe{,2,3}.png + vision_gt*.json, aux_bench.py vision suite | outcome:
  qwen3.8-flash(low) adopted (user cost decision); gemini-3.8(med) accuracy ceiling.

## Lessons-implemented sweep (2026-09-10)

Pre-registered before acting: implement all unimplemented vault lessons or close them with evidence.

| Lesson | Action taken | Result |
|---|---|---|
| Pre-registration + journals | `BENCH-JOURNAL.md` created (this file) | DONE — standing rule: pre-register before runs |
| Pre-flight diagnostics | Added standing rule #4 in this file | process, applies next run |
| Memory routing index | `hermes/memory/INDEX.md` created; MEMORY.md points to it | DONE |
| Enforcement > instruction | `~/.hooks` no — `~/.hermes/hooks/paid-tier-guard.py` wired as pre_tool_call shell hook (allowlisted, 7/7 tests) | DONE — user-refined rule: block only when a free tier exists somewhere |
| Token overhead trim | prompt-size audit: 28.6KB system + 42KB tool schemas; ablation candidate (tts) offered, user timed out → no change | AUDITED, no trim (user call) |
| Skill ablation | 105 skills, 972KB read-cost, only 10.4KB always-on index — no ablation ROI found | CLOSED (index cost trivial) |
| Aggregator cache loss | Measured: OpenRouter passes through cached-input pricing on all daily drivers; DeepSeek direct no longer cheaper (OR v4.1-flash cached $0.003/M beats direct) | LESSON STALE for this stack — no config change |
| llama-swap slot affinity/prefetch | Verified: no mid-session swaps, 0 embed 503s in 24h, TTL unloads only; prefetch daemon = speculative infra → skipped | CLOSED (already satisfied) |
| Memory canary suite | 1 PASS (snapshot recall), 2 PASS (temp rejection), 4 PASS (FTS search), 6 PARTIAL: quick snapshot lacks MEMORY.md (confirmed live) but weekly full backup covers it | 6 executed, all green where applicable |

