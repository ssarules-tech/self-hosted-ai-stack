"""
Compression quality benchmark: does reasoning level affect summarization quality?
Target models x reasoning variants, graded by qwen3.7-flash (reasoning OFF) against
planted facts in a long synthetic document. 2 trials per (model, variant).
"""
import json, time, urllib.request, urllib.error, concurrent.futures, sys, os, re

KEY = re.search(r"^OPENROUTER_API_KEY=(.+)$",
    open(r"C:/Users/Shehryar/AppData/Local/hermes/.env").read(), re.M).group(1).strip()
URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE = "qwen/qwen3.7-flash"

# ---- Document with 20 planted facts scattered across ~45k tokens ----
FACTS = [
    ("F1",  "memory budget on embedding node must stay under 23GB"),
    ("F2",  "decision to migrate shard 7 to the Frankfurt region in Q4"),
    ("F3",  "PR #1042 must be reviewed by owner-3 before Friday"),
    ("F4",  "latency SLO for search is p99 under 250ms"),
    ("F5",  "the reranker will be swapped to a 0.6B model in September"),
    ("F6",  "cache TTL was reduced from 15 minutes to 5 minutes"),
    ("F7",  "owner-5 raised a concern about silent failover timeouts"),
    ("F8",  "budget approved for 2 additional GPU nodes in October"),
    ("F9",  "decision to keep bge-m3 until the Qwen3 migration completes"),
    ("F10", "the watchdog cron was deliberately NOT deployed this quarter"),
    ("F11", "shard 3 has a replica lag exceeding 90 seconds"),
    ("F12", "the team agreed to cap concurrent backfills at 8"),
    ("F13", "postgres connection pool ceiling raised to 400"),
    ("F14", "decision to disable verbose logging in production by Friday"),
    ("F15", "the next architecture review is scheduled for the 12th"),
    ("F16", "owner-2 owns the decommissioning of the old embed container"),
    ("F17", "a 429 backoff of exponential form was added to the client"),
    ("F18", "the Flash variant is preferred over the non-Flash for cost"),
    ("F19", "stress test target is 600 requests with zero failures"),
    ("F20", "the security audit is booked for the first week of November"),
]
import random
random.seed(42)
paras = []
for i in range(520):
    p = (f"Section {i}: The team discussed implementation details of component {i%17}, "
         f"including the tradeoff between latency and throughput on node {(i*3)%9}, "
         f"a decision to {'keep' if i%3 else 'revisit'} the caching strategy for shard {i%11}, "
         f"and an action item assigned to owner-{i%7} to review PR #{1000+i} before the {['Mon','Tue','Wed','Thu','Fri'][i%5]} sync. "
         f"Notable constraint: memory budget on the embedding node must stay under {26-(i%5)}GB.")
    paras.append(p)
# inject facts at spread positions
for k, (fid, fact) in enumerate(FACTS):
    paras[(k * 26) % len(paras)] += f" Additionally, it was recorded that {fact}."
DOC = "\n".join(paras)

FACT_LIST = "\n".join(f"{fid}: {f}" for fid, f in FACTS)
PROMPT = ("Compress the following document into a dense summary under 400 words, "
          "preserving all key decisions, constraints, numbers, owners, and action items. "
          "Output only the summary.\n\n" + DOC)

def chat(model, messages, maxtok, reasoning=None, retries=4):
    body = {"model": model, "messages": messages, "max_tokens": maxtok}
    if reasoning is not None: body["reasoning"] = reasoning
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    for a in range(retries):
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=240).read())
            return d["choices"][0]["message"].get("content") or "", d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503) and a < retries-1:
                time.sleep(3 * 2**a); continue
            raise

def judge(summary):
    j = (f"You grade a summary against facts that were planted in the source document.\n\n"
         f"PLANTED FACTS:\n{FACT_LIST}\n\nSUMMARY:\n{summary[:6000]}\n\n"
         f"For each fact, judge if the summary preserves the SPECIFIC information (numbers, owners, "
         f"decisions — paraphrase OK). Reply ONLY JSON: "
         f'{{"preserved": ["F1","F3"], "lost": ["F2"]}} — every fact must appear in exactly one list.')
    out, _ = chat(JUDGE, [{"role":"user","content":j}], 2000, reasoning={"enabled": False})
    if not out.strip(): raise RuntimeError("judge empty")
    js = out[out.index("{"):out.rindex("}")+1]
    return json.loads(js)

RUNS = [
    ("glm5.3flash-high",  "z-ai/glm-5.3-flash", {"effort": "high"}),
    ("deepseek-ON",       "deepseek/deepseek-v4-flash", {"enabled": True}),
    ("qwen3.7-flash-OFF", "qwen/qwen3.7-flash", {"enabled": False}),
]

def run_one(name, model, reasoning, trial):
    t0 = time.time()
    try:
        summary, usage = chat(model, [{"role":"user","content":PROMPT}], 900, reasoning=reasoning)
    except Exception as e:
        return {"run": name, "trial": trial, "error": str(e)[:80]}
    lat = round(time.time()-t0, 1)
    g = judge(summary)
    preserved = len(g.get("preserved", []))
    return {"run": name, "trial": trial, "latency": lat,
            "preserved": preserved, "total": len(FACTS),
            "lost": g.get("lost", []),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "cost": usage.get("cost", 0),
            "score": preserved / len(FACTS)}

if __name__ == "__main__":
    jobs = [(n, m, r, t) for n, m, r in RUNS for t in (1, 2, 3)]
    print(f"running {len(jobs)} compression jobs (+judge each)...", flush=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        for r in ex.map(lambda j: run_one(*j), jobs):
            results.append(r)
            if "error" in r:
                print(f"[{r['run']:>18}] t{r['trial']} ERR {r['error']}", flush=True)
            else:
                print(f"[{r['run']:>18}] t{r['trial']} preserved={r['preserved']}/{r['total']} "
                      f"{r['latency']}s rtok={r['reasoning_tokens']} cost=${r['cost']:.5f} lost={r['lost'][:4]}", flush=True)
    json.dump(results, open(r"C:/Users/Shehryar/ai-stack/compression_quality_results.json", "w"), indent=1)
    print("\n=== SUMMARY (avg over 2 trials, 20 planted facts) ===")
    for n, m, r in RUNS:
        rs = [x for x in results if x["run"] == n and "error" not in x]
        if not rs: continue
        sc = sum(x["score"] for x in rs)/len(rs)
        lat = sum(x["latency"] for x in rs)/len(rs)
        cost = sum(x["cost"] for x in rs)
        rt = sum(x["reasoning_tokens"] for x in rs)/len(rs)
        print(f"{n:>18}: fact-preservation={sc:.0%}  latency={lat:.1f}s  reasoning_tok={rt:.0f}  total_cost=${cost:.4f}")
