import base64
import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

import config
from frigate.geometry import event_geometry

from . import crop, grid, preview
from .steps import (CHAIN, VALIDATE, is_moving, lying_place_where,
                    toilet_by_zone, zone_name)

log = logging.getLogger("vlm")

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# Ниже этого прореживать кадры бессмысленно: по трём кадрам уже не видно
# ни походки, ни того, что человек делал руками.
MIN_STEP_FRAMES = 3


class EventAnalyzer:
    """Разбирает событие цепочкой отдельных запросов к модели.

    Сначала отдельный запрос-проверка: есть ли наблюдаемый на кадрах.
    Если нет — событие закрывается сразу, остальные вопросы не задаются.
    Дальше по одному запросу на каждый вопрос, который уместен по датчикам:
    ответы копятся в одной карточке, и она целиком уходит в базу.
    """

    def __init__(self, events, db=None):
        self.events = events
        self.db = db
        self.thread = None
        self.stopping = False

        # Где его видели в последний раз. Переживает перезапуск: лежит в базе,
        # поэтому после рестарта система помнит, что дед ушёл в туалет.
        self.last_seen = db.last_seen() if db else None
        if self.last_seen:
            log.info("память: последний раз его видели %s — %s",
                     self.last_seen["seen_at"], self.last_seen["summary"])

        self.prompts = self._load_prompts()
        self.prompt_sha = hashlib.sha1(
            "".join(self.prompts[k] for k in sorted(self.prompts)).encode()
        ).hexdigest()[:8]

        # Почему сорвался последний запрос: "context" — не влезло в окно
        # модели, такое лечится уменьшением числа кадров и повтором.
        self.last_error = None

        self.reference = self._load_reference()
        if not self.reference:
            log.warning("папка %s пуста — эталонных фото нет, модель будет узнавать "
                        "деда только по описанию", config.REFERENCE_DIR)

    def start(self):
        self.stopping = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        model = (f"{config.VLM_LLAMA_MODEL} (llama, кадры склеиваются в сетку)"
                 if config.VLM_BACKEND == "llama" else config.VLM_MODEL)
        log.info("разбор запущен: %s, %d эталонных фото, %d шагов в цепочке",
                 model, len(self.reference), len(CHAIN) + 1)

    def stop(self):
        self.stopping = True

    def _loop(self):
        while not self.stopping:
            try:
                person = self.events.get(timeout=1)
            except queue.Empty:
                continue
            log.info("ВЗЯТО ИЗ СТЕКА %s  (осталось в стеке %d)",
                     person.event_id.split("-")[-1], self.events.qsize())
            try:
                self.analyze(person)
            except Exception:
                log.exception("РАЗБОР СОРВАЛСЯ для %s", person.event_id)
                self._rescue(person)

    def _rescue(self, person):
        """Разбор сорвался на неожиданной ошибке — событие не должно пропасть.

        К этому месту оно уже вынуто из стека, и вернуть его туда нельзя:
        ошибка повторится, стек закрутится на ней и живые события встанут
        за нею в очередь. Кладём его туда, где оно точно дождётся, — строкой
        в базе и задачей на переоценку. Воркер переоценки возьмёт её своим
        ходом, отдельным потоком, никому не мешая.

        Тем же путём уже идёт событие, у которого не скачалось видео: это
        не новый механизм, а тот же самый.
        """
        short = person.event_id.split("-")[-1]
        if not self.db:
            log.error("%s: базы нет — СОБЫТИЕ ПОТЕРЯНО", short)
            return
        try:
            folder = self._event_folder(person)
            self.db.save_event(person, folder, self._day_part(person),
                               clip_path=os.path.join(folder, "clip.mp4"),
                               frames_dir=os.path.join(folder, "frames"))
            self.db.request_recheck(person.event_id, author="разбор сорвался")
            log.info("%s: событие сохранено и поставлено на переоценку — "
                     "не потеряно", short)
        except Exception:
            log.exception("%s: СОБЫТИЕ ПОТЕРЯНО — не удалось сохранить его "
                          "после срыва разбора", short)

    # ---------- разбор события ----------

    def analyze(self, person):
        started = time.time()
        short = person.event_id.split("-")[-1]
        folder = self._event_folder(person)
        frames_dir = os.path.join(folder, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        frames = self._prepare_frames(person, folder, frames_dir)
        if not frames:
            # СОБЫТИЕ НЕ ВЫБРАСЫВАЕМ. Раньше здесь стояло удаление папки и
            # return: событие исчезало насовсем, и по логу таких было 431 из
            # 928. Но «мы не смогли забрать видео» — это наша беда, а не
            # признак того, что события не было.
            #
            # Поэтому строку в базе заводим и ставим запись в очередь
            # переоценки: воркер вернётся к ней позже, когда Frigate
            # допишет запись, и разберёт обычным ходом.
            log.error("%s: КАДРОВ НЕТ (нет клипа, не скачался или ffmpeg не "
                      "отработал) — событие СОХРАНЕНО и поставлено на "
                      "переоценку, не выброшено", short)
            if self.db:
                self.db.save_event(person, folder, self._day_part(person),
                                   clip_path=os.path.join(folder, "clip.mp4"),
                                   frames_dir=frames_dir)
                self.db.request_recheck(person.event_id, author="видео не пришло")
            return None
        log.info("%s: нарезано %d кадров, папка %s", short, len(frames), folder)

        card = {"moving": is_moving(person)}
        trace = []

        # шаг 1 — отдельный запрос: он вообще на кадрах? Пока не подтверждено,
        # карточку в базу не заводим: подтвердится — заведём, нет — стирать нечего.
        #
        # Проверку можно выключить настройкой. Тогда цепочка верит Frigate,
        # который уже опознал человека, и карточки не удаляются никогда.
        if not config.VALIDATE_SUBJECT:
            log.info("%s: проверка наблюдаемого ВЫКЛЮЧЕНА — верим Frigate "
                     "(%s, %s), карточку не удаляем", short,
                     person.identity, person.identity_source)
            card["subject_found"] = True
            answer = True
        else:
            answer = self._run_step(VALIDATE, person, card, frames, folder,
                                    trace, short)
        if answer is None:
            log.error("%s: проверка без ответа — это не «не он», а «неизвестно». "
                      "Карточку заводим без разбора, её можно перепроверить", short)
            if self.db:
                self.db.save_event(person, folder, self._day_part(person),
                                   clip_path=os.path.join(folder, "clip.mp4"),
                                   frames_dir=frames_dir)
                self.db.request_recheck(person.event_id, author="разбор не удался")
            return None
        if not card.get("subject_found"):
            # Ответ «нет» стоит дорого: карточка удаляется насовсем. Прежде чем
            # стирать, показываем ОСТАЛЬНЫЕ кадры — те, которых проверка не
            # видела. Человек мог войти в кадр в середине события или всё это
            # время стоять спиной. Удаляем, только если и второй заход сказал
            # «нет».
            rest = [f for f in frames if f not in self._pick(frames, VALIDATE.frames)]
            if rest:
                log.info("%s: первый заход наблюдаемого не увидел — "
                         "переспрашиваю по остальным %d кадрам", short, len(rest))
                second = self._run_step(VALIDATE, person, card, frames, folder,
                                        trace, short,
                                        chosen=self._spread(rest, config.VALIDATE_FRAMES),
                                        tag="_2")
                if second is not None and card.get("subject_found"):
                    log.info("%s: со второго захода наблюдаемый НАЙДЕН — "
                             "карточка остаётся", short)
            if not card.get("subject_found"):
                self._forget(person, folder, short, card.get("match"))
                return None

        if self.db:
            self.db.save_event(person, folder, self._day_part(person),
                               clip_path=os.path.join(folder, "clip.mp4"),
                               frames_dir=frames_dir)
            log.info("%s: наблюдаемый подтверждён, карточка заведена в базе", short)

        # шаги 2..N — каждый вопрос отдельно и только если есть повод
        for step in CHAIN:
            if not step.needed(person, card):
                log.debug("%s: шаг «%s» пропущен — повода нет", short, step.title)
                continue
            self._run_step(step, person, card, frames, folder, trace, short)

        result = self._finish(person, card, trace, frames, started)
        log.info("%s: ГОТОВО ЗА %.1f с, задано %d запросов — %s (опасность %s)",
                 short, result["analyzed_in_s"], len(trace),
                 result["summary"], result["danger"])

        self._write_trace(folder, trace)

        if self.db:
            result["analysis_id"] = self.db.save_analysis(
                person.event_id, result, {t["step"]: t["answer"] for t in trace},
                config.VLM_MODEL, self.prompt_sha, config.FRAME_WIDTH,
                len(self.reference))
            log.info("%s: СОХРАНЕНО В БАЗУ, разбор #%s", short, result["analysis_id"])

        self._remember(person, result, folder, short)

        # Лёгкая копия для сайта. В самом конце и отдельным потоком: карточка
        # уже в базе, тревога уже поднята, а перекодирование стоит десятки
        # секунд — держать на нём стек с непросмотренными событиями нельзя.
        preview.request(folder, short)
        return result

    # ---------- переоценка: спросить модель заново по той же записи ----------

    def reanalyze(self, event_id):
        """Пересобрать разбор существующего события. Возвращает result или None.

        Отличий от первого разбора три, и все намеренные.

        Кадры режутся ЗАНОВО из сохранённого клипа. Переоценка чаще всего и
        нужна потому, что изменились настройки или промпт: на старых кадрах
        ответ был бы тот же самый.

        Строка события не заводится и не удаляется. Она уже есть, меняется
        только разбор. Даже если проверка теперь скажет «это не он» —
        карточку не стираем: человек просил пересмотреть, а не удалить.

        Разбор ДОПИСЫВАЕТСЯ новой строкой в analyses, а не затирает старую.
        По прошлому ответу видно, что именно модель передумала.
        """
        from .recheck import PersonFromRow

        row = self.db.event(event_id) if self.db else None
        if not row:
            log.error("переоценка %s: события нет в базе", event_id)
            return None

        person = PersonFromRow(row)
        short = event_id.split("-")[-1]
        folder = row["folder"]
        if not folder:
            log.error("%s: переоценка невозможна — у события нет папки", short)
            return None
        os.makedirs(folder, exist_ok=True)

        # Записи может не быть на диске вовсе: событие сохранено именно
        # потому, что видео тогда не пришло. Пробуем забрать его сейчас —
        # к этому времени Frigate обычно уже дописал запись.
        clip = os.path.join(folder, "clip.mp4")
        if not os.path.isfile(clip):
            log.info("%s: записи на диске нет — пробую скачать заново", short)
            if not person.clip_url or not self._download(person.clip_url, clip):
                log.error("%s: переоценка невозможна — запись так и не пришла",
                          short)
                return None

        started = time.time()
        frames_dir = os.path.join(folder, "frames")
        self._clear_frames(frames_dir, os.path.join(folder, "frames_wide"))
        os.makedirs(frames_dir, exist_ok=True)

        frames = self._cut_frames(person, folder, frames_dir)
        if not frames:
            log.error("%s: переоценка — кадры не нарезались", short)
            return None
        log.info("%s: переоценка, заново нарезано %d кадров", short, len(frames))

        card = {"moving": is_moving(person)}
        trace = []

        if config.VALIDATE_SUBJECT:
            self._run_step(VALIDATE, person, card, frames, folder, trace, short)
            if card.get("subject_found") is False:
                log.info("%s: переоценка говорит «это не он» — карточку всё равно "
                         "оставляем, просили пересмотреть, а не удалить", short)
        else:
            card["subject_found"] = True

        for step in CHAIN:
            if step.needed(person, card):
                self._run_step(step, person, card, frames, folder, trace, short)

        result = self._finish(person, card, trace, frames, started)
        self._write_trace(folder, trace)

        if self.db:
            result["analysis_id"] = self.db.save_analysis(
                event_id, result, {t["step"]: t["answer"] for t in trace},
                config.VLM_MODEL, self.prompt_sha, config.FRAME_WIDTH,
                len(self.reference))
            log.info("%s: переоценка сохранена, разбор #%s",
                     short, result["analysis_id"])

        # Память трогаем тем же путём, что и живой разбор: save_last_seen сам
        # откажется, если это событие старше запомненного.
        self._remember(person, result, folder, short)
        return result

    @staticmethod
    def _clear_frames(*dirs):
        """Старые кадры убираем перед новой нарезкой: иначе ffmpeg перезапишет
        только первые N, а хвост от прошлого раза останется и уедет в модель
        вперемешку со свежими.

        Широкие кадры (frames_wide) чистим тем же вызовом и по той же
        причине: переоценка чаще всего и нужна потому, что настройки
        изменились, а старые кадры ответили бы то же самое."""
        for frames_dir in dirs:
            if not os.path.isdir(frames_dir):
                continue
            for name in os.listdir(frames_dir):
                if name.startswith("frame_") and name.endswith(".jpg"):
                    try:
                        os.remove(os.path.join(frames_dir, name))
                    except OSError:
                        pass

    def _forget(self, person, folder, short, match):
        """Проверка не подтвердила деда — карточки быть не должно.

        Стираем и строку в базе (её могли завести прошлым разбором этого же
        события), и папку целиком: видео, кадры, промпты. Это не наш человек,
        хранить нечего."""
        log.info("%s: наблюдаемого на кадрах НЕТ (%s) — карточку удаляем",
                 short, match or "без пояснения")
        if self.db:
            gone = self.db.forget_event(person.event_id)
            if gone:
                log.info("%s: строка события удалена из базы", short)
            # forget_event мог перевести память на предыдущее появление
            self.last_seen = self.db.last_seen()
            self._write_last_seen(self.last_seen)
        self._remove_folder(folder, short)

    @staticmethod
    def _remove_folder(folder, short):
        """Сносим только то, что лежит внутри DATA_DIR: страховка от того,
        чтобы кривой путь события не увёл rmtree куда-нибудь ещё."""
        root = os.path.abspath(config.DATA_DIR)
        target = os.path.abspath(folder)
        if not target.startswith(root + os.sep):
            log.error("%s: папка %s вне %s — НЕ удаляю", short, target, root)
            return
        try:
            shutil.rmtree(target)
            log.info("%s: папка события удалена: %s", short, target)
        except FileNotFoundError:
            pass
        except Exception as error:
            log.error("%s: папку %s удалить не вышло: %s: %s",
                      short, target, type(error).__name__, error)

    # ---------- память: где его видели в последний раз ----------

    def _remember(self, person, result, folder, short):
        """Отдельная короткая карточка «последний раз»: одна строка в базе и
        один файл рядом с ней. Историю она не заменяет — она отвечает на другой
        вопрос: где он может быть прямо сейчас."""
        route = person.zone_route_list
        zone = route[-1] if route else None
        card = {
            "identity": person.identity or config.TARGET_IDENTITY,
            "event_id": person.event_id,
            "analysis_id": result.get("analysis_id"),
            "seen_at": result.get("started"),
            "seen_ts": person.start_time,
            "camera": person.camera,
            "zone": zone,
            "zone_name": self._zone(zone) if zone else None,
            "zone_route": result.get("route"),
            "posture": result.get("posture"),
            # on_what и action остаются колонками ради старых записей, но
            # шагов, которые их заполняли, больше нет: «на чём» перестали
            # спрашивать у позы, а шаг «что делает» убран целиком.
            "on_what": None,
            "action": None,
            "sleeping": result.get("sleeping"),
            "toilet": result.get("toilet"),
            "danger": result.get("danger"),
            "summary": result.get("summary"),
            "folder": folder,
            "frame_url": person.snapshot_clean_url,
        }
        if self.db and not self.db.save_last_seen(card):
            return                      # событие старше запомненного, память не трогаем
        self.last_seen = dict(card, seen_ts=card["seen_ts"] or 0)
        self._write_last_seen(card)
        log.info("%s: ЗАПОМНЕНО, где он: %s, %s (%s)", short,
                 card["zone_name"] or "вне зон", card["posture"], card["seen_at"])

    @staticmethod
    def _write_last_seen(card):
        """Файл пишем через временный: иначе читающий снаружи поймает половину.
        Памяти не стало — файла тоже быть не должно, иначе он врёт."""
        path = config.LAST_SEEN_FILE
        if not path:
            return
        if card is None:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception as error:
                log.error("НЕ УДАЛЁН ФАЙЛ ПАМЯТИ %s: %s", path, error)
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        except Exception as error:
            log.error("НЕ ЗАПИСАЛСЯ ФАЙЛ ПАМЯТИ %s: %s: %s",
                      path, type(error).__name__, error)

    def _memory_lines(self, person):
        """Строки о прошлом разе — только если он и правда был раньше этого
        события: из стека события приходят не по порядку времени."""
        known = self.last_seen
        if not known or not known.get("seen_ts") or not person.start_time:
            return []
        gap = person.start_time - known["seen_ts"]
        if gap <= 0:
            return []
        ago = (f"{int(gap // 60)} мин назад" if gap >= 60
               else f"{int(gap)} с назад")
        lines = [f"ПРОШЛЫЙ РАЗ (этого НЕТ на кадрах, это память системы): "
                 f"{ago} его видели там: {known.get('zone_name') or 'вне зон'}, "
                 f"{known.get('posture') or 'поза неизвестна'}"]
        if known.get("toilet") == "зашёл":
            lines.append("ТОГДА ОН ЗАШЁЛ В ТУАЛЕТ и с тех пор не появлялся")
        if known.get("sleeping"):
            lines.append("ТОГДА ОН СПАЛ")
        return lines

    def _run_step(self, step, person, card, frames, folder, trace, short,
                  chosen=None, tag=""):
        """Один вопрос: собрать промпт, показать нужные кадры, разложить ответ.

        chosen — явный список кадров, если шаг задаётся повторно по другим
        кадрам; tag — приписка к именам файлов, чтобы второй заход не затирал
        первый."""
        if step.wide:
            whole = self._wide_frames(person, folder, short)
            if whole:
                frames = whole
        if chosen is None:
            chosen = self._pick(frames, step.frames)
        refs = self.reference if step.reference else []
        images, prompt = self._request(step, person, card, refs, chosen)
        with open(os.path.join(folder, f"prompt_{step.name}{tag}.txt"),
                  "w", encoding="utf-8") as f:
            f.write(prompt)

        model = self._model_for(step)
        log.info("%s: ЗАПРОС «%s» — %d кадров%s%s%s",
                 short, step.title, len(chosen),
                 f" + {len(refs)} эталонов" if refs else "",
                 ", склеены в одну картинку" if self._one_image() else "",
                 f", модель {model}" if model != config.VLM_MODEL else "")

        began = time.time()
        answer = self._ask(prompt, images, model)

        # Не влезло в окно модели — это не повод терять событие целиком.
        # Показываем половину кадров и спрашиваем снова: ответ по шести кадрам
        # хуже, чем по двенадцати, но неизмеримо лучше, чем никакого.
        while (answer is None and self.last_error == "context"
               and len(chosen) > MIN_STEP_FRAMES):
            chosen = self._thin(chosen)
            log.warning("%s: «%s» не влезло в контекст — повторяю на %d кадрах",
                        short, step.title, len(chosen))
            images, prompt = self._request(step, person, card, refs, chosen)
            answer = self._ask(prompt, images, model)

        spent = round(time.time() - began, 1)
        if answer is None:
            log.error("%s: шаг «%s» без ответа за %.1f с — идём дальше без него",
                      short, step.title, spent)
            trace.append({"step": step.name + tag, "title": step.title,
                          "seconds": spent, "frames": len(chosen),
                          "answer": None, "failed": True})
            return None

        step.apply(card, answer)
        log.info("%s: «%s» за %.1f с -> %s", short, step.title, spent,
                 json.dumps(answer, ensure_ascii=False)[:200])
        trace.append({"step": step.name + tag, "title": step.title,
                      "seconds": spent, "frames": len(chosen),
                      "answer": answer, "failed": False})
        with open(os.path.join(folder, f"answer_{step.name}{tag}.json"),
                  "w", encoding="utf-8") as f:
            json.dump(answer, f, ensure_ascii=False, indent=2)
        return answer

    def _finish(self, person, card, trace, frames, started):
        """Складывает карточку в тот вид, в котором её ждут база и сайт."""
        danger, reason = self._danger(person, card)
        route = self._route(person)

        # Модель входа не увидела, а зона Frigate сработала. Это не повод
        # молчать: зона — факт, а не мнение, и человек, своими глазами
        # видевший поход в туалет, должен найти этому след в карточке.
        # Пишем осторожно — «у двери»: датчики знают, что он там был, и не
        # знают, вошёл ли он внутрь. «Зашёл» по-прежнему говорит только
        # модель.
        if not card.get("toilet"):
            near = toilet_by_zone(person)
            if near:
                card["toilet"] = near
                log.info("%s: модель входа в туалет не подтвердила, но зона "
                         "сработала — ставлю «%s»",
                         person.event_id.split("-")[-1], near)
        result = {
            **person.vlm_payload(),
            "route": route,
            "subject_found": True,
            "moving": card.get("moving"),
            # posture — то, В ЧЁМ ОН ОСТАЛСЯ: по нему считается вид события и
            # «где он сейчас». postures — весь путь положений за событие:
            # прошёл, посидел, снова встал.
            "posture": card.get("posture") or "не видно",
            "postures": card.get("postures") or [],
            "quality": card.get("quality"),
            "gait": card.get("gait"),
            "gait_risk": card.get("gait_risk"),
            "gait_support": card.get("gait_support"),
            "sleeping": card.get("sleeping"),
            "sleep_moves": card.get("sleep_moves"),
            "sleep_head": card.get("sleep_head"),
            "eating": card.get("eating"),
            "drinking": card.get("drinking"),
            "meds": card.get("meds"),
            "toilet": card.get("toilet"),
            "fallen": card.get("fallen"),
            "danger": danger,
            "danger_reason": reason,
            "summary": self._summary(route, card),
            "steps": trace,
            "steps_asked": len(trace),
            "frames_used": len(frames),
            "analyzed_in_s": round(time.time() - started, 1),
        }
        return result

    @staticmethod
    def _summary(route, card):
        # Положений за событие может быть несколько — «прошёл, сел» говорит
        # о восемнадцати минутах куда больше, чем одно слово «идёт».
        seen = card.get("postures") or []
        where = ", ".join(seen) if seen else (card.get("posture") or "не видно")
        parts = [route or "вне зон", where]
        if card.get("sleeping"):
            parts.append("спит")
        if card.get("fallen"):
            parts.append("УПАЛ")
        if card.get("toilet"):
            parts.append(f"туалет: {card['toilet']}")
        if card.get("eating"):
            parts.append("ест")
        if card.get("drinking"):
            parts.append("пьёт")
        if card.get("meds"):
            parts.append("лекарства")
        if card.get("gait_risk") and card["gait_risk"] != "норма":
            parts.append(f"походка: {card['gait_risk']}")
        return " | ".join(parts)

    @staticmethod
    def _danger(person, card):
        """Уровень тревоги — решение кода. Модель даёт для него наблюдения."""
        # Падение — отдельный вопрос с двумя ответами, и он идёт первым в
        # цепочке. Никаких «средних» падений и поверхностей: упал — тревога.
        if card.get("fallen"):
            return "высокая", "модель видит падение"
        if card.get("gait_risk") == "вот-вот упадёт":
            return "высокая", "теряет равновесие на ходу"
        if card.get("gait_risk") == "шатается":
            return "средняя", "походка нетвёрдая: " + (card.get("gait") or "шаткая")
        if card.get("toilet_steady") == "шатко":
            return "средняя", "вышел из туалета нетвёрдо — тут чаще всего падают"
        route = person.zone_route_list
        if route and route[-1] in config.EXIT_ZONES:
            return "средняя", f"ушёл через зону «{zone_name(route[-1])}»"
        if card.get("sleeping"):
            # Спит сидя — это отдельная новость, а не то же, что спит в постели:
            # так засыпают от усталости или когда нехорошо.
            if card.get("posture") == "сидит":
                return "норма", "заснул сидя"
            return "норма", "спит " + lying_place_where(person)
        if person.still_seconds > config.DANGER_STILL_MINUTES * 60:
            return "низкая", f"не двигался {int(person.still_seconds // 60)} мин"
        return "норма", ""

    # ---------- кадры и контекст ----------

    @staticmethod
    def _one_image():
        """Llama 3.2 Vision в Ollama берёт одно изображение на запрос —
        кадры для неё склеиваются в сетку (vlm/grid.py)."""
        return config.VLM_BACKEND == "llama"

    def _request(self, step, person, card, refs, chosen):
        """Картинки и текст запроса. Для Qwen — кадры по одному, эталоны
        первыми; для Llama — одна сетка, а в промпте сказано, как её читать."""
        if self._one_image():
            image, shape = grid.build(refs, chosen)
            layout_line = grid.context_line(len(refs), len(chosen), shape)
            images = [image]
        else:
            layout_line = None
            images = [self._encode(p) for p in refs] + [self._encode(f) for f in chosen]
        prompt = self.prompts[step.name].replace(
            "{{CONTEXT}}", self._context(person, step, card, len(chosen), layout_line))
        return images, prompt

    @staticmethod
    def _model_for(step):
        """Опознание отдаём тяжёлой модели, если она задана: 4b на этом вопросе
        не работает. Остальные шаги — быстрой, им хватает.

        В режиме llama модель одна на все шаги: своей «тяжёлой» у неё нет."""
        if config.VLM_BACKEND == "llama":
            return config.VLM_LLAMA_MODEL
        if step.heavy and config.VLM_VALIDATE_MODEL:
            return config.VLM_VALIDATE_MODEL
        return config.VLM_MODEL

    @staticmethod
    def _thin(frames):
        """Половина кадров, но равномерно по времени: выбрасывать хвост нельзя —
        в нём как раз то, чем событие закончилось."""
        keep = max(MIN_STEP_FRAMES, len(frames) // 2)
        step = len(frames) / keep
        return [frames[min(len(frames) - 1, int(i * step))] for i in range(keep)]

    @staticmethod
    def _pick(frames, mode):
        """Сколько кадров показать шагу. Весь ролик нужен там, где важно
        движение; про позу и про дверь спрашивают по краю события; проверке
        хватает нескольких кадров, но обязательно по всей длине."""
        if mode == "spread":
            return EventAnalyzer._spread(frames, config.VALIDATE_FRAMES)
        limit = max(1, config.STEP_FRAMES)
        if mode == "all" or len(frames) <= limit:
            return frames
        if mode == "first":
            return frames[:limit]
        return frames[-limit:]

    @staticmethod
    def _spread(frames, count):
        """Ровно count кадров, растянутых по всей длине события, включая первый
        и последний. Брать подряд нельзя: человек мог появиться в середине."""
        count = max(1, min(count, len(frames)))
        if count == 1:
            return [frames[len(frames) // 2]]
        step = (len(frames) - 1) / (count - 1)
        return [frames[round(i * step)] for i in range(count)]

    def _context(self, person, step, card, scene_frames, layout_line=None):
        part = self._day_part(person)
        lines = []
        if layout_line:
            lines.append(layout_line)
        elif step.reference:
            lines.append(f"СОСТАВ КАДРОВ: первых кадров эталонных — "
                         f"{len(self.reference)}, дальше {scene_frames} кадров "
                         f"сцены по порядку времени")
        else:
            lines.append(f"СОСТАВ КАДРОВ: {scene_frames} кадров сцены "
                         f"по порядку времени")
        lines += [
            f"ВНЕШНОСТЬ НАБЛЮДАЕМОГО: {config.TARGET_DESCRIPTION}",
            f"ВРЕМЯ СУТОК: {part}",
            f"ДЛИТЕЛЬНОСТЬ: {person.duration} с, из них двигался "
            f"{person.path_seconds} с, не двигался {person.still_seconds} с",
        ]
        if person.near_edge:
            lines.append("ВНИМАНИЕ: под конец человек прижат к краю кадра, "
                         "он мог просто выйти из кадра")
        if step.memory:
            lines += self._memory_lines(person)
        lines += step.facts(person, card)
        return "\n".join(lines)

    def _event_folder(self, person):
        stamp = time.localtime(person.start_time or time.time())
        day = time.strftime("%Y-%m-%d", stamp)
        moment = time.strftime("%H-%M-%S", stamp)
        tail = person.event_id.split("-")[-1]
        return os.path.join(config.DATA_DIR, day, f"{moment}_{person.camera}_{tail}")

    @staticmethod
    def _write_trace(folder, trace):
        with open(os.path.join(folder, "steps.json"), "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _day_part(person):
        hour = int(time.strftime("%H", time.localtime(person.start_time or time.time())))
        return ("ночь" if hour < 6 else "утро" if hour < 12
                else "день" if hour < 18 else "вечер")

    def _prepare_frames(self, person, folder, frames_dir):
        """Нарезка кадров: по человеку, а не по всему кадру.

        Порядок фильтров важен: сначала fps (сколько кадров взять), потом
        crop (вырезать человека), и только потом scale (ограничить ширину).
        Резать после сжатия бессмысленно — резать будет уже нечего.
        """
        if not person.clip_url:
            return []
        clip = os.path.join(folder, "clip.mp4")
        if not self._download(person.clip_url, clip):
            return []
        return self._cut_frames(person, folder, frames_dir)

    def _cut_frames(self, person, folder, frames_dir):
        """Нарезка кадров из уже лежащего на диске клипа. Отдельно от
        скачивания: переоценке скачивать нечего, клип у неё уже есть."""
        clip = os.path.join(folder, "clip.mp4")
        seconds = self._clip_seconds(clip) or person.duration or 1
        fps = max(config.FRAMES_PER_EVENT / seconds, 0.1)

        short = person.event_id.split("-")[-1]
        chain = [f"fps={fps:.4f}"]

        if config.CROP:
            box, points = event_geometry(person.event_id)
            region = crop.region_for(points or person.points, box)
            cut = crop.ffmpeg_filter(region)
            if cut:
                chain.append(cut)
                log.info("%s: режем по человеку — %.0f%% ширины, %.0f%% высоты кадра",
                         short, region[2] * 100, region[3] * 100)
            else:
                log.info("%s: рамка человека не пришла — кадр целиком", short)
            # Ширина ограничивается СВЕРХУ и никогда не растягивается:
            # min(предел, iw). Растянуть кроп до предела значит выдумать
            # пиксели, которых в записи нет, и заплатить за них токенами.
            if config.FRAME_WIDTH:
                chain.append(f"scale='min({config.FRAME_WIDTH},iw)':-2")
        else:
            # Старый путь: весь кадр ужимается до заданной ширины.
            if config.FRAME_WIDTH:
                chain.append(f"scale={config.FRAME_WIDTH}:-2")
            log.info("%s: кадр целиком, ужат до %s px", short, config.FRAME_WIDTH)

        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", clip,
             "-vf", ",".join(chain),
             "-frames:v", str(config.FRAMES_PER_EVENT),
             "-q:v", "4", os.path.join(frames_dir, "frame_%02d.jpg")],
            check=False,
        )
        return self._frame_list(frames_dir)

    @staticmethod
    def _frame_list(frames_dir):
        if not os.path.isdir(frames_dir):
            return []
        return sorted(os.path.join(frames_dir, n) for n in os.listdir(frames_dir)
                      if n.startswith("frame_") and n.endswith(".jpg"))

    def _wide_frames(self, person, folder, short=""):
        """Кадры ЦЕЛИКОМ, без кропа по человеку. None — их не требуется.

        Кроп сделан ради мелочей: поза, руки, свесившаяся голова. Но есть
        вопросы, где нужна ровно обратная вещь — обстановка. «Он вошёл в
        дверь или просто ушёл за край кадра» решается по проёму и по
        границе комнаты, а кроп выносит за вырез и то, и другое. Хуже
        того: в обрезанном кадре человек уходит за край ВСЕГДА, потому что
        край едет за ним следом, — и правило промпта «пропал у края, а не у
        двери» перестаёт различать хоть что-нибудь.

        Поэтому такие шаги получают свой набор кадров, снятый целиком.
        Режем один раз на событие и складываем рядом: второй шаг про дверь
        возьмёт уже готовые.

        Если кроп выключен, обычные кадры и так целые — возвращаем None,
        и шаг смотрит их.
        """
        if not config.CROP or config.WIDE_FRAMES <= 0:
            return None
        wide_dir = os.path.join(folder, "frames_wide")
        ready = self._frame_list(wide_dir)
        if ready:
            return ready

        clip = os.path.join(folder, "clip.mp4")
        if not os.path.isfile(clip):
            log.warning("%s: записи нет — вопрос про дверь пойдёт по "
                        "обрезанным кадрам", short)
            return None

        os.makedirs(wide_dir, exist_ok=True)
        seconds = self._clip_seconds(clip) or person.duration or 1
        fps = max(config.WIDE_FRAMES / seconds, 0.1)
        chain = [f"fps={fps:.4f}"]
        if config.FRAME_WIDTH:
            chain.append(f"scale='min({config.FRAME_WIDTH},iw)':-2")

        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", clip,
             "-vf", ",".join(chain),
             "-frames:v", str(config.WIDE_FRAMES),
             "-q:v", "4", os.path.join(wide_dir, "frame_%02d.jpg")],
            check=False,
        )
        made = self._frame_list(wide_dir)
        log.info("%s: для вопроса про дверь нарезано %d кадров ЦЕЛИКОМ "
                 "(без кропа)", short, len(made))
        return made or None

    @staticmethod
    def _zone(name):
        return config.ZONE_NAMES.get(name, name)

    def _route(self, person):
        return " → ".join(self._zone(z) for z in person.zone_route_list)

    def _camera_zones(self, camera):
        zones = config.CAMERA_ZONES.get(camera)
        if not zones:
            return "список зон для этой камеры не задан"
        return ", ".join(self._zone(z) for z in zones)

    # ---------- разговор с моделью ----------

    def _ask(self, prompt, images, model=None):
        body = json.dumps({
            "model": model or config.VLM_MODEL,
            "prompt": prompt,
            "images": images,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_ctx": config.VLM_CONTEXT},
        }).encode()
        request = urllib.request.Request(f"{config.VLM_URL}/api/generate", body,
                                         {"Content-Type": "application/json"})
        self.last_error = None
        try:
            with urllib.request.urlopen(request, timeout=config.VLM_TIMEOUT) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            text = error.read().decode(errors="replace")[:300]
            if "context" in text:
                self.last_error = "context"
                log.error("МОДЕЛЬ ОТКАЗАЛА: не хватило контекста (сейчас "
                          "VLM_CONTEXT=%d). Картинки — почти весь запрос: один "
                          "кадр %d px весит около тысячи токенов. Ответ: %s",
                          config.VLM_CONTEXT, config.FRAME_WIDTH, text)
            else:
                self.last_error = "http"
                log.error("МОДЕЛЬ ОТКАЗАЛА (HTTP %s): %s", error.code, text)
            return None
        except urllib.error.URLError as error:
            log.error("МОДЕЛЬ НЕДОСТУПНА по адресу %s: %s. Проверь, запущена ли "
                      "Ollama и верен ли VLM_URL", config.VLM_URL, error.reason)
            return None
        except TimeoutError:
            log.error("МОДЕЛЬ НЕ УСПЕЛА за %d с. Либо она холодная и грузится с "
                      "диска, либо кадров слишком много", config.VLM_TIMEOUT)
            return None
        except Exception as error:
            log.error("МОДЕЛЬ НЕ ОТВЕТИЛА: %s: %s", type(error).__name__, error)
            return None

        try:
            answer = json.loads(payload.get("response", ""))
        except ValueError:
            log.error("МОДЕЛЬ ВЕРНУЛА НЕ JSON: %r", payload.get("response", "")[:300])
            return None
        return answer if isinstance(answer, dict) else None

    # ---------- файлы ----------

    def _load_prompts(self):
        """По файлу на шаг. Нет файла — шаг не задать, это ошибка настройки."""
        needed = [VALIDATE] + CHAIN
        prompts = {}
        missing = []
        for step in needed:
            path = os.path.join(config.PROMPTS_DIR, step.prompt_file)
            if not os.path.exists(path):
                missing.append(path)
                continue
            with open(path, encoding="utf-8") as f:
                prompts[step.name] = f.read()
        if missing:
            raise FileNotFoundError(
                "нет файлов промптов: " + ", ".join(missing) +
                ". Проверь PROMPTS_DIR (сейчас " + config.PROMPTS_DIR + ")")
        return prompts

    def _load_reference(self):
        if not os.path.isdir(config.REFERENCE_DIR):
            return []
        return [os.path.join(config.REFERENCE_DIR, n)
                for n in sorted(os.listdir(config.REFERENCE_DIR))
                if n.lower().endswith(IMAGE_SUFFIXES)]

    @staticmethod
    def _encode(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    @staticmethod
    def _download(url, path):
        """Сразу после конца события Frigate ещё не дописал сегменты записи и
        отвечает 400. Раньше событие из-за этого терялось целиком — теперь
        ждём и пробуем снова."""
        tries = max(1, config.CLIP_TRIES)
        for attempt in range(1, tries + 1):
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    with open(path, "wb") as f:
                        f.write(response.read())
                if attempt > 1:
                    log.info("видео забрали с %d-й попытки", attempt)
                return True
            except Exception as error:
                last = f"{type(error).__name__}: {error}"
                if attempt < tries:
                    log.warning("видео ещё не готово (%s), жду %d с и пробую "
                                "снова — попытка %d из %d",
                                last, config.CLIP_RETRY_WAIT, attempt + 1, tries)
                    time.sleep(config.CLIP_RETRY_WAIT)
        log.error("НЕ СКАЧАЛОСЬ ВИДЕО %s за %d попыток: %s", url, tries, last)
        return False

    @staticmethod
    def _clip_seconds(path):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=30,
            )
            return float(out.stdout.strip())
        except Exception:
            return None
