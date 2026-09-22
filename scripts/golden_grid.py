"""
GOLDEN GRID — full factorial aux benchmark.
models x reasoning-configs x budgets(900/2000/4000) x scenarios x 3 trials.
Checkpointed: every completed run appended to golden_grid_results.jsonl (crash-safe).
Aggregate with: python golden_grid.py --aggregate
"""
import json, time, os, sys, re, urllib.request, urllib.error, random, argparse

KEY = re.search(r"^OPENROUTER_API_KEY=(.+)$",
    open(r"C:/Users/Shehryar/AppData/Local/hermes/.env").read(), re.M).group(1).strip()
URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE = "qwen/qwen3.7-flash"
OUT = r"C:/Users/Shehryar/ai-stack/golden_grid_results.jsonl"
BUDGETS = [900, 2000, 4000]
TRIALS = 3

# ---------- scenarios (shared with aux_bench for consistency) ----------
_src = open(r"C:/Users/Shehryar/ai-stack/aux_bench.py").read()
exec(_src[_src.index("REVIEW_SNIPPETS = ["):_src.index("REVIEW_MODELS")])
exec(_src[_src.index("COMP_FACTS = ["):_src.index("COMP_MODELS")])
exec(_src[_src.index("def _build_comp_doc"):_src.index("COMP_MODELS")])
_m = re.search(r'REVIEW_PROMPT = """(.*?)"""', _src, re.S)
REVIEW_PROMPT = _m.group(1)
COMP_SEEDS = [7, 42, 99, 123]  # 4 of 6 docs

# ---------- model grid: (label, model_id, [valid reasoning configs]) ----------
# compat map: enabled:false 400s on glm/gpt-oss/minimax; minimax ignores all; effort form universal on OR
GRID = [
    ("glm",      "z-ai/glm-5.3-flash",        [None, {"effort":"low"}, {"effort":"high"}]),
    ("qwen37",   "qwen/qwen3.7-flash",        [{"enabled":False}, {"enabled":True}, {"enabled":True,"effort":"low"}, {"enabled":True,"effort":"high"}]),
    ("qwen38",   "qwen/qwen3.8-flash",        [{"enabled":False}, {"enabled":True}, {"enabled":True,"effort":"low"}, {"enabled":True,"effort":"high"}]),
    ("gptoss",   "openai/gpt-oss-120b",       [None, {"effort":"low"}, {"effort":"high"}]),
    ("deepseek", "deepseek/deepseek-v4-flash",[{"enabled":False}, {"enabled":True}, {"enabled":True,"effort":"low"}, {"enabled":True,"effort":"high"}]),
    ("mm3free",  "minimax/minimax-m3:free",   [None]),
    ("ling",     "inclusionai/ling-3.0-flash",[None, {"effort":"low"}, {"effort":"high"}]),
    ("solar",    "upstage/solar-pro4",        [None, {"effort":"low"}, {"effort":"high"}]),
    ("nex",      "nex-agi/nex-n2-mini",       [None, {"effort":"low"}]),
    ("mistral",  "mistralai/mistral-nemo",    [None]),
]
JUDGES = [("deepseek","deepseek/deepseek-v4-flash",{"enabled":False}),
          ("glm","z-ai/glm-5.3-flash",{"effort":"low"}),
          ("qwen38","qwen/qwen3.8-flash",{"enabled":False})]
FREE_MODELS = {"mm3free"}  # sequential scheduling
PAID_WORKERS = 6

def chat(model, messages, maxtok, reasoning=None, retries=5):
    body = {"model": model, "messages": messages, "max_tokens": maxtok}
    if reasoning is not None: body["reasoning"] = reasoning
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    for a in range(retries):
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=300).read())
            return d["choices"][0]["message"].get("content") or "", d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503) and a < retries-1:
                time.sleep(3 * 2**a); continue
            raise

def judge_json(prompt, maxtok=2500):
    out, _ = chat(JUDGE, [{"role":"user","content":prompt}], maxtok, reasoning={"enabled":False})
    if not out.strip(): raise RuntimeError("judge empty")
    return json.loads(out[out.index("{"):out.rindex("}")+1])

def ensemble_review(snippet, review):
    """Run all 3 judges; return {judge_name: {found,missed,hallucinated,raw}}"""
    buglist = "\n".join(f"- {b}" for b in snippet["bugs"])
    jp = (f"You are grading a code review against a hidden list of planted bugs.\n\nPLANTED BUGS (ground truth):\n{buglist}\n\n"
          f"REVIEW OUTPUT:\n{review[:5000]}\n\nFor each planted bug (B1..B3), decide if the review found the same root cause "
          f'(different wording OK). Reply ONLY JSON: {{"found":["B1"],"missed":["B2","B3"],"hallucinated":<int, count of claims that are NOT real bugs in the code>}}')
    verdicts = {}
    for jn, jm, jrc in JUDGES:
        try:
            out, _ = chat(jm, [{"role":"user","content":jp}], 2500, reasoning=jrc)
            g = json.loads(out[out.index("{"):out.rindex("}")+1])
            verdicts[jn] = {"found":g.get("found",[]), "missed":g.get("missed",[]),
                            "hallucinated":g.get("hallucinated",0), "raw":out[:2000]}
        except Exception as e:
            verdicts[jn] = {"error": str(e)[:60]}
    return verdicts

def ensemble_compress(summary):
    fl = "\n".join(f"{fid}: {f}" for fid,f in COMP_FACTS)
    jp = (f"You grade a summary against facts planted in the source document.\n\nPLANTED FACTS:\n{fl}\n\nSUMMARY:\n{(summary or '')[:6000]}\n\n"
          f'For each fact judge if the summary preserves the SPECIFIC info (numbers, owners, decisions; paraphrase OK). '
          f'Reply ONLY JSON: {{"preserved":["F1"],"lost":["F2"]}} - every fact in exactly one list.')
    verdicts = {}
    for jn, jm, jrc in JUDGES:
        try:
            out, _ = chat(jm, [{"role":"user","content":jp}], 2500, reasoning=jrc)
            g = json.loads(out[out.index("{"):out.rindex("}")+1])
            verdicts[jn] = {"preserved":g.get("preserved",[]), "lost":g.get("lost",[]), "raw":out[:2000]}
        except Exception as e:
            verdicts[jn] = {"error": str(e)[:60]}
    return verdicts

def consensus_score_review(verdicts, n_bugs=3):
    """Mean score across judges that returned valid verdicts + agreement flag."""
    scores, halls = [], []
    for jn, v in verdicts.items():
        if "error" not in v:
            scores.append(len(v["found"])/n_bugs)
            halls.append(v.get("hallucinated",0))
    if not scores: return None, None, 0
    return sum(scores)/len(scores), sum(halls)/len(halls), len(set(round(s,2) for s in scores))==1

def consensus_score_compress(verdicts, n_facts):
    scores = []
    for jn, v in verdicts.items():
        if "error" not in v:
            scores.append(len(v["preserved"])/n_facts)
    if not scores: return None, 0
    return sum(scores)/len(scores), len(set(round(s,2) for s in scores))==1

def run_review(label, model, rc, budget, snippet, trial):
    t0=time.time()
    try:
        review, usage = chat(model, [{"role":"user","content":REVIEW_PROMPT.format(code=snippet["code"])}], budget, reasoning=rc)
    except Exception as e:
        return {"slot":"review","model":label,"rc":json.dumps(rc) if rc else "default","budget":budget,
                "snippet":snippet["id"],"trial":trial,"error":str(e)[:60]}
    lat = round(time.time()-t0,1)
    verdicts = ensemble_review(snippet, review)
    sc, hal, unanimous = consensus_score_review(verdicts)
    rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0)
    return {"slot":"review","model":label,"rc":json.dumps(rc) if rc else "default","budget":budget,
            "snippet":snippet["id"],"trial":trial,"score":sc,"hallucinated":hal,"unanimous":unanimous,
            "latency":lat,"rtok":rt,"cost":usage.get("cost",0),"outlen":len(review or ""),
            "raw_output":(review or "")[:6000],"judge_verdicts":verdicts}

def run_compress(label, model, rc, budget, seed, trial):
    t0=time.time()
    doc = _build_comp_doc(seed)
    prompt = ("Compress the following document into a dense summary under 400 words, "
        "preserving all key decisions, constraints, numbers, owners, and action items. Output only the summary.\n\n"+doc)
    try:
        summary, usage = chat(model, [{"role":"user","content":prompt}], budget, reasoning=rc)
    except Exception as e:
        return {"slot":"compression","model":label,"rc":json.dumps(rc) if rc else "default","budget":budget,
                "doc":seed,"trial":trial,"error":str(e)[:60]}
    lat = round(time.time()-t0,1)
    verdicts = ensemble_compress(summary)
    sc, unanimous = consensus_score_compress(verdicts, len(COMP_FACTS))
    rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens",0)
    return {"slot":"compression","model":label,"rc":json.dumps(rc) if rc else "default","budget":budget,
            "doc":seed,"trial":trial,"score":sc,"unanimous":unanimous,
            "latency":lat,"rtok":rt,"cost":usage.get("cost",0),"outlen":len(summary or ""),
            "raw_output":(summary or "")[:6000],"judge_verdicts":verdicts}

def done_keys():
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(line)
                k = (r["slot"],r["model"],r["rc"],r["budget"],r.get("snippet") or r.get("doc"),r["trial"])
                done.add(k)
            except Exception: pass
    return done

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aggregate", action="store_true")
    args = ap.parse_args()
    if args.aggregate:
        rows = [json.loads(l) for l in open(OUT, encoding="utf-8") if l.strip()]
        ok = [r for r in rows if "error" not in r]
        print(f"total={len(rows)} ok={len(ok)} err={len(rows)-len(ok)}")
        for slot in ("review","compression"):
            print(f"\n=== {slot.upper()} ===")
            print(f"{'model':>9} {'rc':>28} {'bud':>5} | {'score':>6} {'lat':>6} {'hall':>5} {'errs':>4}")
            combos = sorted(set((r["model"],r["rc"],r["budget"]) for r in rows if r["slot"]==slot))
            for m,rc,b in combos:
                rs = [r for r in ok if r["slot"]==slot and r["model"]==m and r["rc"]==rc and r["budget"]==b]
                errs = [r for r in rows if r["slot"]==slot and r["model"]==m and r["rc"]==rc and r["budget"]==b and "error" in r]
                if not rs: print(f"{m:>9} {rc:>28} {b:>5} |    --- all err ({len(errs)})"); continue
                sc = sum(r["score"] for r in rs)/len(rs)
                lat = sum(r["latency"] for r in rs)/len(rs)
                hal = sum(r.get("hallucinated",0) for r in rs)/len(rs)
                print(f"{m:>9} {rc:>28} {b:>5} | {sc:>6.0%} {lat:>5.1f}s {hal:>5.2f} {len(errs):>4}")
        return

    jobs = []
    for label, model, rcs in GRID:
        for rc in rcs:
            for budget in BUDGETS:
                for sn in REVIEW_SNIPPETS:
                    for t in range(1, TRIALS+1):
                        jobs.append(("review", label, model, rc, budget, sn["id"], t))
                for seed in COMP_SEEDS:
                    for t in range(1, TRIALS+1):
                        jobs.append(("compression", label, model, rc, budget, seed, t))
    done = done_keys()
    jobs = [j for j in jobs if (j[0], j[1], json.dumps(j[3]) if j[3] else "default", j[4], j[5], j[6]) not in done]
    total_cells = len(set((j[1], json.dumps(j[3]) if j[3] else "default", j[4]) for j in jobs))
    print(f"grid: {total_cells} cells, {len(jobs)} runs to do ({len(done)} already done)", flush=True)
    import threading, concurrent.futures
    snip_by_id = {s["id"]: s for s in REVIEW_SNIPPETS}
    lock = threading.Lock()
    fh = open(OUT, "a", encoding="utf-8")
    n = [0]
    def do_job(j):
        slot, label, model, rc, budget, sid, t = j
        if slot == "review":
            r = run_review(label, model, rc, budget, snip_by_id[sid], t)
        else:
            r = run_compress(label, model, rc, budget, sid, t)
        with lock:
            fh.write(json.dumps(r)+"\n"); fh.flush()
            n[0] += 1
            if n[0] % 20 == 0 or "error" in r:
                sc = r.get("score", r.get("error","?"))
                print(f"[{n[0]}/{len(jobs)}] {label}/{r['rc']}/b{budget} {sid} t{t}: {sc if not isinstance(sc,float) else f'{sc:.0%}'}", flush=True)
    paid = [j for j in jobs if j[1] not in FREE_MODELS]
    free = [j for j in jobs if j[1] in FREE_MODELS]
    print(f"tiered: {len(paid)} paid (6 workers), {len(free)} free (sequential)", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=PAID_WORKERS * 2) as ex:
        list(ex.map(do_job, paid))
    for j in free:  # free tier sequential
        do_job(j)
    fh.close()
    print("GRID_DONE", flush=True)

if __name__ == "__main__":
    main()
