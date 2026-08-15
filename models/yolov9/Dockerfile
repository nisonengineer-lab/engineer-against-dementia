# ===========================================================================
#  Сборка YOLOv9 в ONNX для Frigate
#
#  Сборка (см. build.sh):
#      docker build -f Dockerfile \
#        --build-arg MODEL_SIZE=s --build-arg IMG_SIZE=320 \
#        --output . .
#
#  На выходе — yolov9-s-320.onnx, который кладётся в
#  frigate/config/model_cache/ и указывается в model.path.
# ===========================================================================

FROM python:3.11 AS build

RUN apt-get update \
 && apt-get install --no-install-recommends -y libgl1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /bin/

WORKDIR /yolov9
ADD https://github.com/WongKinYiu/yolov9.git .

RUN uv pip install --system -r requirements.txt
RUN uv pip install --system onnx==1.18.0 onnxruntime "onnx-simplifier>=0.4.1" onnxscript

ARG MODEL_SIZE
ARG IMG_SIZE

# --------------------------------------------------------------------------
#  КРИТИЧНО: суффикс `-converted`.
#
#  Обычные yolov9-s.pt имеют другую структуру выходного тензора. Frigate
#  прогоняет результат через post_process_yolo, который такой формат не
#  понимает — и возвращает пустой список детекций. Ошибки при этом нет:
#  система выглядит рабочей, но не видит ни одного человека.
# --------------------------------------------------------------------------
ADD https://github.com/WongKinYiu/yolov9/releases/download/v0.1/yolov9-${MODEL_SIZE}-converted.pt yolov9-${MODEL_SIZE}.pt

# torch>=2.6 переключил weights_only на True по умолчанию, а чекпоинты
# yolov9 содержат pickled-объекты. Без правки загрузка падает.
RUN sed -i "s/ckpt = torch.load(attempt_download(w), map_location='cpu')/ckpt = torch.load(attempt_download(w), map_location='cpu', weights_only=False)/g" models/experimental.py

RUN python3 export.py --weights ./yolov9-${MODEL_SIZE}.pt --imgsz ${IMG_SIZE} --simplify --include onnx

# Выносим только .onnx — образ на выходе весит ровно столько, сколько модель.
FROM scratch
ARG MODEL_SIZE
ARG IMG_SIZE
COPY --from=build /yolov9/yolov9-${MODEL_SIZE}.onnx /yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx
