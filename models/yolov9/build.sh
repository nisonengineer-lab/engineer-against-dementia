#!/usr/bin/env bash
# ===========================================================================
#  Сборка ONNX-модели YOLOv9 для Frigate.
#
#  Использование:
#      ./build.sh                 # по умолчанию: s @ 320
#      ./build.sh t 320           # tiny, быстрее, но хуже в сложных позах
#      ./build.sh s 640           # точнее, дороже по GPU
#
#  Результат кладётся в ../../frigate/config/model_cache/
# ===========================================================================
set -euo pipefail

MODEL_SIZE="${1:-s}"
IMG_SIZE="${2:-320}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/../../frigate/config/model_cache"

echo ">>> Собираю yolov9-${MODEL_SIZE} @ ${IMG_SIZE}x${IMG_SIZE}"

mkdir -p "${OUT_DIR}"

docker build \
  -f "${SCRIPT_DIR}/Dockerfile" \
  --build-arg "MODEL_SIZE=${MODEL_SIZE}" \
  --build-arg "IMG_SIZE=${IMG_SIZE}" \
  --output "${OUT_DIR}" \
  "${SCRIPT_DIR}"

RESULT="${OUT_DIR}/yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx"

if [[ ! -s "${RESULT}" ]]; then
  echo "!!! Файл не появился: ${RESULT}" >&2
  exit 1
fi

echo ">>> Готово: ${RESULT} ($(du -h "${RESULT}" | cut -f1))"
echo ">>> Пропиши в frigate/config.yaml:"
echo "      model.path: /config/model_cache/yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx"
echo "      model.width / model.height: ${IMG_SIZE}"
echo ">>> И перезапусти:  docker compose restart frigate"
