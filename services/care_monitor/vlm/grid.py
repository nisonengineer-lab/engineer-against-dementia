"""Склейка кадров в одну картинку — для моделей, которым можно дать только одну.

ЗАЧЕМ. Llama 3.2 Vision в Ollama принимает ровно одно изображение на запрос,
а вопросы цепочки задаются по 5–12 кадрам: модели нужно видеть, что было
до и что после. Поэтому кадры (и эталонные фото, если шаг их просит) кладутся
в сетку, слева направо и сверху вниз, а в углу каждой клетки — её номер.

ЦЕНА. Llama сама ужимает картинку примерно до 1120 пикселей по большей
стороне. Сетка 4x3 — это клетка шириной около 280 пикселей, то есть каждый
кадр видно мельче, чем когда кадры идут по одному. Поэтому сетка собирается
сразу под GRID_WIDTH: рисовать её шире бессмысленно, всё равно ужмут.

Клетки одного размера: кадр вписывается с полями, а не растягивается, —
иначе высокий кроп сидящего человека и широкий кроп идущего исказили бы
пропорции тела, а по ним как раз и решают, сидит он или лежит.
"""
import base64
import io
import math

from PIL import Image, ImageDraw, ImageFont

import config

BACKGROUND = (24, 24, 24)
LABEL_BG = (0, 0, 0)
LABEL_FG = (255, 255, 0)


def layout(count):
    """Столбцов и строк для count клеток: почти квадрат, строк не больше столбцов."""
    cols = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / cols))
    return cols, rows


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:            # Pillow старше 10.1: только мелкий шрифт
        return ImageFont.load_default()


def build(references, frames):
    """Сетка в base64 (JPEG). references — эталонные фото, идут первыми с
    подписью «REF n»; frames — кадры сцены по порядку времени, подпись «n»."""
    cells = [(f"REF {i}", p) for i, p in enumerate(references, 1)]
    cells += [(str(i), p) for i, p in enumerate(frames, 1)]
    cols, rows = layout(len(cells))

    width = max(cols * 64, config.GRID_WIDTH)
    cell_w = width // cols
    # Пропорции клетки — как у первого кадра сцены: их большинство.
    with Image.open(frames[0] if frames else cells[0][1]) as first:
        ratio = first.height / first.width
    cell_h = max(32, int(cell_w * ratio))

    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = _font(max(12, cell_w // 10))
    for n, (label, path) in enumerate(cells):
        x, y = (n % cols) * cell_w, (n // cols) * cell_h
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((cell_w - 2, cell_h - 2))
            sheet.paste(im, (x + (cell_w - im.width) // 2, y + (cell_h - im.height) // 2))
        box = draw.textbbox((0, 0), label, font=font)
        draw.rectangle((x, y, x + box[2] + 8, y + box[3] + 6), fill=LABEL_BG)
        draw.text((x + 4, y + 2), label, fill=LABEL_FG, font=font)

    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=88)
    return base64.b64encode(out.getvalue()).decode(), (cols, rows)


def context_line(references, frames, shape):
    """Строка для промпта: как читать сетку."""
    cols, rows = shape
    line = (f"СОСТАВ КАДРОВ: все кадры склеены в ОДНУ картинку — сетка "
            f"{cols}x{rows}, читать слева направо и сверху вниз, номер клетки "
            f"в её левом верхнем углу.")
    if references:
        line += (f" Клетки с подписью REF 1…REF {references} — эталонные фото "
                 f"наблюдаемого, дальше клетки 1…{frames} — кадры сцены по "
                 f"порядку времени")
    else:
        line += f" Клетки 1…{frames} — кадры сцены по порядку времени"
    return line
