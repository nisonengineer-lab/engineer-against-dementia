"""Перенарезка кадров события с ДРУГИМ числом кадров, но той же вырезкой.

Рамку человека аддон берёт у Frigate, а старые события Frigate уже не
помнит. Поэтому вырезку восстанавливаем по картинке: ищем, из какого места
первого кадра ролика вырезан сохранённый frame_01.jpg (и в каком масштабе,
если вырезка была шире 1280 и её ужали). Дальше режем тем же ffmpeg-фильтром,
что и аддон: fps -> crop -> scale='min(1280,iw)':-2.

Запуск (нужны numpy и pillow):
    python eval/recut.py 24 <куда_класть>
Кадры кладутся в <куда_класть>/<aid>/frame_NN.jpg, отчёт — в recut.json там же.
Событие, где найденная вырезка не совпала со старым кадром, пропускается.
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

R = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(R)
COUNT = int(sys.argv[1])
OUT = sys.argv[2]
LOW = 360                   # ширина, на которой ищем грубо


def gray(path_or_img):
    img = Image.open(path_or_img) if isinstance(path_or_img, str) else path_or_img
    return np.asarray(img.convert("L"), dtype=np.float32)


def first_frame(clip):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", clip, "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, check=True).stdout
    import io
    return Image.open(io.BytesIO(raw))


def ssd_map(image, templ):
    """Сумма квадратов разностей шаблона по всем положениям (через FFT)."""
    ih, iw = image.shape
    th, tw = templ.shape
    if th > ih or tw > iw:
        return None
    size = (ih + th, iw + tw)
    corr = np.fft.irfft2(np.fft.rfft2(image, size) *
                         np.conj(np.fft.rfft2(templ, size)), size)[:ih - th + 1, :iw - tw + 1]
    sq = np.pad(image ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    win = sq[th:, tw:] - sq[:-th, tw:] - sq[th:, :-tw] + sq[:-th, :-tw]
    return (win - 2 * corr + (templ ** 2).sum()) / templ.size


def locate(full, crop_img):
    """(x, y, w, h) вырезки в пикселях ролика."""
    W, H = full.size
    fw, fh = crop_img.size
    k = LOW / W
    small = gray(full.resize((LOW, round(H * k))))
    widths = [fw] if fw < 1280 else range(1280, W + 1, 16)   # ужатая — перебираем ширину
    best = None
    for cw in widths:
        ch = round(cw * fh / fw)
        if ch > H:
            break
        tw, th = max(8, round(cw * k)), max(8, round(ch * k))
        m = ssd_map(small, gray(crop_img.resize((tw, th))))
        if m is None:
            continue
        y, x = np.unravel_index(np.argmin(m), m.shape)
        score = m[y, x]
        if best is None or score < best[0]:
            best = (score, cw, ch, x / k, y / k)
    _, cw, ch, x0, y0 = best
    # уточняем в полном разрешении около грубой точки
    big = gray(full)
    templ = gray(crop_img.resize((cw, ch)) if fw == 1280 else crop_img)
    pad = int(2 / k) + 4
    xa, ya = max(0, int(x0) - pad), max(0, int(y0) - pad)
    xb, yb = min(W, int(x0) + cw + pad), min(H, int(y0) + ch + pad)
    m = ssd_map(big[ya:yb, xa:xb], templ)
    y, x = np.unravel_index(np.argmin(m), m.shape)
    return int(xa + x), int(ya + y), cw, ch


def clip_seconds(clip):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", clip], capture_output=True, text=True).stdout
    return float(out.strip() or 1)


def main():
    man = json.load(open(os.path.join(R, "manifest.json")))
    report = {}
    man = man[:int(os.environ.get("LIMIT", len(man)))]
    for i, m in enumerate(man):
        folder = os.path.join(PROJ, m["folder"])
        clip = os.path.join(folder, "clip.mp4")
        old = sorted(glob.glob(os.path.join(folder, "frames", "frame_*.jpg")))
        if not old or not os.path.exists(clip):
            continue
        crop_img = Image.open(old[0])
        full = first_frame(clip)
        whole = crop_img.size[0] == 1280 and abs(
            crop_img.size[1] / 1280 - full.size[1] / full.size[0]) < 0.01
        chain = [f"fps={max(COUNT / clip_seconds(clip), 0.1):.4f}"]
        box = None
        if not whole:
            box = locate(full, crop_img)
            chain.append("crop={2}:{3}:{0}:{1}".format(*box))
        chain.append("scale='min(1280,iw)':-2")
        dest = os.path.join(OUT, str(m["aid"]))
        os.makedirs(dest, exist_ok=True)
        for f in glob.glob(os.path.join(dest, "*.jpg")):
            os.remove(f)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-vf", ",".join(chain),
                        "-frames:v", str(COUNT), "-q:v", "4",
                        os.path.join(dest, "frame_%02d.jpg")], check=False)
        new = sorted(glob.glob(os.path.join(dest, "frame_*.jpg")))
        # сверка: первый новый кадр против первого старого
        diff = None
        if new:
            a = gray(new[0])
            b = gray(Image.open(old[0]).resize(Image.open(new[0]).size))
            diff = float(np.abs(a - b).mean())
        report[m["aid"]] = {"box": box, "whole": whole, "frames": len(new), "diff": diff}
        print(f"{i + 1}/{len(man)} {m['aid']} {'целиком' if whole else box} "
              f"кадров {len(new)} расхождение {diff:.1f}" if diff is not None else
              f"{i + 1}/{len(man)} {m['aid']} НЕТ КАДРОВ", flush=True)
    json.dump(report, open(os.path.join(OUT, "recut.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
