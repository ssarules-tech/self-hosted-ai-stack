"""
Extended review quality benchmark: does reasoning level affect bug-detection?
Target: z-ai/glm-5.3-flash at default reasoning vs effort=low vs effort=high.
Scoring: judge model (deepseek-v4-flash, cheap) maps each review against the
known planted-bug list for the snippet. 2 trials per (variant, snippet).
"""
import json, time, urllib.request, urllib.error, concurrent.futures, sys, os

KEY = open(os.path.expanduser("~") + "/.or_key").read().strip() if os.path.exists(os.path.expanduser("~") + "/.or_key") else None
if KEY is None:
    # fallback: read from hermes .env
    import re
    env = open(r"C:/Users/Shehryar/AppData/Local/hermes/.env").read()
    KEY = re.search(r"^OPENROUTER_API_KEY=(.+)$", env, re.M).group(1).strip()
URL = "https://openrouter.ai/api/v1/chat/completions"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "z-ai/glm-5.3-flash"
TARGET_REASONING = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None  # e.g. '{"effort":"high"}'
TAG = sys.argv[3] if len(sys.argv) > 3 else "default"
OUT = sys.argv[4] if len(sys.argv) > 4 else r"C:/Users/Shehryar/ai-stack/review_reasoning_results.json"
JUDGE = "qwen/qwen3.7-flash"  # reasoning-off grader; deepseek burned entire budget on hidden reasoning even at effort=low

# ---- 6 snippets, each with 3 planted HARD bugs ----
SNIPPETS = [
{
"id": "cache_invalidation",
"code": '''class Cache:
    def __init__(self, ttl=60):
        self.store = {}
        self.ttl = ttl

    def set(self, key, value, now=None):
        self.store[key] = (value, time.monotonic())

    def get(self, key):
        if key not in self.store:
            return None
        value, ts = self.store[key]
        if time.monotonic() - ts > self.ttl:
            del self.store[key]
            return None
        return value

    def delete(self, key):
        self.store.pop(key, None)
''',
"bugs": [
 "B1: set() takes a ttl/now parameter but never uses it — the `now` argument is ignored and ttl is not stored per-key, so a per-entry TTL passed by callers silently does nothing",
 "B2: delete() removes the key but nothing clears associated per-key metadata elsewhere / delete does not invalidate dependent keys (stale association remains)",
 "B3: get() returns the stored value object by reference, so callers mutating the returned object corrupt the cache copy (no defensive copy)",
],
},
{
"id": "async_race",
"code": '''import asyncio

class RateLimiter:
    def __init__(self, max_per_second=10):
        self.max = max_per_second
        self.tokens = max_per_second
        self.updated = time.monotonic()
        self.lock = None

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.updated
        self.tokens = min(self.max, self.tokens + elapsed * self.max)
        self.updated = now

    def acquire(self):
        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    async def acquire_async(self):
        return self.acquire()
''',
"bugs": [
 "B1: acquire() does check-then-decrement on shared state with no lock — concurrent async callers can both pass the `if self.tokens >= 1` check and double-spend tokens (race condition)",
 "B2: self.lock is initialized to None and never created/used, so any locking intent is dead code",
 "B3: acquire_async() calls the synchronous acquire() directly without awaiting a lock or yielding, so it provides no async safety and blocks the event loop under contention",
],
},
{
"id": "money_decimal",
"code": '''from decimal import Decimal

def apply_discount(cart_items, percent_off):
    total = 0
    for item in cart_items:
        price = item["price"]
        discounted = price * (100 - percent_off) / 100
        total += discounted
    return round(total, 2)

def charge(accounts, account_id, amount):
    acct = accounts.get(account_id)
    if amount <= 0:
        raise ValueError("invalid amount")
    acct["balance"] -= amount
    return acct["balance"]
''',
"bugs": [
 "B1: prices are Decimal (implied by import) but discount math mixes float arithmetic (100 - percent_off)/100 — Decimal*float raises TypeError, or if prices are floats, money is accumulated in binary float causing cent drift",
 "B2: round(total, 2) applied once at the end instead of per-item quantization — each item's discounted price is not rounded to cents before summing, causing settlement mismatch with per-item receipts",
 "B3: charge() checks amount validity but never checks whether acct['balance'] covers the amount — accounts can go negative (insufficient-funds bug)",
],
},
{
"id": "retry_poison",
"code": '''import requests

def fetch_with_retry(url, retries=3):
    last_err = None
    for i in range(retries):
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            time.sleep(2 ** i)
        except RuntimeError:
            return None
    raise last_err
''',
"bugs": [
 "B1: RuntimeError from HTTP>=400 is caught and returns None immediately — a single 429/503 (retryable) is never retried, and the caller cannot distinguish 'not found' from 'server error'",
 "B2: resp is never closed (no context manager / resp.close()) — connection pool exhaustion under repeated calls",
 "B3: if all retries fail with non-network errors... raise last_err raises the last CONNECTION error, but if the final attempt raised RuntimeError it returned None earlier; conversely if retries=0, last_err is None and `raise None` raises TypeError (edge-case crash)",
],
},
{
"id": "off_by_one_contract",
"code": '''def paginate(items, page, per_page):
    start = page * per_page
    end = start + per_page
    return items[start:end]

def find_last_duplicate(seq):
    seen = set()
    last_dup = None
    for i, x in enumerate(seq):
        if x in seen:
            last_dup = (i, x)
        seen.add(x)
    return last_dup

def percentile(data, p):
    data = sorted(data)
    k = (len(data) - 1) * p
    f = int(k)
    return data[f]
''',
"bugs": [
 "B1: paginate uses 0-based page without validation — page=-1 silently returns garbage slice; negative page accepted (contract violation)",
 "B2: find_last_duplicate returns index i of the CURRENT occurrence but the docstring-contract 'last duplicate' should be the later index; more critically it returns the second occurrence position, not the last duplicate pair when 3+ copies exist it updates correctly, BUT if seq is empty it returns None which collides with 'no duplicates' sentinel — ambiguous contract",
 "B3: percentile ignores the fractional part of k entirely — always floors to data[int(k)] instead of interpolating, so p=0.5 on even-length lists returns a lower element than the true median (off-by-one interpolation bug)",
],
},
{
"id": "resource_leak",
"code": '''import threading

class ConnectionPool:
    def __init__(self, size=5):
        self.size = size
        self.free = [f"conn{i}" for i in range(size)]
        self.cond = threading.Condition()

    def checkout(self, timeout=10):
        with self.cond:
            if not self.free:
                self.cond.wait(timeout)
        return self.free.pop() if self.free else None

    def checkin(self, conn):
        self.free.append(conn)
        with self.cond:
            self.cond.notify()
''',
"bugs": [
 "B1: checkout() releases the condition lock (exits `with` block) BEFORE popping from self.free — the wait-for-notification and the pop are not atomic; two threads can both see free non-empty after notify and one pops... but worse, pop happens outside the lock entirely → race/.IndexError under concurrency",
 "B2: checkin() appends conn BEFORE acquiring the lock — mutation of shared list outside lock; also notifies only one waiter even if multiple connections were returned",
 "B3: pool never validates a checked-in conn belongs to it / double-checkin grows free beyond size, eventually returning the same connection to two callers (exceeds pool capacity invariant)",
],
},
]

PROMPT_TMPL = """Review this Python code for bugs. List ONLY genuine bugs with severity (Critical/High/Medium/Low) and a one-line explanation each. Be precise about the root cause.

```python
{code}
```"""

def chat(model, messages, maxtok, reasoning=None, retries=4):
    body = {"model": model, "messages": messages, "max_tokens": maxtok}
    if reasoning is not None:
        body["reasoning"] = reasoning
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    for attempt in range(retries):
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=180).read())
            msg = d["choices"][0]["message"]
            return msg.get("content") or "", d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(3 * (2 ** attempt))
                req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
                continue
            raise

def judge(snippet, review):
    buglist = "\n".join(f"- {b}" for b in snippet["bugs"])
    j = f"""You are grading a code review against a hidden list of planted bugs.

PLANTED BUGS (ground truth):
{buglist}

REVIEW OUTPUT:
{review[:4000]}

For each planted bug (B1, B2, B3), decide if the review found the same root cause (different wording is fine). Reply with ONLY JSON: {{"found": ["B1","B3"], "missed": ["B2"], "hallucinated": <int, count of claims that are NOT real bugs in the code>}}"""
    out, _ = chat(JUDGE, [{"role": "user", "content": j}], 2000, reasoning={"enabled": False})
    if not out.strip():
        raise RuntimeError("judge returned empty content (reasoning consumed budget)")
    try:
        js = out[out.index("{"):out.rindex("}")+1]
        return json.loads(js)
    except Exception:
        print(f"  [judge parse fail] raw: {out[:200]!r}", flush=True)
        raise

VARIANTS = [("default", None)] if TARGET_REASONING is not None else [("default", None), ("effort-low", {"effort": "low"}), ("effort-high", {"effort": "high"})]
if TARGET_REASONING is not None:
    VARIANTS = [(TAG, TARGET_REASONING)]

def run_one(variant_name, reasoning, snippet, trial):
    t0 = time.time()
    try:
        review, usage = chat(TARGET, [{"role": "user", "content": PROMPT_TMPL.format(code=snippet["code"])}], 1200, reasoning=reasoning)
    except Exception as e:
        return {"variant": variant_name, "snippet": snippet["id"], "trial": trial, "error": str(e)[:80]}
    lat = round(time.time() - t0, 1)
    g = judge(snippet, review)
    total = len(snippet["bugs"])
    return {"variant": variant_name, "snippet": snippet["id"], "trial": trial, "latency": lat,
            "found": g.get("found", []), "missed": g.get("missed", []),
            "hallucinated": g.get("hallucinated", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            "cost": usage.get("cost", 0), "score": len(g.get("found", [])) / total}

if __name__ == "__main__":
    jobs = [(vn, rc, sn, t) for vn, rc in VARIANTS for sn in SNIPPETS for t in (1, 2)]
    print(f"running {len(jobs)} review jobs + {len(jobs)} judge calls...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        for r in ex.map(lambda j: run_one(*j), jobs):
            results.append(r)
            st = f"{r.get('found')} | miss {r.get('missed')} | halluc {r.get('hallucinated')}" if "error" not in r else r["error"]
            print(f"[{r['variant']:>11}] {r['snippet']:>20} t{r['trial']} score={r.get('score','-')} {r.get('latency','-')}s {st}", flush=True)

    json.dump(results, open(r"C:/Users/Shehryar/ai-stack/review_reasoning_results.json", "w"), indent=1)
    print("\n=== SUMMARY (avg over 2 trials x 6 snippets) ===")
    for vn, _ in VARIANTS:
        rs = [r for r in results if r["variant"] == vn and "error" not in r]
        if not rs: continue
        sc = sum(r["score"] for r in rs) / len(rs)
        lat = sum(r["latency"] for r in rs) / len(rs)
        hal = sum(r["hallucinated"] for r in rs) / len(rs)
        cost = sum(r["cost"] for r in rs)
        rt = sum(r["reasoning_tokens"] for r in rs) / len(rs)
        print(f"{vn:>11}: detection={sc:.0%}  latency={lat:.1f}s  halluc/trial={hal:.2f}  reasoning_tok/trial={rt:.0f}  total_cost=${cost:.4f}")
