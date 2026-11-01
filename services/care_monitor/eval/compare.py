"""Сводная таблица: точность по вопросам для нескольких вариантов.

python3 eval/compare.py — колонки:
    старый     промпты, что стояли до проверки (ответы прогона "old")
    малина     промпты, что стоят на малине сейчас (fall/sleep — "new",
               posture — "v2", meal/gait — "old")
    простые12  простые промпты, 12 кадров ("s12")
    простые24  простые промпты, 24 кадра ("s24")
Сравнение только на тех событиях, где ответ есть во ВСЕХ колонках, иначе
проценты считались бы по разным наборам.
Поза — три класса: «идёт» и «стоит» считаются одним «на ногах».
"""
import collections
import json
import os
import statistics

R = os.path.dirname(os.path.abspath(__file__))
COLUMNS = {
    "старый": {s: "old" for s in ("fall", "sleep", "posture", "meal", "gait")},
    "малина": {"fall": "new", "sleep": "new", "posture": "v2", "meal": "old", "gait": "old"},
    "простые12": {s: "s12" for s in ("fall", "sleep", "posture", "meal", "gait")},
    "простые24": {s: "s24" for s in ("fall", "sleep", "posture", "meal", "gait")},
}

truth = {}
for line in open(os.path.join(R, "truth.jsonl"), encoding="utf-8"):
    row = json.loads(line)
    if row.get("confidence") != "low":
        truth[row["aid"]] = row["mine"]

answers = {}
seconds = collections.defaultdict(list)
for line in open(os.path.join(R, "ab_results.jsonl"), encoding="utf-8"):
    r = json.loads(line)
    if isinstance(r["answer"], dict) and "_error" not in r["answer"]:
        answers[(r["aid"], r["step"], r["version"])] = r["answer"]
        if r.get("seconds"):
            seconds[r["version"]].append(r["seconds"])


def three(p):
    return {"идёт": "стоит"}.get(p, p)


def meal_of(a):
    if "doing" in a:
        return a["doing"] in ("ест",), a["doing"] in ("пьёт",)
    return bool(a.get("eating")), bool(a.get("drinking"))


# вопрос -> (шаг, как достать ответ модели, как достать правду)
QUESTIONS = {
    "упал": ("fall", lambda a: a.get("fallen"), lambda t: t.get("fallen")),
    "спит": ("sleep", lambda a: a.get("sleeping"), lambda t: t.get("sleeping")),
    "поза": ("posture", lambda a: three(a.get("posture")), lambda t: three(t.get("posture"))),
    "ест": ("meal", lambda a: meal_of(a)[0], lambda t: t.get("eating")),
    "пьёт": ("meal", lambda a: meal_of(a)[1], lambda t: t.get("drinking")),
    "ходит": ("gait", lambda a: a.get("walks"), lambda t: t.get("walks")),
}

present = [c for c in COLUMNS if any(v == COLUMNS[c]["fall"] for (_, _, v) in answers)]
print(f"{'':8}" + "".join(f"{c:>18}" for c in present))
for q, (step, got, want) in QUESTIONS.items():
    aids = [a for a in truth if want(truth[a]) is not None and all(
        (a, step, COLUMNS[c][step]) in answers for c in present)]
    cells = []
    for c in present:
        ok = fp = fn = 0
        for a in aids:
            m, t = got(answers[(a, step, COLUMNS[c][step])]), want(truth[a])
            if isinstance(t, bool):
                m = bool(m)
                fp += m and not t
                fn += (not m) and t
            ok += m == t
        extra = f" ({fp}/{fn})" if step != "posture" else ""
        cells.append(f"{100 * ok // max(1, len(aids)):3}%{extra}")
    print(f"{q:8}" + "".join(f"{x:>18}" for x in cells) + f"   n={len(aids)}")
print("в скобках: ложных «да» / пропущенных «да»")
for c in present:
    vs = {COLUMNS[c][s] for s in COLUMNS[c]}
    t = [x for v in vs for x in seconds[v]]
    if t:
        print(f"{c}: медиана ответа {statistics.median(t):.0f} с")
