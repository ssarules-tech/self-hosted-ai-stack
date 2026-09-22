# self-hosted-ai-stack

A production multi-model AI platform running locally on a single 12GB GPU (RTX 4070 Super), Windows 11 + WSL2, containerized with Docker and networked over Tailscale to a second always-on node.

## What's here

- **`scripts/`** — the model evaluation harness used to pick every model slot:
  - `golden_grid.py` — 1,085-iteration grid over model × reasoning-level × token-budget, with a 3-judge ensemble across four capability roles (review, compression, title/summary, vision)
  - `aux_bench.py` — the original ~600-run auxiliary-slot benchmark (wave 2), incl. synthetic vision tasks rendered with Pillow
  - `compression_quality_bench.py` — 20-planted-facts long-context compression test (~45k tokens)
  - `review_reasoning_bench.py` — reasoning-budget vs. quality benchmark with token-bucket rate limiting and parallel async judges
- **`docs/rag-stack-reference.md`** — the RAG stack layout: LightRAG + Qwen3-Embedding/Reranker with sleep-unload VRAM management
- **`docs/GOLDEN-GRID-REPORT.md`** — full methodology and findings from the golden grid
- **`docs/BENCH-JOURNAL.md`** — pre-registered benchmark journal (method before results)
- **`docker/`** — hardened compose templates: llama-swap, LightRAG, Honcho, SearXNG (secrets via env/files only)

## Design highlights

- **VRAM packing**: five+ GGUF models and embedding/reranker services fit in 12GB via staggered idle sleep-unload timers (600s embeddings, 660s rerankers)
- **Storage**: named ext4 volumes inside WSL2 (~1.6 GB/s) over Docker Desktop 9p bind mounts (~200 MB/s) — an 8x cold-load difference measured on real models
- **Cost engineering**: per-slot model selection driven by measured cost/quality tradeoffs, running the whole stack at $0/day on free-tier routes without quality loss
- **Reliability**: WSL2 GPU passthrough recovery, cron path-mangling fixes on Windows, cross-node memory sync with zero data loss

All benchmark scripts read API keys from environment/files — no secrets in the repo.

## Requirements

- Windows 11 + WSL2, Docker Desktop with NVIDIA GPU
- Python 3.11
- GGUF models (Qwen3 family) via llama.cpp / llama-swap
