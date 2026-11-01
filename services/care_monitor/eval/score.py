"""Сравнение версий промптов по ответам в ab_results.jsonl.

python3 eval/score.py — по каждому шагу и каждой версии: сколько раз модель
сказала «да» и сколько из них верно, сколько пропустила, общая точность.
Правильный ответ — truth.jsonl (просмотрено глазами); события, где и сам
проверяющий не уверен, не считаются.
"""
import collections
import json
import os

R = os.path.dirname(os.path.abspath(__file__))
truth = {}
for line in open(os.path.join(R, "truth.jsonl"), encoding="utf-8"):
    row = json.loads(line)
    if row.get("confidence") != "low":
        truth[row["aid"]] = row["mine"]

rows = [json.loads(l) for l in open(os.path.join(R, "ab_results.jsonl"), encoding="utf-8")]

# шаг -> [(поле в ответе модели, поле в truth)]
FIELDS = {
    "fall": [("fallen", "fallen")],
    "sleep": [("sleeping", "sleeping")],
    "meal": [("eating", "eating"), ("drinking", "drinking")],
    "gait": [("walks", "walks")],
}

stats = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    mine = truth.get(r["aid"])
    answer = r["answer"]
    if mine is None or not isinstance(answer, dict):
        continue
    key = (r["step"], r["version"])
    if r["step"] == "posture":
        if mine.get("posture") and answer.get("posture"):
            stats[key + ("posture",)]["n"] += 1
            stats[key + ("posture",)]["ok"] += answer["posture"] == mine["posture"]
        continue
    for model_field, true_field in FIELDS.get(r["step"], []):
        t, m = mine.get(true_field), answer.get(model_field)
        if t is None or m is None:
            continue
        c = stats[key + (model_field,)]
        c["tp"] += bool(m) and bool(t)
        c["fp"] += bool(m) and not t
        c["fn"] += (not m) and bool(t)
        c["tn"] += (not m) and not t

for (step, version, field), c in sorted(stats.items(), key=lambda x: (x[0][0], x[0][2], x[0][1])):
    if field == "posture":
        print(f"{step:8} {field:9} {version:4}  совпало {c['ok']}/{c['n']}"
              f"  точность {100 * c['ok'] // max(1, c['n'])}%")
        continue
    n = c["tp"] + c["fp"] + c["fn"] + c["tn"]
    print(f"{step:8} {field:9} {version:4}  «да»: верно {c['tp']:2} ложно {c['fp']:2} | "
          f"«нет»: верно {c['tn']:2} пропуск {c['fn']:2} | точность {100 * (c['tp'] + c['tn']) // max(1, n)}%")
