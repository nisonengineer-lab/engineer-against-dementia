#!/usr/bin/env python3
"""Добор лёгких копий для уже снятых событий.

Новые события получают preview.mp4 сами — его делает разбор. Всё, что снято
раньше, осталось с одним clip.mp4: 2880x1620, до ста мегабайт, а с уличной
камеры ещё и HEVC, который десктопный браузер не открывает вовсе. Этот
скрипт проходит по папкам событий и кладёт рядом лёгкую копию.

Работа не быстрая: примерно от четверти до половины длительности записи на
каждое событие, и это чистый процессор. Поэтому:

  * идём по одному, не параллелим — машина, на которой это крутится, ещё и
    дом обслуживает;
  * начинаем со свежих событий, их смотрят чаще;
  * готовые пропускаем, так что скрипт можно прервать и запустить снова.

    python3 backfill_previews.py                 посмотреть, сколько работы
    python3 backfill_previews.py --go            сделать
    python3 backfill_previews.py --go --limit 20 сделать первые двадцать
    python3 backfill_previews.py --go --nice     уступать процессор всему
"""
import argparse
import os
import sys
import time

import config
from vlm import preview


def folders():
    """Папки событий, свежие первыми: ГГГГ-ММ-ДД/ЧЧ-ММ-СС_камера_id."""
    root = config.DATA_DIR
    if not os.path.isdir(root):
        sys.exit(f"нет папки событий: {root}")
    out = []
    for day in sorted(os.listdir(root), reverse=True):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir):
            continue
        for name in sorted(os.listdir(day_dir), reverse=True):
            folder = os.path.join(day_dir, name)
            if os.path.isfile(os.path.join(folder, "clip.mp4")):
                out.append(folder)
    return out


def mb(path):
    try:
        return os.path.getsize(path) / 1e6
    except OSError:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="делать, а не только считать")
    ap.add_argument("--limit", type=int, default=0, help="сколько событий обработать")
    ap.add_argument("--force", action="store_true", help="переделать и готовые")
    ap.add_argument("--nice", action="store_true",
                    help="уступать процессор: медленнее, но дом не тормозит")
    args = ap.parse_args()

    if args.nice:
        try:
            os.nice(19)
        except OSError:
            pass

    all_folders = folders()
    todo = [f for f in all_folders if args.force or not preview.ready(f)]
    if args.limit:
        todo = todo[:args.limit]

    weight = sum(mb(os.path.join(f, "clip.mp4")) for f in todo)
    print(f"событий с записью: {len(all_folders)}")
    print(f"без лёгкой копии:  {len(todo)}  ({weight / 1024:.1f} ГБ оригиналов)")
    if not args.go:
        print("\nэто только подсчёт. Чтобы сделать: --go "
              "(можно прервать и запустить снова, готовые пропускаются)")
        return

    began = time.time()
    done = failed = 0
    saved = 0.0
    for n, folder in enumerate(todo, 1):
        short = os.path.basename(folder)
        was = mb(os.path.join(folder, "clip.mp4"))
        print(f"[{n}/{len(todo)}] {short}  {was:.0f} МБ … ", end="", flush=True)
        out = preview.make(folder, short, force=args.force)
        if out:
            now = mb(out)
            saved += was - now
            done += 1
            print(f"{now:.1f} МБ")
        else:
            failed += 1
            print("НЕ ВЫШЛО")

    spent = time.time() - began
    print(f"\nготово {done}, не вышло {failed}, за {spent / 60:.1f} мин")
    print(f"на показ вместо {weight / 1024:.1f} ГБ уходит "
          f"{(weight - saved) / 1024:.2f} ГБ")


if __name__ == "__main__":
    main()
