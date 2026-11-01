"""A/B: старый промпт (как ушёл тогда) против нового на тех же кадрах и фактах.

Набор — 230 событий из db_backup/, правильные ответы в truth.jsonl (просмотрены
глазами). Запуск: python3 eval/ab_test.py, результат дописывается в
eval/ab_results.jsonl, уже сделанное пропускается.
"""
import base64, glob, json, os, random, sys, time, urllib.request

R = os.path.dirname(os.path.abspath(__file__))  # eval/
PROJ = os.path.dirname(R)
URL = "http://OLLAMA_HOST_IP:11434/api/generate"
MODEL = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "qwen3-vl:4b-instruct"
OUT = os.path.join(R, "ab_results.jsonl")
# --prompts eval/simple  — брать новые промпты из этой папки, а не из prompts/
# --frames <папка>       — кадры из <папка>/<aid>/ (перенарезка eval/recut.py)
PROMPTS = sys.argv[sys.argv.index("--prompts") + 1] if "--prompts" in sys.argv else None
FRAMES = sys.argv[sys.argv.index("--frames") + 1] if "--frames" in sys.argv else None
# 16384 хватает на 12 кадров (~13 тыс. токенов) и оставляет место 8b на 8 ГБ видеокарты
CTX = int(sys.argv[sys.argv.index("--ctx") + 1]) if "--ctx" in sys.argv else 32768
random.seed(3)

# python3 eval/ab_test.py                         — старый и новый промпт по всем шагам
# python3 eval/ab_test.py --steps sleep --tag v2  — только новый промпт, только эти шаги,
#                                                   ответы пишутся с version="v2"
# python3 eval/ab_test.py --model qwen3-vl:8b-instruct --tag 8b --per-step 15
#                                                 — медленная модель на части набора
# python3 eval/ab_test.py --model qwen3-vl:8b-instruct --tag 8b --with-old
#                                                 — другая модель: и старый промпт
#                                                   ("8b_old"), и новый ("8b")
ONLY_STEPS = set(sys.argv[sys.argv.index("--steps") + 1].split(",")) if "--steps" in sys.argv else None
TAG = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else None

man = {m["aid"]: m for m in json.load(open(os.path.join(R, "manifest.json")))}
for m in man.values():
    m["folder"] = os.path.join(PROJ, m["folder"])
res = {}
for f in glob.glob(os.path.join(R, "truth.jsonl")):
    for line in open(f):
        if line.strip():
            r = json.loads(line)
            res[r["aid"]] = r

done = set()
if os.path.exists(OUT):
    for line in open(OUT):
        r = json.loads(line)
        done.add((r["aid"], r["step"], r["version"]))


def truth_ok(aid):
    return aid in res and res[aid].get("confidence") != "low"


def has(aid, step):
    return isinstance(man[aid]["answers"].get(step), dict) and os.path.exists(
        os.path.join(man[aid]["folder"], f"prompt_{step}.txt"))


cases = []
def take(step, cond, n):
    xs = [a for a in man if truth_ok(a) and has(a, step) and cond(a)]
    random.shuffle(xs)
    cases.extend((a, step) for a in xs[:n])

mine = lambda a, k: res[a]["mine"].get(k)
take("sleep", lambda a: mine(a, "sleeping") is True, 20)
take("sleep", lambda a: mine(a, "sleeping") is False, 30)
take("fall", lambda a: man[a]["answers"]["fall"].get("fallen") is True, 33)
take("fall", lambda a: man[a]["answers"]["fall"].get("fallen") is False, 12)
take("posture", lambda a: bool(mine(a, "posture")), 40)
take("meal", lambda a: mine(a, "eating") or mine(a, "drinking"), 15)
take("meal", lambda a: mine(a, "eating") is False and mine(a, "drinking") is False
     and (man[a]["answers"]["meal"].get("eating") or man[a]["answers"]["meal"].get("drinking")), 10)
take("gait", lambda a: mine(a, "walks") is not None, 30)
seen = set(); cases = [c for c in cases if not (c in seen or seen.add(c))]
if ONLY_STEPS:
    cases = [c for c in cases if c[1] in ONLY_STEPS]
if "--per-step" in sys.argv:
    # урезанный прогон (медленная модель): N событий на шаг. Сначала берём
    # все случаи, где правильный ответ «да» (их мало, и на них модели и
    # ошибаются), потом добиваем случаями «нет».
    limit = int(sys.argv[sys.argv.index("--per-step") + 1])
    yes_field = {"sleep": ("sleeping",), "meal": ("eating", "drinking"),
                 "gait": ("walks",), "fall": ("fallen",)}
    def is_yes(case):
        return any(mine(case[0], f) for f in yes_field.get(case[1], ()))
    random.shuffle(cases)
    picked, yes, total = [], {}, {}
    for want_yes in (True, False):
        for c in cases:
            if is_yes(c) != want_yes or total.get(c[1], 0) >= limit:
                continue
            if want_yes and yes.get(c[1], 0) >= (limit + 1) // 2:
                continue            # «да» — не больше половины выборки
            picked.append(c)
            total[c[1]] = total.get(c[1], 0) + 1
            yes[c[1]] = yes.get(c[1], 0) + want_yes
    cases = picked
print("cases", len(cases), flush=True)


def facts(saved):
    body = saved.split("ФАКТЫ:\n", 1)[1]
    return body.split("\n\n", 1)[0]


def frames_for(aid, folder, step):
    src = os.path.join(FRAMES, str(aid)) if FRAMES else os.path.join(folder, "frames")
    fs = sorted(glob.glob(os.path.join(src, "frame_*.jpg")))
    # сон смотрит последнюю половину (в аддоне — STEP_FRAMES=6 из 12)
    return fs[-max(1, len(fs) // 2):] if step == "sleep" else fs


def enc(p):
    return base64.b64encode(open(p, "rb").read()).decode()


def ask(prompt, images):
    body = json.dumps({"model": MODEL, "prompt": prompt, "images": images, "stream": False,
                       "format": "json",
                       "options": {"temperature": 0, "num_ctx": CTX}}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        payload = json.load(r)
    try:
        return json.loads(payload.get("response", "")), round(time.time() - t, 1)
    except ValueError:
        return {"_raw": payload.get("response", "")[:300]}, round(time.time() - t, 1)


new_tpl = {s: open(os.path.join(PROMPTS or os.path.join(PROJ, "prompts"), f"{s}.txt"),
                   encoding="utf-8").read()
           for s in ("sleep", "fall", "posture", "meal", "gait")}

fails = 0
for i, (aid, step) in enumerate(cases):
    folder = man[aid]["folder"]
    saved = open(os.path.join(folder, f"prompt_{step}.txt"), encoding="utf-8").read()
    images = [enc(p) for p in frames_for(aid, folder, step)]
    import re
    ctx = re.sub(r"СОСТАВ КАДРОВ: \d+", f"СОСТАВ КАДРОВ: {len(images)}", facts(saved))
    versions = (("old", saved), ("new", new_tpl[step].replace("{{CONTEXT}}", ctx)))
    if TAG:
        versions = ((TAG, versions[1][1]),)
        if "--with-old" in sys.argv:
            versions = ((TAG + "_old", saved),) + versions
    for version, prompt in versions:
        if (aid, step, version) in done:
            continue
        try:
            answer, sec = ask(prompt, images)
        except Exception as e:
            print(f"ОШИБКА {aid} {step} {version}: {e}", flush=True)
            if "400" in str(e):
                continue            # не влезло в контекст — это не падение Ollama
            fails += 1
            if fails >= 3:
                sys.exit("Ollama не отвечает три раза подряд — остановка")
            continue
        fails = 0
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"aid": aid, "step": step, "version": version, "answer": answer,
                                "seconds": sec}, ensure_ascii=False) + "\n")
    print(f"{i + 1}/{len(cases)} {aid} {step}", flush=True)
print("DONE", flush=True)
