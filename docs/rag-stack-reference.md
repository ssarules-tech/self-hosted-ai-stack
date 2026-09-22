# Local RAG Stack Reference

**Last updated:** 2026-08-24  
**Host:** Windows 11 / RTX 4070 Super / Ryzen 9 7900X3D / 31GB DDR5

---

## Overview

Self-hosted retrieval-augmented generation stack running entirely local. Documents indexed in LightRAG with graph-aware retrieval, embeddings and reranking via llama-swap, web extraction via Firecrawl, and peer memory via Honcho.

---

## Running Containers

| Container | Image | Port | Purpose |
|-----------|-------|------|---------|
| `llama-swap` | `ghcr.io/mostlygeek/llama-swap:unified-cuda` | 8090 | Model orchestration proxy |
| `lightrag` | `ghcr.io/hkuds/lightrag:latest` | 9621 | RAG engine + WebUI |
| `lightrag-postgres` | `pgvector/pgvector:pg18` | 127.0.0.1:5433 | Vector/graph storage |
| `firecrawl-api-1` | `firecrawl-api` | 3002 | URL extraction service |
| `searxng-core` | `searxng/searxng:latest` | 8080 | Metasearch backend |
| `honcho-api-1` | `honcho-api` | 127.0.0.1:8060 | Peer memory API |
| `couchdb-for-obsidian` | `couchdb:latest` | 127.0.0.1:5984 | Obsidian sync backend |

---

## Core Components

### LightRAG (RAG Engine)
- **Data volume:** `C:\Users\<user>\ai-stack\docker\lightrag\data`
  - `inputs/` — documents to index
  - `rag_storage/` — vector indexes and graph
  - `prompts/` — custom extraction prompts
- **Storage:** PostgreSQL 18 + pgvector with HNSW indexes
  - Entity table: `LIGHTRAG_VDB_ENTITY_bge_m3_embedding_1024d`
  - Relation table: `LIGHTRAG_VDB_RELATION_bge_m3_embedding_1024d`
  - Chunk table: `LIGHTRAG_VDB_CHUNKS_bge_m3_embedding_1024d`
- **MCP bridge:** `lightrag-mcp` via `uvx --with mcp==1.29.0`
  - Port: 9621
  - API key configured in Hermes config

**Retrieval modes:**
- `hybrid` — vector + BM25 keyword combined (default)
- `local` / `global` — graph-aware retrieval at different scopes
- `naive` — pure vector fallback

**Citation verification:** Use REST API directly (not MCP wrapper) for source chunks:
```bash
curl -X POST http://localhost:9621/query \
  -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  -d '{"query":"...", "mode":"hybrid", "include_references":true, "include_chunk_content":true}'
```

### llama-swap (Model Orchestrator)
- **Endpoint:** `http://127.0.0.1:8090/v1`
- **Config:** `~/ai-stack/docker/llama-swap/config.yaml`
- **Models served:**
  - `kat-coder-v2.5-35b-a3b-abliterated` (chat, 64K ctx)
  - `bge-m3-embedding` (embeddings, 1024-dim)
  - `bge-reranker-v2-m3` (reranking)
- **TTL:** 1800s idle timeout
- **Sleep-unload (2026-09-02):** `honcho-embed` uses `--sleep-idle-seconds 600`,
  `lightrag-rerank` uses 660 — Wafiq's advice. Model unloads from RAM after
  idle (offset so they never cycle together); ~5s cold reload on next ping.
  `/health` probes do not reset the idle timer. First request after sleep
  returns 503 "Loading model" — callers must retry.
- **Health check timeout:** 300s (cold load ~2m15s from Z:\ drive)

**Speculative decoding flags:**
```
--spec-type draft-mtp --spec-draft-n-max 1
--cache-type-k q8_0 --cache-type-v q8_0
--cache-type-k-draft q4_0 --cache-type-v-draft q4_0
```

### BGE-M3 Embeddings
- **Model:** `bge-m3-embedding`
- **Dimensions:** 1024
- **Binding:** OpenAI-compatible via llama-swap
- **Index type:** HNSW, cosine similarity

### BGE-Reranker-v2-M3
- **Protocol:** Cohere-shaped (llama.cpp `--reranking`)
- **Purpose:** Recombines vector + keyword results for precision lookup on dates, clause numbers, named parties

---

## Supporting Services

### Firecrawl (Web Extraction)
- API service + Playwright + FoundationDB + RabbitMQ + PostgreSQL + Redis
- Wired as Hermes `extract_backend`
- URL content extraction for document ingestion

### SearXNG (Search Backend)
- Self-hosted metasearch on port 8080
- Wired as Hermes `web.search_backend`
- No query leakage to single provider

### Honcho (Peer Memory)
- Deriver + API + Postgres15 + Redis
- Cross-session facts about people you work with
- Separate from LightRAG document storage

### CouchDB + Obsidian Sync
- CouchDB on port 5984 behind nginx (5986)
- Notes sync backend for Obsidian vault
- LightRAG can ingest from here if needed

---

## Configuration Highlights

### LightRAG `.env` (`ai-stack/docker/lightrag/.env`)
```bash
# LLM routing through llama-swap
LLM_BINDING=openai
LLM_BINDING_HOST=http://host.docker.internal:8090/v1
LLM_MODEL=kat-coder-v2.5-35b-a3b-abliterated
LLM_TIMEOUT=420

# Embeddings
EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=http://host.docker.internal:8090/v1
EMBEDDING_MODEL=bge-m3-embedding
EMBEDDING_DIM=1024
```

### Known Pitfalls
1. **MAX_PARALLEL_INSERT=1** — llama-swap hosts one model at a time; concurrent ingestion causes embedding timeouts
2. **PG18 volume mount** — must mount at `/var/lib/postgresql` (parent), not `/var/lib/postgresql/data`
3. **mcp==1.29.0 pin** — newer versions broke `lightrag-mcp` imports
4. **LLM_TIMEOUT=420** — cold load from Z:\ drive takes ~2m15s; default 240s is too short

---

## Data Flow

```
User Query
    │
    ▼
Hermes Agent ──MCP──▶ LightRAG ──REST──▶ Postgres/pgvector
    │                      │
    │                      ├───▶ BGE-M3 embeddings (via llama-swap)
    │                      ├───▶ BGE-Reranker (via llama-swap)
    │                      └───▶ KAT-Coder for generation (via llama-swap)
    │
    ├───▶ SearXNG (web search)
    ├───▶ Firecrawl (URL extraction)
    └───▶ Honcho (peer context)
```

---

## Quick Commands

```bash
# Check container status
docker ps --filter name=lightrag --filter name=llama-swap

# View LightRAG logs
docker logs lightrag --tail 50

# Query LightRAG directly
curl -X POST http://localhost:9621/query \
  -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  -d '{"query":"...", "mode":"hybrid"}'

# Check llama-swap running models
curl http://localhost:8090/running

# List LightRAG documents (via MCP)
hermes mcp call lightrag get_documents
```

---

## Related Skills
- `mlops/self-hosted-rag-architecture` — RAG design patterns and engine comparison
- `mlops/local-llm-serving` — llama-swap configuration and tuning
- `mlops/llama-cpp` — GGUF inspection and one-off inference
