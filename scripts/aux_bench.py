"""
AUX MODEL BENCHMARK FRAMEWORK
Per-slot scenario suites, planted ground truth, judged by qwen3.7-flash (reasoning OFF).
Usage: python aux_bench.py <slot>   (slot = review|compression|title|helper|vision)
Results: aux_bench_<slot>_results.json
"""
import json, time, base64, io, urllib.request, urllib.error, concurrent.futures, sys, os, re, random

KEY = re.search(r"^OPENROUTER_API_KEY=(.+)$",
    open(r"C:/Users/Shehryar/AppData/Local/hermes/.env").read(), re.M).group(1).strip()
URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE = "qwen/qwen3.7-flash"
SLOT = sys.argv[1] if len(sys.argv) > 1 else "review"
TRIALS = int(os.environ.get("BENCH_TRIALS", "3"))

KAT_KEY = open(r"C:/Users/Shehryar/ai-stack/.kat_key").read().strip()
def chat(model, messages, maxtok, reasoning=None, retries=5, image_b64=None):
    local = model.startswith("kat-")
    base = "http://127.0.0.1:8090/v1/chat/completions" if local else URL
    auth = KAT_KEY if local else KEY
    body = {"model": model, "messages": messages, "max_tokens": maxtok}
    if reasoning is not None and not local: body["reasoning"] = reasoning
    req = urllib.request.Request(base, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"})
    for a in range(retries):
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=300).read())
            return d["choices"][0]["message"].get("content") or "", d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503) and a < retries-1:
                time.sleep(3 * 2**a); continue
            raise

def judge_json(prompt, maxtok=2500):
    out, _ = chat(JUDGE, [{"role":"user","content":prompt}], maxtok, reasoning={"enabled": False})
    if not out.strip(): raise RuntimeError("judge empty")
    js = out[out.index("{"):out.rindex("}")+1]
    return json.loads(js)

# ============================ REVIEW ============================
REVIEW_SNIPPETS = [
{"id":"fin_float","code":'''def process_payment(order, amount):
    if amount > order.total:
        raise ValueError("exceeds total")
    order.paid += amount
    if order.paid == order.total:
        order.status = "PAID"
    return order.paid''',
 "bugs":["B1: float equality `order.paid == order.total` never triggers reliably with binary floats — paid status silently never set (or set on wrong accumulation); money must use Decimal and tolerance comparison",
        "B2: no check that amount is positive — negative amounts DECREASE paid and can corrupt state",
        "B3: repeated partial payments allowed after status PAID with no guard — double-pay acceptance"]},
{"id":"thread_iter","code":'''import threading

results = []
def worker(items):
    for x in items:
        results.append(transform(x))

def run_all(batches):
    threads = [threading.Thread(target=worker, args=(b,)) for b in batches]
    for t in threads: t.start()
    return results''',
 "bugs":["B1: main thread returns `results` without joining threads — returns incomplete/partial list (missing join)",
        "B2: shared list append from multiple threads without lock — while CPython GIL makes append atomic-ish, interleaving order is nondeterministic and this is a latent race (documented anti-pattern)",
        "B3: no exception handling in worker — a transform() exception kills the thread silently, results missing with no error surfaced"]},
{"id":"shell_inject","code":'''import subprocess

def backup(db_name, out_dir):
    cmd = f"pg_dump {db_name} > {out_dir}/backup.sql"
    subprocess.run(cmd, shell=True, capture_output=True)
    return True''',
 "bugs":["B1: shell=True with f-string interpolation — command injection via db_name/out_dir (e.g. `db; rm -rf /`)",
        "B2: return True unconditionally — subprocess failure is invisible (no returncode check)",
        "B3: no error output surfaced: capture_output=True swallows stderr, failures are silent"]},
{"id":"state_mut","code":'''def add_tag(record, tags=[]):
    tags.append(record.id)
    record.tags = tags
    return record''',
 "bugs":["B1: mutable default argument `tags=[]` — shared across all calls without an explicit list, cross-record tag contamination",
        "B2: record.tags aliases the same list object as the default — mutating one mutates all records sharing it",
        "B3: no copy on assignment — even when caller passes a list, record and caller's list alias each other"]},
{"id":"async_gather","code":'''import asyncio

async def fetch_all(urls):
    tasks = [asyncio.create_task(fetch(u)) for u in urls]
    return await asyncio.gather(*tasks)''',
 "bugs":["B1: no return_exceptions=True — a single failed fetch cancels/raises the whole gather, losing all completed results",
        "B2: unbounded task creation — thousands of URLs create thousands of simultaneous connections (no semaphore)",
        "B3: no timeout on gather — one hung connection blocks forever (no asyncio.wait_for)"]},
{"id":"crypto_weak","code":'''import hashlib, random

def make_token(user_id):
    salt = str(random.randint(100000, 999999))
    raw = f"{user_id}:{salt}"
    return hashlib.md5(raw.encode()).hexdigest()''',
 "bugs":["B1: MD5 for security tokens — cryptographically broken, must use SHA-256+ or HMAC",
        "B2: random (Mersenne Twister) not secrets module — predictable salt, tokens guessable",
        "B3: 6-digit salt has only 900k entropy AND is included in the hashed string verbatim — trivially brute-forceable per user"]},
]

REVIEW_MODELS = [
    ("ling-flash", "inclusionai/ling-3.0-flash", None),
    ("mistral-nemo", "mistralai/mistral-nemo", None),
    ("nex-n2-mini", "nex-agi/nex-n2-mini", None),
    ("solar-pro4", "upstage/solar-pro4", None),
    ("gemma-4-31b", "google/gemma-4-31b-it:free", None),
    ("minimax-m3-free", "minimax/minimax-m3:free", None),
    ("minimax-m2.7-free", "minimax/minimax-m2.7:free", None),
    ("kat-local", "kat-coder-v2.5-35b-a3b-abliterated", None),
    ("qwen-ON-default", "qwen/qwen3.7-flash", {"enabled": True}),
    ("qwen-ON-low",     "qwen/qwen3.7-flash", {"enabled": True, "effort": "low"}),
    ("qwen-ON-high",    "qwen/qwen3.7-flash", {"enabled": True, "effort": "high"}),
    ("glm-high",        "z-ai/glm-5.3-flash", {"effort": "high"}),
    ("gptoss-high",     "openai/gpt-oss-120b", {"effort": "high"}),
]

REVIEW_PROMPT = """Review this Python code for bugs. List ONLY genuine bugs with severity (Critical/High/Medium/Low) and a one-line explanation each. Be precise about the root cause.

```python
{code}
```"""

# ============================ COMPRESSION ============================
COMP_FACTS = [
    ("F1","migration deadline moved to October 15"),
    ("F2","shard 5 replica lag exceeds 120 seconds"),
    ("F3","connection pool ceiling raised to 400"),
    ("F4","owner-6 owns the old container decommission"),
    ("F5","budget approved for 3 GPU nodes in Q4"),
    ("F6","watchdog cron deliberately not deployed"),
    ("F7","cache TTL cut from 15 minutes to 5"),
    ("F8","backfill concurrency capped at 8"),
    ("F9","p99 latency SLO is 250ms for search"),
    ("F10","security audit booked first week of November"),
    ("F11","reranker swap planned for September"),
    ("F12","429 exponential backoff added to client"),
]
def _build_comp_doc(seed):
    random.seed(seed)
    paras = []
    for i in range(420):
        paras.append(f"Section {i}: The team discussed implementation details of component {i%19}, "
            f"including the tradeoff between latency and throughput on node {(i*3)%11}, "
            f"a decision to {'keep' if i%3 else 'revisit'} the caching strategy for shard {i%13}, "
            f"and an action item assigned to owner-{i%8} to review PR #{2000+i} before the {['Mon','Tue','Wed','Thu','Fri'][i%5]} sync. "
            f"Notable constraint: memory budget on the embedding node must stay under {24-(i%4)}GB.")
    for k, (fid, fact) in enumerate(COMP_FACTS):
        paras[(k * 33 + seed) % len(paras)] += f" Additionally, it was recorded that {fact}."
    return "\n".join(paras)

COMP_MODELS = [
    ("ling-flash", "inclusionai/ling-3.0-flash", None),
    ("mistral-nemo", "mistralai/mistral-nemo", None),
    ("nex-n2-mini", "nex-agi/nex-n2-mini", None),
    ("solar-pro4", "upstage/solar-pro4", None),
    ("minimax-m3-free", "minimax/minimax-m3:free", None),
    ("kat-local", "kat-coder-v2.5-35b-a3b-abliterated", None),
    ("qwen-OFF",   "qwen/qwen3.7-flash", {"enabled": False}),
    ("glm-high",   "z-ai/glm-5.3-flash", {"effort": "high"}),
    ("deepseek-ON","deepseek/deepseek-v4-flash", {"enabled": True}),
    ("gptoss-ON",  "openai/gpt-oss-120b", {"enabled": True}),
]
COMP_PROMPT = ("Compress the following document into a dense summary under 400 words, "
    "preserving all key decisions, constraints, numbers, owners, and action items. "
    "Output only the summary.\n\n")

# ============================ TITLE ============================
TITLE_SCENARIOS = [
    ("LightRAG embedding swap without re-embedding", ["lightrag","embedding","swap"]),
    ("Debugging Docker container OOM during burst test", ["docker","oom"]),
    ("Planning PM-02 always-on node bring-up", ["pm-02"]),
    ("Migrating Honcho memory sync to workspace auth", ["honcho","auth"]),
    ("Benchmarking speculative decoding MTP settings", ["mtp","benchmark"]),
    ("SearXNG engine redundancy for research trawling", ["searxng"]),
]
TITLE_MODELS = [
    ("ling-flash", "inclusionai/ling-3.0-flash", None),
    ("mistral-nemo", "mistralai/mistral-nemo", None),
    ("minimax-m3-free", "minimax/minimax-m3:free", None),
    ("qwen-OFF",  "qwen/qwen3.7-flash", {"enabled": False}),
    ("deepseek-OFF","deepseek/deepseek-v4-flash", {"enabled": False}),
    ("glm-low",   "z-ai/glm-5.3-flash", {"effort": "low"}),
]
TITLE_PROMPT = "Generate a 4-6 word chat title for this message, no quotes: '{}'"

# ============================ HELPER ============================
HELPER_SCENARIOS = [
    {"id":"summarize","task":"Summarize in 2 sentences the key tradeoff between bge-m3 and Qwen3-Embedding for a RAG stack.",
     "rubric":["mentions multilingual/multi-vector strength of bge-m3 OR instruction-aware/32k ctx of Qwen3","notes a real tradeoff (dimension, context length, or task fit)","exactly 2 sentences"]},
    {"id":"json_extract","task":'Extract structured JSON from this text. Output ONLY JSON with keys "name", "version", "ctx": \n"The Qwen3-Embedding-0.6B model (v2 release) supports 32k context and 1024 dimensions."',
     "rubric":['valid JSON','name contains Qwen3','ctx mentions 32k or 32768','dimension 1024 present if referenced']},
    {"id":"simplify","task":"Rewrite this for a non-technical reader in 1 sentence: 'The speculative decoding acceptance rate of 94% with p-min 0.75 yields 26 tok/s generation on the 35B MoE.'",
     "rubric":["no unexplained jargon like tok/s, p-min, MoE in final","conveys faster text generation","1 sentence"]},
    {"id":"classify","task":"Classify the intent of this message as one word (question/command/feedback): 'Can you check whether the firecrawl container is healthy?'",
     "rubric":["answer is exactly one of question/command/feedback","correct answer is question"]},
    {"id":"constraint","task":"List exactly 3 bullet points, each starting with '- ', comparing Redis and Valkey for caching. No extra text before or after.",
     "rubric":["exactly 3 bullets","each starts with '- '","no preamble text","mentions both Redis and Valkey"]},
    {"id":"translate_tone","task":"Convert this terse ops note into a polite customer-facing status update (2 sentences max): 'DB pool at 400. Backfill slowed. ETA unknown.'",
     "rubric":["polite/professional tone","mentions performance degradation OR maintenance","2 sentences or fewer","no internal jargon like 'DB pool' or 'backfill' without context"]},
]
HELPER_MODELS = [
    ("ling-flash", "inclusionai/ling-3.0-flash", None),
    ("mistral-nemo", "mistralai/mistral-nemo", None),
    ("nex-n2-mini", "nex-agi/nex-n2-mini", None),
    ("solar-pro4", "upstage/solar-pro4", None),
    ("minimax-m3-free", "minimax/minimax-m3:free", None),
    ("kat-local", "kat-coder-v2.5-35b-a3b-abliterated", None),
    ("qwen-OFF", "qwen/qwen3.7-flash", {"enabled": False}),
    ("glm-high", "z-ai/glm-5.3-flash", {"effort": "high"}),
    ("gptoss-high","openai/gpt-oss-120b", {"effort": "high"}),
]

# ============================ VISION ============================
def _gen_images():
    """Generate 6 test images with planted ground truth; return list of (name, b64, question, expect)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    out = []
    # 1. bar chart
    img = Image.new("RGB",(500,300),"white"); d=ImageDraw.Draw(img)
    vals = {"Alpha":40,"Beta":75,"Gamma":25,"Delta":90}
    for i,(k,v) in enumerate(vals.items()):
        x=60+i*110; d.rectangle([x,280-v*2.5,x+70,280],fill="steelblue"); d.text((x+10,285),f"{k}={v}",fill="black")
    d.text((10,10),"Sales by Region",fill="black")
    out.append(("bar_chart", img, "What is the exact value of the Beta bar?", "75"))
    # 2. dense text memo
    img = Image.new("RGB",(600,200),"white"); d=ImageDraw.Draw(img)
    d.text((10,10),"MEMO: The deadline is October 15. Contact: Dana (ext 4471).",fill="black")
    d.text((10,35),"Budget line: $18,500 for Q4 infrastructure.",fill="black")
    d.text((10,60),"Priority: P2. Do not deploy watchdog cron this quarter.",fill="black")
    out.append(("memo", img, "What is Dana's phone extension, exactly?", "4471"))
    # 3. simple table
    img = Image.new("RGB",(400,220),"white"); d=ImageDraw.Draw(img)
    d.text((20,20),"node   | status | load",fill="black")
    d.text((20,45),"node-1 | up     | 42%",fill="black")
    d.text((20,70),"node-2 | down   | 0%",fill="black")
    d.text((20,95),"node-3 | up     | 87%",fill="black")
    out.append(("table", img, "Which node is down?", "node-2"))
    # 4. big number
    img = Image.new("RGB",(400,150),"white"); d=ImageDraw.Draw(img)
    d.text((100,40),"TOKEN: 88293-XQ",fill="black")
    out.append(("token", img, "What is the token shown (include the dash part)?", "88293-XQ"))
    # 5. arrows/flow
    img = Image.new("RGB",(500,150),"white"); d=ImageDraw.Draw(img)
    d.text((30,60),"[A] --> [B] --> [C] --> [D]",fill="black")
    out.append(("flow", img, "What letter comes immediately after B in the flow?", "C"))
    # 6. handwriting-ish (slanted text)
    img = Image.new("RGB",(400,150),"white"); d=ImageDraw.Draw(img)
    d.text((80,60),"secret code: ZEBRA-7",fill="black")
    out.append(("code_word", img, "What is the secret code (include the number)?", "ZEBRA-7"))
    b64s = []
    for name, im, q, exp in out:
        buf = io.BytesIO(); im.save(buf, format="PNG")
        b64s.append((name, base64.b64encode(buf.getvalue()).decode(), q, exp))
    return b64s

def vision_support_probe():
    """Return candidate models that accept image input."""
    imgs = _gen_images()
    if not imgs: return []
    name, b64, q, exp = imgs[0]
    cands = [("ling-flash","inclusionai/ling-3.0-flash",None),
             ("mistral-nemo","mistralai/mistral-nemo",None),
             ("nex-n2-mini","nex-agi/nex-n2-mini",None),
             ("solar-pro4","upstage/solar-pro4",None),
             ("gemma-4-31b","google/gemma-4-31b-it:free",None),
             ("nemotron-super","nvidia/nemotron-3-super-120b-a12b:free",None),
             ("minimax-m3-free","minimax/minimax-m3:free",None),
             ("qwen3.7-flash","qwen/qwen3.7-flash",{"enabled":False}),
             ("glm5.3-flash","z-ai/glm-5.3-flash",{"effort":"low"}),
             ("gptoss-120b","openai/gpt-oss-120b",{"effort":"high"}),
             ("minimax-m3","minimax/minimax-m3:free",{"effort":"low"}),
             ("deepseek-v4","deepseek/deepseek-v4-flash",{"enabled":False})]
    ok = []
    for label, m, r in cands:
        try:
            msg = [{"role":"user","content":[{"type":"text","text":q},
                    {"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}}]}]
            out,_ = chat(m, msg, 100, reasoning=r)
            ok.append((label,m,r))
            print(f"  vision support: {label} YES ({out[:30]!r})")
        except Exception as e:
            print(f"  vision support: {label} no ({str(e)[:40]})")
    return ok

VISION_MODELS = None  # probed at runtime

# ============================ RUNNERS ============================
def run_review(model, reasoning, snippet, trial):
    t0=time.time()
    try:
        review, usage = chat(model, [{"role":"user","content":REVIEW_PROMPT.format(code=snippet["code"])}], 1500, reasoning=reasoning)
    except Exception as e:
        return {"error": str(e)[:80]}
    buglist = "\n".join(f"- {b}" for b in snippet["bugs"])
    j = (f"You are grading a code review against a hidden list of planted bugs.\n\nPLANTED BUGS (ground truth):\n{buglist}\n\n"
         f"REVIEW OUTPUT:\n{review[:5000]}\n\nFor each planted bug (B1..B3), decide if the review found the same root cause "
         f'(different wording OK). Reply ONLY JSON: {{"found":["B1"],"missed":["B2","B3"],"hallucinated":<int, count of claims that are NOT real bugs in the code>}}')
    try:
        g = judge_json(j)
    except Exception as e:
        return {"error": f"judge: {str(e)[:60]}"}
    return {"latency": round(time.time()-t0,1), "found": g.get("found",[]), "missed": g.get("missed",[]),
            "hallucinated": g.get("hallucinated",0), "score": len(g.get("found",[]))/3,
            "rtok": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0),
            "cost": usage.get("cost",0)}

def run_compression(model, reasoning, seed, trial):
    doc = _build_comp_doc(seed)
    t0=time.time()
    try:
        summary, usage = chat(model, [{"role":"user","content":COMP_PROMPT+doc}], 900, reasoning=reasoning)
    except Exception as e:
        return {"error": str(e)[:80]}
    fl = "\n".join(f"{fid}: {f}" for fid,f in COMP_FACTS)
    j = (f"You grade a summary against facts planted in the source document.\n\nPLANTED FACTS:\n{fl}\n\nSUMMARY:\n{(summary or '')[:6000]}\n\n"
         f'For each fact judge if the summary preserves the SPECIFIC info (numbers, owners, decisions; paraphrase OK). Reply ONLY JSON: {{"preserved":["F1"],"lost":["F2"]}} — every fact in exactly one list.')
    try:
        g = judge_json(j)
    except Exception as e:
        return {"error": f"judge: {str(e)[:60]}"}
    return {"latency": round(time.time()-t0,1), "preserved": len(g.get("preserved",[])), "total": len(COMP_FACTS),
            "lost": g.get("lost",[]), "score": len(g.get("preserved",[]))/len(COMP_FACTS),
            "rtok": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0),
            "cost": usage.get("cost",0), "summary_chars": len(summary or "")}

def run_title(model, reasoning, scenario, trial):
    topic, keywords = scenario
    t0=time.time()
    try:
        title, usage = chat(model, [{"role":"user","content":TITLE_PROMPT.format(topic)}], 60, reasoning=reasoning)
    except Exception as e:
        return {"error": str(e)[:80]}
    title = (title or "").strip().strip('"')
    words = len(title.split())
    sc = 0.0
    sc += 1.0 if 4 <= words <= 8 else (0.5 if 2 <= words <= 10 else 0.0)
    sc += 0.5 if not any(c in title for c in ['"','\n']) else 0.0
    kw_hit = sum(1 for k in keywords if k.lower() in title.lower())
    sc += min(1.5, kw_hit * 0.75)
    return {"latency": round(time.time()-t0,1), "score": min(sc,3.0)/3.0, "title": title[:60],
            "rtok": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0), "cost": usage.get("cost",0)}

def run_helper(model, reasoning, scen, trial):
    t0=time.time()
    try:
        out, usage = chat(model, [{"role":"user","content":scen["task"]}], 400, reasoning=reasoning)
    except Exception as e:
        return {"error": str(e)[:80]}
    j = (f"Grade this model output against a rubric (each criterion pass=1 fail=0).\nTASK: {scen['task']}\n\nRUBRIC:\n" +
         "\n".join(f"- {r}" for r in scen["rubric"]) +
         f'\n\nOUTPUT:\n{(out or "")[:2000]}\n\nReply ONLY JSON: {{"passes":["0","2"],"fails":["1","3"]}} using criterion INDEX numbers.')
    try:
        g = judge_json(j)
    except Exception as e:
        return {"error": f"judge: {str(e)[:60]}"}
    p = len(g.get("passes",[]))
    return {"latency": round(time.time()-t0,1), "score": p/len(scen["rubric"]),
            "rtok": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0), "cost": usage.get("cost",0)}

def run_vision(model, reasoning, img_tuple, trial):
    name, b64, q, expect = img_tuple
    t0=time.time()
    msg = [{"role":"user","content":[{"type":"text","text":q+" Answer with only the value."},
            {"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}}]}]
    try:
        out, usage = chat(model, msg, 150, reasoning=reasoning)
    except Exception as e:
        return {"error": str(e)[:80]}
    correct = expect.lower() in (out or "").lower()
    return {"latency": round(time.time()-t0,1), "score": 1.0 if correct else 0.0,
            "answer": (out or "")[:40], "expect": expect, "rtok": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0),
            "cost": usage.get("cost",0)}

# ============================ MAIN ============================
def main():
    jobs, runner, label = [], None, SLOT
    if SLOT == "review":
        runner = run_review
        for mn, m, r in REVIEW_MODELS:
            for sn in REVIEW_SNIPPETS:
                for t in range(1, TRIALS+1):
                    jobs.append(((mn, m, r, sn, t), lambda a: runner(a[1],a[2],a[3],a[4])))
    elif SLOT == "compression":
        runner = run_compression
        for mn, m, r in COMP_MODELS:
            for seed in (7, 42, 99, 123, 555, 2024):
                for t in range(1, TRIALS+1):
                    jobs.append(((mn, m, r, seed, t), lambda a: runner(a[1],a[2],a[3],a[4])))
    elif SLOT == "title":
        runner = run_title
        for mn, m, r in TITLE_MODELS:
            for scen in TITLE_SCENARIOS:
                for t in range(1, TRIALS+1):
                    jobs.append(((mn, m, r, scen, t), lambda a: runner(a[1],a[2],a[3],a[4])))
    elif SLOT == "helper":
        runner = run_helper
        for mn, m, r in HELPER_MODELS:
            for scen in HELPER_SCENARIOS:
                for t in range(1, TRIALS+1):
                    jobs.append(((mn, m, r, scen, t), lambda a: runner(a[1],a[2],a[3],a[4])))
    elif SLOT == "vision":
        models = vision_support_probe()
        imgs = _gen_images()
        runner = run_vision
        for mn, m, r in models:
            for img in imgs:
                for t in range(1, TRIALS+1):
                    jobs.append(((mn, m, r, img, t), lambda a: runner(a[1],a[2],a[3],a[4])))
    else:
        print("unknown slot"); return

    print(f"[{label}] {len(jobs)} jobs", flush=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fn, args): args for args, fn in jobs}
        for fut in concurrent.futures.as_completed(futs):
            args = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"error": str(e)[:80]}
            r.update({"model": args[0]})
            results.append(r)
            st = r.get("score")
            print(f"  {args[0]:>16} {str(args[3])[:28]:>28} t{args[-1]} score={st if st is not None else r.get('error','?')}", flush=True)

    json.dump(results, open(rf"C:/Users/Shehryar/ai-stack/aux_bench_{SLOT}_results.json","w"), indent=1)
    print(f"\n=== [{SLOT.upper()}] SUMMARY (per model, avg over {TRIALS} trials) ===")
    models = sorted(set(r["model"] for r in results))
    for mn in models:
        rs = [r for r in results if r["model"]==mn and "error" not in r]
        errs = [r for r in results if r["model"]==mn and "error" in r]
        if not rs:
            print(f"{mn:>16}: ALL ERRORS ({len(errs)})"); continue
        sc = sum(r["score"] for r in rs)/len(rs)
        lat = sum(r["latency"] for r in rs)/len(rs)
        hal = sum(r.get("hallucinated",0) for r in rs)/len(rs)
        cost = sum(r.get("cost",0) for r in rs)
        print(f"{mn:>16}: score={sc:.0%} lat={lat:.1f}s halluc={hal:.2f} cost=${cost:.4f} errs={len(errs)}")

if __name__ == "__main__":
    main()
