import logging
import queue
import signal
import threading
import time

import config
import logs

# Сколько ждать текущий разбор при остановке. Он уже потратил на событие
# минуты машинного времени, и обрывать его на полуслове — выбросить их зря.
# База в это время ещё открыта, поэтому карточка успевает записаться.
SHUTDOWN_WAIT = 45
from frigate import EventTracker, FrigateListener, last_appearance
from storage import Database
from storage.retention import Retention
from vlm import EventAnalyzer, RecheckWorker

log = logging.getLogger("main")


def warm_memory(finished, analyzer, db):
    """Один раз при старте: если система не помнит, где человек, — спрашиваем
    архив Frigate.

    Отдельным потоком, потому что запрос к Frigate и разбор ролика занимают
    десятки секунд, а слушатель MQTT в это время должен уже работать: живое
    событие важнее архивного.

    Найденное событие кладём в тот же стек, что и живые. Разбирает его тот же
    воркер, тем же путём, и память заполняется обычным ходом — отдельной
    ветки кода для этого не нужно.
    """
    if not config.LOOKBACK:
        return
    known = db.last_seen() if db else None
    if known:
        log.info("память не пуста (%s, %s) — архив Frigate не трогаем",
                 known["seen_at"], known["summary"])
        return

    log.info("память пуста — спрашиваю у Frigate, когда он последний раз "
             "видел %s", config.TARGET_IDENTITY)
    person = last_appearance()
    if person is None:
        log.info("в архиве Frigate наблюдаемого за последние %d ч нет — "
                 "память заполнится с первого живого события",
                 config.LOOKBACK_HOURS)
        return

    when = time.strftime("%d.%m %H:%M:%S", time.localtime(person.start_time))
    log.info("АРХИВ: последний раз его видели %s на %s (%s), зоны %s — "
             "отправляю на разбор", when, person.camera,
             person.identity_source, person.zone_route or "вне зон")
    finished.put(person)


def heartbeat(finished, analyzer, tracker):
    """Раз в N минут напоминает, что система жива, и показывает стек."""
    while True:
        time.sleep(config.LOG_HEARTBEAT_MIN * 60)
        log.info("состояние: в стеке %d, событий в работе %d, разбор %s",
                 finished.qsize(), len(tracker.people),
                 "идёт" if analyzer.thread and analyzer.thread.is_alive() else "СТОИТ")
        known = analyzer.last_seen
        if known:
            minutes = int((time.time() - (known.get("seen_ts") or 0)) // 60)
            log.info("последний раз его видели %d мин назад: %s, %s",
                     minutes, known.get("zone_name") or "вне зон",
                     known.get("posture") or "поза неизвестна")
        else:
            log.info("последний раз его видели: ещё ни разу")


def stop_politely(signum, frame):
    """SIGTERM — это то, чем Home Assistant останавливает аддон.

    Без этого обработчика Python убивал процесс на месте: блок finally не
    выполнялся, текущий разбор обрывался, а весь стек непросмотренных
    событий исчезал молча — на каждом перезапуске и на каждом обновлении
    аддона. Переводим сигнал в тот же путь, которым идёт Ctrl+C.
    """
    log.info("получен сигнал %s — останавливаюсь", signum)
    raise KeyboardInterrupt


if __name__ == "__main__":
    logs.setup()
    signal.signal(signal.SIGTERM, stop_politely)
    log.info("=" * 62)
    log.info("старт: цель %s, камеры %s, модель %s",
             config.TARGET_IDENTITY, config.CAMERAS or "все", config.VLM_MODEL)

    listener = FrigateListener(
        config.MQTT_HOST,
        config.MQTT_PORT,
        config.MQTT_USER,
        config.MQTT_PASSWORD,
    )
    listener.start()

    tracker = EventTracker(config.TARGET_IDENTITY, config.CAMERAS)
    # СТЕК БЕЗ ПРЕДЕЛА. Здесь стоял maxsize=QUEUE_SIZE, и при переполнении
    # самое старое событие выбрасывалось. Терять событие нельзя ни при каких
    # обстоятельствах: это запись о живом человеке, и «модель не успевает» —
    # наша беда, а не повод стереть её.
    #
    # Памяти это не стоит почти ничего: в стеке лежат не кадры и не видео, а
    # короткие описания события — несколько килобайт каждое. Тысяча
    # отставших событий это единицы мегабайт, тогда как их видео уже лежат
    # на диске.
    #
    # Порядок остался LIFO, и намеренно: свежее событие разбирается первым,
    # потому что тревога важна сейчас, а не через час. Отставшие никуда не
    # деваются и разберутся, когда очередь дойдёт.
    finished = queue.LifoQueue()

    db = Database()
    analyzer = EventAnalyzer(finished, db)
    analyzer.start()

    # Переоценка идёт своим потоком и своей очередью. В общий стек её класть
    # нельзя: живое событие — это тревога, и она не должна ждать, пока мы
    # по чьей-то просьбе пересматриваем вчерашнюю запись.
    rechecker = RecheckWorker(analyzer, db)
    rechecker.start()

    # Срок хранения: раз в час стирает из базы и с диска всё старше
    # RETENTION_DAYS суток. Своим потоком — уборка не должна задерживать
    # ни живой разбор, ни переоценку.
    retention = Retention(db)
    retention.start()

    threading.Thread(target=heartbeat, args=(finished, analyzer, tracker),
                     daemon=True).start()
    threading.Thread(target=warm_memory, args=(finished, analyzer, db),
                     daemon=True).start()

    try:
        while True:
            topic, data = listener.events.get()
            person = tracker.handle(topic, data)
            if person is None:
                continue

            finished.put(person)
            waiting = finished.qsize()
            log.info("ПОЛОЖЕНО В СТЕК %s  (в стеке %d)",
                     person.event_id.split("-")[-1], waiting)
            # Ничего не выбрасываем, но и молчать о заторе нельзя: если
            # отставших набралось столько, разбор не успевает за домом, и
            # это надо видеть в логе, а не узнавать по пропавшим записям.
            if waiting >= config.QUEUE_SIZE:
                log.warning("В СТЕКЕ %d СОБЫТИЙ — разбор не успевает за "
                            "событиями. Ничего не потеряно, но свежие "
                            "записи ждут дольше обычного", waiting)
    except KeyboardInterrupt:
        log.info("остановка по Ctrl+C")
    except Exception:
        log.exception("ГЛАВНЫЙ ЦИКЛ УПАЛ")
    finally:
        listener.stop()
        retention.stop()
        rechecker.stop()
        analyzer.stop()

        # Ждём текущий разбор, и только потом закрываем базу. Раньше база
        # закрывалась сразу, и разбор, шедший в этот момент, падал на
        # закрытом соединении — событие пропадало вместе с ним.
        if analyzer.thread and analyzer.thread.is_alive():
            log.info("жду, пока закончится текущий разбор (до %d с)",
                     SHUTDOWN_WAIT)
            analyzer.thread.join(timeout=SHUTDOWN_WAIT)
            if analyzer.thread.is_alive():
                log.warning("разбор не уложился в %d с — событие, которое он "
                            "разбирал, останется без карточки", SHUTDOWN_WAIT)

        # Стек живёт только в памяти процесса: то, что в нём осталось, не
        # переживёт остановку. Молчать об этом нельзя — иначе события
        # пропадают без следа в логе.
        left = finished.qsize()
        if left:
            log.warning("В СТЕКЕ ОСТАЛОСЬ %d НЕРАЗОБРАННЫХ СОБЫТИЙ. Стек "
                        "хранится в памяти, при остановке они теряются", left)

        db.close()
        log.info("остановлено")
