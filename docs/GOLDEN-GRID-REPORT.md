# Golden Grid — Aux Model Testing: Methodology, Results & Breakdown

**Run:** Aug 29–30, 2026 · 2,512 graded runs (review + compression slots) + ~7,500 judge calls · total captured model cost **$1.79** (judge ensemble ~$15–20) · harness: `ai-stack/golden_grid.py` + `aux_bench.py`

---

## 1. Question

Which model + reasoning-config + token-budget should own each Hermes auxiliary slot (review, compression, title, helper, vision), measured on **real production workloads** with **cost as a first-class decision variable** — not academic benchmarks.

## 2. Design

Full-factorial grid, every cell = (model × reasoning-config × budget × scenario × trial):

| Factor | Values |
|---|---|
| Models (10) | glm, qwen37, qwen38, deepseek, gptoss, minimax-m3:free (`mm3free`), ling, solar, nex, mistral |
| Reasoning-configs | Per-model valid set: `default`, `{"enabled": false}`, `{"effort": "low"}`, `{"effort": "high"}`, `{"enabled": true}` — 400s on glm/gpt-oss if invalid form used; minimax ignores all toggles |
| Budgets (3) | max_tokens = 900 / 2000 / 4000 — a first-class variable, not fixed |
| Trials | 3 per cell, unique sessions |
| Slots | review (n=18/cell: 6 snippets × 3 trials), compression (n=12/cell: 4 × 3) |

**Total: 168 valid cells per slot × 2 slots = ~2,520 cells.** One command runs it; `--aggregate` produces `golden_grid_aggregated.json` (bootstrap CIs, 1000 resamples, per cell).

### Instrumentation
- **Checkpointed per call** (JSONL append+flush, done-keys dedupe) — the multi-hour grid was interrupted and resumed without loss; 39 retry rows carry `is_retry:true` alongside originals as an audit trail.
- Per-run capture: score, hallucination count, latency, reasoning-token count, cost (`usage.cost`), output length, raw output, **per-judge verdicts** (found/missed/hallucinated per judge).
- Tiered scheduling: paid models 6–12 parallel workers (measured 0.8% 429 rate, backoff-recovered), free tiers sequential.

## 3. Judging protocol

- **3-judge ensemble from distinct families**: deepseek-v4-flash (off), glm-5.3-flash (effort:low), qwen3.8-flash (off). Per-judge verdicts stored separately; consensus score + unanimity flag per run.
- **Judges run reasoning-OFF/low with ≥2500-token headroom** — a reasoning-ON judge (deepseek-ON) burned its entire budget on hidden thinking and silently graded all zeros. Fail-loudly rule added after.
- **The judge design itself was validated**: disagreement cases + random sample re-graded with a reasoning-ON judge → **92% within-1-unit agreement, mean delta −0.04, no ranking change**. OFF-judge design validated empirically.
- **Judge bias measured, not assumed**: glm self-grading its own family +4.8pp lenient; qwen38 judging qwen37 −12.6pp harsh. Consensus-of-3 absorbs one biased judge. Absolute planted-ground-truth scoring is immune to position bias (no pairwise needed).
- **Objective ground truth**: review snippets contain 3 planted bugs each (B1/B2/B3); scores are bug-recall against the known answer key — the judge reads responses and marks found/missed, but the target list is fixed. Compression scores against required compression properties.

## 4. Headline finding — the budget law

Reasoning-ON models consume `max_tokens` on hidden thinking **before** any visible output:

| Budget | qwen37-ON compression | deepseek-ON compression |
|---|---|---|
| 900 | 0% (full starvation) | 12% |
| 4000 | 83% | 91% |

Reasoning-ON needs ~4× the budget to stop starving — **and even then never beats the same model's OFF config** at a fraction of the cost. Reasoning-ON cells at b900 score 0.0% with unanimous judges (all tokens eaten, empty output). Compression + title: reasoning OFF, period. Review barely needs reasoning (glm ~200 thinking tokens).

Empty output = budget starvation until proven otherwise — never blame the model first. Retest at 3–4× budget + reasoning OFF.

## 5. Results

### 5a. Cross-cut analysis (computed from the raw 2,512-row clean.json)

**Per-snippet difficulty (review, all configs pooled):**

| Snippet | Mean bug-recall | Reading |
|---|---|---|
| shell_inject | 61.6% | easiest — classic injection bugs found widely |
| crypto_weak | 56.2% | |
| thread_iter | 48.6% | |
| fin_float | 42.1% | float/rounding logic hardest for small models |
| state_mut | 40.6% | subtle state-mutation bugs — hardest of the well-detected |
| async_gather | **14.2%** | **near-universally missed** — async race conditions are the differentiator almost no model catches |

Insight: most of the spread between models comes from fin_float + state_mut + async_gather. The async_gather specialists are all glm — its best cells hit 44–63% where the pooled field manages 14% (best non-glm: nex 44.4%, mm3free 44.4%). This is a large part of why glm owns the review leaderboard at b4000.

**Budget × model interaction (mean over configs) — the budget law at cell level:**

| Budget | glm | mm3free | ling | solar | deepseek | qwen37 | qwen38 | gptoss | nex |
|---|---|---|---|---|---|---|---|---|---|
| **review b900** | 53.3 | 77.8 | 32.2 | 43.4 | 37.2 | 22.5 | 24.3 | 49.8 | 21.9 |
| **review b2000** | 59.3 | 69.1 | 53.3 | 52.3 | 42.0 | 28.5 | 32.3 | 52.5 | 30.9 |
| **review b4000** | 84.0 | 68.5 | 54.5 | 58.4 | 46.1 | 32.4 | 44.6 | 51.2 | 48.1 |
| **compression b900** | 59.3 | 100.0 | 86.0 | 25.0 | 20.7 | 20.7 | 15.5 | 9.5 | 0.0 |
| **compression b2000** | 66.7 | 99.8 | 99.1 | 51.9 | 78.0 | 14.6 | 22.9 | 70.4 | 8.1 |
| **compression b4000** | 85.5 | 100.0 | 99.5 | 76.4 | 97.7 | 79.1 | 35.3 | 82.6 | 50.0 |

Two regimes visible:
- **Budget-dependent models** (glm, solar, deepseek, qwen37, nex): strongly scale with budget — glm review jumps 53→84 from b900→b4000. Their b900 numbers are starvation, not capability.
- **Budget-flat models** (mm3free flat ~100/68-78, gptoss flat ~50, qwen38 compression flat ~35): performance is config-bound, not budget-bound. mm3free's review score actually *drops* slightly with more budget (69→68 with more room to over-produce hallucinations — its halluc rate rises 1.09→1.17).
- **qwen38 compression is config-capped**: 0% at every ON config across all budgets, best 35.3 pooled even at b4000 — it's the model most broken by reasoning modes, not budget.

**Budget effect pooled (all models/configs):** review 36.7% → 43.8% → 51.1% (b900→b2000→b4000); compression 32.4% → 52.5% → 75.8%. Budget is the single largest quality lever in the grid — bigger than model choice within the free tier.

**Per-model pooled (all configs, all budgets):**

| Slot | Model | Pooled mean | n | Total cost | Latency |
|---|---|---|---|---|---|
| review | mm3free | 71.8% | 54 | $0.000 | 7.9s |
| review | glm | 65.5% | 162 | $0.037 | 22.8s |
| review | solar | 51.4% | 162 | $0.023 | 44.3s |
| review | gptoss | 51.2% | 162 | $0.023 | 10.7s |
| review | ling | 46.7% | 162 | $0.015 | 7.5s |
| review | deepseek | 41.8% | 216 | $0.046 | 30.9s |
| review | qwen38 | 33.7% | 216 | $0.141 | 26.3s |
| review | nex | 33.6% | 108 | $0.017 | 9.3s |
| review | mistral | 30.2% | 49 | $0.001 | 12.7s |
| review | qwen37 | 27.8% | 216 | $0.045 | 11.7s |
| compression | mm3free | 99.9% | 36 | $0.000 | 10.7s |
| compression | ling | 94.9% | 108 | $0.029 | 4.8s |
| compression | glm | 70.5% | 108 | $0.177 | 23.7s |
| compression | deepseek | 65.5% | 144 | $0.214 | 22.4s |
| compression | gptoss | 54.2% | 108 | $0.183 | 39.4s |
| compression | solar | 51.1% | 108 | $0.085 | 47.9s |
| compression | qwen37 | 38.1% | 144 | $0.378 | 10.7s |
| compression | mistral | 36.1% | 33 | $0.046 | 33.6s |
| compression | qwen38 | 24.6% | 144 | $0.250 | 32.1s |
| compression | nex | 19.4% | 72 | $0.083 | 14.9s |

(Caveat: pooled means mix starvation cells with healthy cells — the per-cell tables above are the fair comparison; pooled is for cost-efficiency and robustness-at-a-glance. mm3free's 71.8% review mean is the most cost-robust profile in the grid: zero dollars, zero 429s, top-3 at every budget.)

**Run health:** 748/2,512 runs produced empty output (29.8%) — overwhelmingly reasoning-ON cells at b900/b2000, i.e. the starvation effect measured directly at scale. **Important clarification: empties were NOT retried until they produced output — by design.** The harness's retry logic covers HTTP 429/5xx only (`golden_grid.py` chat(): retries on 429/500/502/503, verified in source). An empty output with a 200 status is a *successful API call* — the model spent the whole budget on hidden reasoning and legitimately produced nothing. Re-running it until text appeared would erase the finding: the 0.0% starvation scores in the compression table ARE the measurement. Confirmed in the raw JSONL: 2,567 raw rows → 2,520 cell keys, 744 keys are empty-only, and clean.json keeps all 748 empty rows as first-class data (empty rate + answered-only scores in their methodology). Only 39 `is_retry` rows exist, all HTTP-failure retries — 4 of which were still empty and 26 of which duplicate existing keys (kept as audit trail, deduped at aggregation). 39 retry rows, all retained with audit flags. 429 recovery worked (paid models completed at 6–12 workers).

### 5b. Review (planted-bug recall, 18 runs/cell, 95% bootstrap CIs)

| Rank | Config | Score | CI | Latency | Cost/run | Halluc/trial | Notes |
|---|---|---|---|---|---|---|---|
| 1 | **glm effort-low b4000** | **84.0%** | [74–93] | 8.8s | $0.07 | 0.61 | **pick** — ties effort-high & default at b4000, 7× cheaper than default |
| 1 | glm effort-high b4000 | 84.0% | [75–92] | 14.4s | $0.14 | 0.74 | effort-high bought nothing |
| 1 | glm default b4000 | 84.0% | [77–91] | 64.0s | $0.68 | 0.78 | same score, slowest + priciest |
| 4 | glm effort-high b2000 | 81.5% | [73–90] | 16.5s | $0.16 | 0.75 | |
| 5 | **mm3free default b900** | **77.8%** | [67–86] | 4.8s | $0.00 | 1.09 | free fallback; highest hallucination rate of top tier |
| 6 | glm effort-low b900 | 73.5% | [61–85] | 7.2s | $0.07 | 0.72 | cheapest near-tier entry |
| — | solar effort-high b4000 | 66.0% | [55–77] | 52.4s | | 0.37 | best of the rest |
| — | ling effort-high b4000 | 61.7% | [51–72] | 12.1s | | 0.30 | |
| — | deepseek-ON-low b4000 | 61.1% | [48–76] | 31.7s | | 0.31 | |
| — | qwen37-OFF b900 | 50.6% | [40–61] | 1.0s | | 0.30 | |
| — | qwen38 best cell | 50.3% | [38–61] | 50.1s | | 0.19 | judge-only, outclassed everywhere |
| — | gptoss best cell | 53.7% | [40–67] | | | | mid |
| — | nex best cell | 54.3% | [41–69] | | | | mid |
| ✗ | mistral default b900 | 34.6% | [24–45] | 8.6s | | **1.78** | **disqualified** — 1.47–1.78 hallucinations/trial |
| ✗ | gemma-free | — | | | | | 429s throughout |

** glm self-grade +4.8pp lenient bias is real but small; discounted, it still ranks #1.**

### Compression ( unanimity was the story)

| Rank | Config | Score | CI | Latency | Cost/run | Unanimity |
|---|---|---|---|---|---|---|
| 1 | **mm3free default b900** | **100%** | [100–100] | 8.6s | $0.00 | 100% | **pick** — free, unanimous |
| 1 | mm3free default b4000 | 100% | [100–100] | 7.0s | $0.00 | 100% | |
| 1 | ling effort-low b4000 | 100% | [100–100] | 5.2s | $0.003 | 100% | |
| 1 | glm effort-low b900/2000 | 100% | [100–100] | ~15s | ~$0.02 | 100% | |
| 1 | deepseek-OFF b4000 | 100% | [100–100] | 58.2s | | 100% | |
| 2 | qwen37-OFF b4000 | 99.8% | [99–100] | 8.6s | | 92% | |
| — | glm default b900/2000 | **0.0%** | [0–0] | | | 100% | **full starvation** — reasoning burns the entire budget |
| — | qwen37/qwen38-ON b900 | **0.0%** | [0–0] | | | 100% | same starvation |
| — | qwen37-OFF b2000 | 58.3% | [33–83] | 8.7s | | 100% | **bimodal** — degenerate PR-cataloging mode at tight budgets |

### Title / Helper / Vision (per-slot runs, `aux_bench.py`, prior sweep — carried forward)

| Slot | Pick | Score | Notes |
|---|---|---|---|
| Title | qwen37-OFF b900 | 92% @ 0.6s | ties glm-low 92%; qwen38 90% at 5× cost |
| Helper family (skills_hub/approval/mcp/curator) | glm-high 76% > nex/minimax 74% > ling 72% | | qwen37-OFF 62–68% but 3–6× cheaper → picked for volume |
| Vision | qwen38-LOW (switched 9/9) | | was qwen37-OFF; dense-text smoke 12/15 vs 5–9/15, MMMU-Pro 0.798, ~$0.30/mo @10 calls/day |
| KAT-Coder | 48% review @76s, 500s on >32k ctx | | not viable for aux |

## 6. Final applied config (user decision, 8/30 evening — cost-first, supersedes raw 'pick')

Live-verified via the Hermes aux client after application:

| Slot | Applied | Rationale |
|---|---|---|
| Review | glm-5.3-flash **default reasoning** (effort-high removed) | user kept default over effort-low pick; effort-high demonstrably bought nothing |
| Compression | **minimax-m3:free b900** | 100% unanimous, free, 9s — over glm-low |
| Helper family | **minimax-m3:free** | user accepted the 74%-vs-76% gap for cost |
| Title | qwen37-OFF | unchanged |
| Vision | **qwen38-LOW** (9/9: user cost-minimal decision after dense-text + AA MMMU-Pro analysis; qwen37 unstable 5–9/15 on 7–13px probes; Gemini3.8-med accuracy pick declined at 8× cost; minimax-m3:free deprecated on OR) | applied + live-verified |
| Vision (prev) | qwen37-OFF | superseded |
| Global fallback | `auxiliary.openrouter_model = minimax/minimax-m3:free`, `free_only: true` | free-tier never takes always-on quality slots; fallback only |

Note: **free-tier models never take always-on quality slots** (reliability-first standing rule) — recorded as fallbacks.

## 7. Validity & known limits

- **Total spend $1.79 measured across 2,512 runs** — cost captured from provider `usage.cost` per request, not estimated. Judge ensemble adds ~$15–20.
- 3 trials/cell + bootstrap CIs: differences inside overlapping CIs are ties (e.g. three glm review configs at 84% are statistically one result — the tiebreaker was cost, not score).
- Retry/audit trail: 39 retry rows retained alongside originals; mixed single/multi-judge rows archived before resume (done-keys would otherwise skip never-ensemble-judged cells).
- **Known ceiling** (`ponytail:` note): clean.json has 1,005 rows carrying a `doc` field instead of `snippet` (schema drift mid-grid between slot formats); aggregation dedupes by cell key so rankings are unaffected, but scenario-level slicing of those rows requires the doc-key variant.
- Judge-validation and self-bias measured (§3) — the protocol was tested, not assumed.

## 8. Harness rules learned (operational)

1. Sweep the **full** grid when asked — never pre-scope to "likely finalists."
2. Checkpoint every call; grids WILL be interrupted.
3. **Validate one job end-to-end before launch** — lambda-arg bugs, regex breaks, and label renames (`qwen` vs `qwen37`) silently corrupt grids.
4. Judge = reasoning-OFF/low with ≥2500-token headroom.
5. Validate the judge design itself before trusting it.
6. Per-model reasoning-config compatibility differs (400s / ignored / absent) — build the compatibility matrix first.
7. Measure 429 rate empirically before throttling workers.
8. Fail loudly on judge JSON-parse failures — silent all-zero fallbacks mask real failures.
9. Hermes config keys may be display-masked; resolve real keys via container env, never print.
10. Record the user's cost-first decisions alongside the 'optimal' picks — never silently overwrite a user decision with the 'optimal' one.

---

*Artifacts: `ai-stack/golden_grid.py`, `aux_bench.py`, `golden_grid_results.jsonl` (2,512 rows + retries), `golden_grid_clean.json`, `golden_grid_aggregated.json` (bootstrap CIs per cell), `golden_grid_legacy_singlejudge.jsonl` (pre-ensemble archive), `golden_grid_results_corrupt_backup.jsonl` (corrupt-run backup).*

## 9. Newmodels extension (Sept 9–12, 2026) — review slot: ds41 / glm / qwen3.8 / agnes

`golden_grid_newmodels.py` (ai-stack) · results: `golden_grid_newmodels_results.jsonl` (450 rows, 446 clean) · same 6 review snippets × 3 trials, same 3-judge ensemble · budgets 900/2000/4000 + uncapped 16000. Agnes billed $0 (promo) throughout; token backfill: agnes tok_in measured exact (141–184), tok_out = rtok + visible est (~4 ch/tok, flagged `tok_out_estimated`).

### 9a. All cells (score / hall-trial / latency / $-per-run)

| Arm | b900 | b2000 | b4000 | b16000 (uncapped) |
|---|---|---|---|---|
| ds41-off | 77% / .87 / 11s / $.0003 | **88%** / 1.11 / 10s / $.00035 | 77% / .83 / 8s / $.0004 | — |
| ds41-low | 15% / starved 18/18 | 19% / starved 15/18 | 54% / .26 / 30s / $.0029 | 75% / .41 / 88s / $.0030 |
| ds41-high | 13% / starved 18/18 | 26% / starved 17/18 | 38% / starved 10/18 | 60% / .39 / 78s / $.0039 |
| ds41-max | 12% / starved 18/18 | 19% / starved 17/18 | 27% / starved 14/18 | 55% / .35 / 213s / $.0059 |
| glm-default | — (84% @ b4000, original grid) | — | 84% / CI[75–92] / 64s / $.0007 | **89%** / .54 / 38s / $.0012 |
| qwen38-on/low/high | — | — | — | 64/69/**75%** / .41 / 54–71s / $.0014–.0017 |
| agnes-off | 56% / .35 / 14s | 57% / .31 / 18s | 65% / .30 / 12s | — |
| agnes-on | 20% / starved 14/18 | 71% / .50 / 13s | 76% / .41 / 14s | — |

### 9b. Uncapped verdicts (b16000)
- **Budget law holds even at 16k**: ds41 reasoning arms (55–75%) never recover past their own OFF arm (88%). Their deficit is capability, not starvation. All DeepSeek ON configs dominated — permanently excluded from review routing.
- **glm-default uncapped = new champion: 89%** (hall .54, 38s, $.0012/run). Uncapped bought it +5pp over b4000 (84%→89%) — the only arm where extra budget measurably paid. Per-snippet: crypto_weak & state_mut 100%, shell_inject 93%, fin_float/thread_iter/async_gather 78–81%.
- qwen3.8 plateau: high-effort 75% uncapped = its ceiling; no budget helps.
- 4 ds cells lost to connection errors (2×WinError 10060, 2×read timeout) — flagged in log, not silently scored.

### 9c. Agnes 3.0 Flash (promo, $0)
- Review ceiling **76% (agnes-on b4000)** — CI [66–84] sits below glm (84–89) and ds41-off (88). Not competitive for review.
- Hallucination 0.30–0.50/trial — **more honest than ds41-off** (.83–1.11), less capable.
- Weak bug classes: state_mut (72%) and async_gather (52%) — misses mutation/concurrency bugs; crypto/shell fine.
- **Thinking is default-ON and must be explicitly disabled for cheap slots** (b900 starves 14/18 → 20%).
- No video modality (text+image only, per Agnes docs + AA) → excluded from vision candidate pool per user criterion. No further slot runs.
- Verdict: optional free fallback lane only (Anthropic-compatible endpoint + 1M ctx are its real differentiators). Main-chat switch to Agnes: rejected — glm measured-better and equally free.

### 9d. Cost-efficiency, applicable aux slots (final)

| Slot | Pick (status quo or change) | $/run measured | $/mo @10–50 calls/day | Basis |
|---|---|---|---|---|
| Review | **glm-default** — consider b16000/uncapped budget (+5pp for ~$.0005/run more) | $.0007–.0012 | ~$0.3–1.8 | b16000 89% vs b4000 84%; ds41-off 88% @ 10s/$.00035 remains the fast-cheap tier |
| Review (alt) | ds41-off b2000 | $.00035 | ~$0.1–0.5 | 88%, 9.7s; blind spot async_gather 37%; hall 2.4× glm's |
| Compression | mm3free b900 | $0 | $0 | 100% unanimous — uncapped adds nothing (outputs ~200 tok) |
| Title | qwen37-OFF b900 | ~$0 | ~$0 | 92% @ 0.6s; budget-flat |
| Helper family | mm3free | $0 | $0 | 74% vs glm-high 76% accepted by user for cost; outputs never near cap |
| Vision | qwen38-LOW (9/9) | $.001/call | ~$0.3–1.5 | unchanged this sweep |

**Slot-level conclusions:** Only review rewards token budget; every other slot is budget-flat because reasoning is OFF and outputs are short — uncapped measurements there would be pure waste (verified: OFF-arm quality is budget-flat across b900/2000/4000 in both grids). Cost remains negligible everywhere; the review decision is accuracy-vs-hallucination, not money: glm uncapped (89%, hall .54) vs ds41-off (88%, hall 1.11, 4× faster, 3.4× cheaper) — statistically tied, so latency/cost tiebreak favors ds41-off for interactive use, glm-uncapped for quality-critical batch review.

## 10. Critical self-analysis + AA methodology deep dive (2026-09-16)

Post-hoc critique of this grid against the task-construction standards of the 10 evaluations informing the Artificial Analysis Intelligence Index v4.3 (decomposition: `raw/articles/aa-methodology-decomposition-2026-09-16.md` in the Library vault; full lessons: `concepts/aa-benchmark-task-quality.md`).

### 10a. What this grid got right
- Planted-GT checklist scoring = the strongest instrument in the vault's own instrument ladder (checklist > pairwise > absolute) — matches AA's preference for objective/execution scoring (5/10 index evals need no judge).
- Judged the judge (92% within-1 re-grade), measured family bias, CIs, honest ties, per-run measured cost, checkpointing, 429-rate before throttling.

### 10b. What was wrong
1. **Budget as a grid factor** (finding 9/16): `max_tokens` is a config artifact, not a model property. It tripled the grid and 748/2,512 runs (29.8%) were starvation empties that existed only because of the axis. Starvation is a *pre-flight diagnostic*, not a measured dimension. Supersedes §9's framing; supersedes the skill's "sweep budget as a grid dimension" rule.
2. **Winner's curse**: applied picks were argmax over ~168 cells/slot with ~±9pp CIs — picks are inflated by multiple comparisons. Rule going forward: select by CI lower bound or "top tier", tiebreak on measured cost/latency.
3. **Corpus too small & clustered**: 6 snippets carry the ranking, spread concentrated in 3 of them; bootstrap resampled runs (n=18) as independent when they are 6 tasks × 3 trials. AA buys ±1% with hundreds–thousands of tasks; 6 items can never get there. Going forward: cluster-bootstrap by snippet.
4. **Task saturation ignored**: compression top models 100% unanimous; shell_inject 61% ceiling-ish. AA retires saturated evals (MMLU-Pro/AIME/LiveCodeBench out in v4.0). **Compression slot: demoted to probe-only, no grid ever again.** shell_inject demoted to calibration anchor.
5. **Judge leading risk**: judge sees the full 3-bug key at once. AA's GDP.pdf grades one criterion per judge call, blind to model identity. Adopt per-criterion blind judging.
6. **No agreement metric**: per-judge verdicts stored but Fleiss' kappa never computed (pipeline step 4 existed, output missing). Report kappa every grid.
7. **No drift protocol**: models behind stable IDs change; no scheduled re-run. Quarterly champion+judge re-run on a held-out split; trigger early when AA re-baselines a model in use.
8. **Snippet provenance undocumented**: authored once by the benchmarked operator, no selection criteria disclosed, no leakage immunity. Adopt own-repo reverted PRs as provenance-real review targets.

### 10c. Division of labor vs AA (standing protocol, committed to skill + journal)
- **AA mining (free)**: longlist pruning by index ceiling + Omniscience non-halluc + speed; effort-coverage check per model page (index = ceiling; no OFF/low data).
- **Degradation probes (own, cheap)**: reasoning/effort interaction at aux scale — the question AA structurally cannot answer (its cost/quality regime also inverts at 1–2k token calls).
- **Construct grid (own, small)**: only review + vision dense probes (the constructs no industry benchmark covers), production config + uncapped, corpus v3 rules, CI-lower-bound selection.
- Compression/title/helper: probe + AA-prior + live-config validation only.

### 10d. Task-quality grading vs AA construction standards
| Dimension | AA | This grid | Verdict |
|---|---|---|---|
| Provenance/realism | real artifacts (Slack exports, 4.6k PDF pages, real SaaS APIs) | hand-authored synthetic snippets | weakest dimension |
| Judge blindness | one criterion/call, blind to identity+source | full key at once | transferable gap |
| Saturation handling | retires saturated evals | kept saturated tasks | dead weight |
| Curation-bias disclosure | HLE bias disclosed | undocumented | unacknowledged |
| Guardrail/zero-credit | AutomationBench pre-committed | post-hoc mistral DQ | rule in spirit |
| Executable GT | 5/10 evals judge-free | judge-verified recall | one rung lower (location/class regex-verifiable) |
