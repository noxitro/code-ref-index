"""runs/index.jsonl の各実行について、Copilot の記録から往復・ツール・トークン・正答を集計する。

usage: python -I collect.py [bench_dir]
出力: runs/summary.tsv と、条件ごとの平均を標準出力に。
"""
import collections, json, os, re, sqlite3, statistics, sys

bench = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(__file__))
home = os.path.expanduser("~/.copilot")
# tasks.json(Nox)と tasks-<名前>.json(別のコードベース)。id はファイルをまたいで一意にする
tasks = {t["id"]: t for fn in sorted(os.listdir(bench)) if re.fullmatch(r"tasks(-[\w-]+)?\.json", fn)
         for t in json.load(open(os.path.join(bench, fn), encoding="utf-8"))}
db = sqlite3.connect("file:" + os.path.join(home, "session-store.db") + "?mode=ro", uri=True)
LOC = re.compile(r"(?<![\w/.-])([\w.-]+(?:/[\w.-]+)*\.(?:h|hh|hpp|hxx|c|cc|cpp|cxx|inl|ipp|inc|ixx|cppm|tpp|txx|m|mm)):(\d+)")


def normalize(path, truth_paths):
    # 書式違反で先頭のディレクトリを落とした回答(T1-D-r1 の runtime/)も中身で採点する
    if path in truth_paths:
        return path
    cands = [p for p in truth_paths if p.endswith("/" + path)]
    return cands[0] if len(cands) == 1 else path

rows = []
for line in open(os.path.join(bench, "runs", "index.jsonl"), encoding="utf-8-sig"):
    r = json.loads(line)
    if r["rep"] == 0:
        continue  # 試運転
    ev = [json.loads(l) for l in open(os.path.join(home, "session-state", r["session"], "events.jsonl"), encoding="utf-8")]
    tools = collections.Counter(e["data"]["toolName"] for e in ev if e["type"] == "tool.execution_start")
    result_chars = 0
    for e in ev:
        if e["type"] == "tool.execution_complete":
            res = e["data"].get("result")
            result_chars += len(json.dumps(res, ensure_ascii=False)) if res is not None else 0
    answer = [e["data"].get("content", "") for e in ev if e["type"] == "assistant.message"][-1:] or [""]
    truth = set(tasks[r["task"]]["truth"])
    truth_paths = {t.rsplit(":", 1)[0] for t in truth}
    got = {f"{normalize(p, truth_paths)}:{n}" for p, n in LOC.findall(answer[0].replace("\\", "/"))}
    u = db.execute(
        "select count(*), sum(input_tokens), sum(cache_read_tokens), sum(output_tokens), sum(total_nano_aiu)/1e9 "
        "from assistant_usage_events where session_id=?", (r["session"],)).fetchone()
    calls, inp, cache, out, credits = (u[0], u[1] or 0, u[2] or 0, u[3] or 0, u[4] or 0.0)
    rows.append({
        "name": r["name"], "round": r.get("round", "v1"), "task": r["task"], "cond": r["cond"], "sec": r["sec"],
        "model_calls": calls, "tool_calls": sum(tools.values()), "view": tools["view"], "lsp": tools["lsp"],
        "shell": tools["powershell"], "mcp": sum(n for t, n in tools.items() if t.startswith("code-ref-index")),
        "search": tools["rg"] + tools["grep"] + tools["glob"], "tool_result_chars": result_chars,
        "input_tokens": inp, "cache_read": cache, "uncached_input": inp - cache, "output_tokens": out,
        "credits": round(credits, 4), "tp": len(got & truth), "fp": len(got - truth), "fn": len(truth - got),
        "correct": int(got == truth), "missed": " ".join(sorted(truth - got)), "extra": " ".join(sorted(got - truth)),
    })

keys = list(rows[0].keys()) if rows else []
with open(os.path.join(bench, "runs", "summary.tsv"), "w", encoding="utf-8") as f:
    f.write("\t".join(keys) + "\n")
    for x in rows:
        f.write("\t".join(str(x[k]) for k in keys) + "\n")

print("cond  n  correct  model_calls  tool_calls  view  input_tokens  uncached  output  credits  sec")
for rd, c in sorted({(x["round"], x["cond"]) for x in rows}):
    xs = [x for x in rows if x["cond"] == c and x["round"] == rd]
    m = lambda k: statistics.mean(x[k] for x in xs)
    print(f"{rd}-{c:4} {len(xs):2}  {sum(x['correct'] for x in xs):2}/{len(xs):<2}   {m('model_calls'):8.1f}  {m('tool_calls'):9.1f}  {m('view'):4.1f}"
          f"  {m('input_tokens'):11.0f}  {m('uncached_input'):8.0f}  {m('output_tokens'):6.0f}  {m('credits'):.3f}  {m('sec'):4.0f}")
for x in rows:
    print(f"{x['name']:10} ok={x['correct']} tp={x['tp']} fp={x['fp']} fn={x['fn']} calls={x['model_calls']} tools={x['tool_calls']}"
          f"(view {x['view']}, lsp {x['lsp']}, search {x['search']}, shell {x['shell']}, mcp {x['mcp']}) in={x['input_tokens']} out={x['output_tokens']} cr={x['credits']}")
