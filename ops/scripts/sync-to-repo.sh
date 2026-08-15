#!/usr/bin/env bash
# ===========================================================================
#  sync-to-repo.sh — сбор живых конфигов, санитизация, коммит.
#
#  Логика: собрать -> вычистить секреты -> показать diff -> применить ->
#  проверить -> закоммитить. По умолчанию НИЧЕГО не меняет.
#
#  Использование:
#      ./sync-to-repo.sh                    # только показать, что изменится
#      ./sync-to-repo.sh --apply            # записать в репозиторий
#      ./sync-to-repo.sh --apply --commit   # + git commit
#      ./sync-to-repo.sh --apply --commit --push   # + создать репо и запушить
#
#  Секреты, которые не выводятся автоматически, добавляй строками в
#  ~/.repo-secrets.local  (по одному значению на строку). Файл не в репо.
# ===========================================================================
set -uo pipefail

REPO="${REPO:-$HOME/PycharmProjects/engineer-against-dementia}"
STAGE="$HOME/.repo-staging"
RAW="$STAGE/raw"
CLEAN="$STAGE/clean"
SECRETS_LOCAL="$HOME/.repo-secrets.local"
GH_USER="nisonengineer-lab"
GH_REPO="engineer-against-dementia"

APPLY=0; COMMIT=0; PUSH=0
for a in "$@"; do
  case "$a" in
    --apply)  APPLY=1 ;;
    --commit) COMMIT=1 ;;
    --push)   PUSH=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Неизвестный флаг: $a"; exit 1 ;;
  esac
done

c_r=$'\e[31m'; c_g=$'\e[32m'; c_y=$'\e[33m'; c_b=$'\e[1m'; c_0=$'\e[0m'
say(){ echo; echo "${c_b}=== $* ===${c_0}"; }
ok(){  echo "  ${c_g}✔${c_0} $*"; }
warn(){ echo "  ${c_y}!${c_0} $*"; }
err(){ echo "  ${c_r}[X]${c_0} $*"; }

[ -d "$REPO" ] || { err "Репозиторий не найден: $REPO"; exit 1; }

rm -rf "$STAGE"; mkdir -p "$RAW" "$CLEAN"; chmod 700 "$STAGE"

# ===========================================================================
say "1. СБОР ЖИВЫХ ФАЙЛОВ"
# ===========================================================================
grab(){ # grab <источник> <куда-в-staging> [sudo]
  local src="$1" dst="$RAW/$2" use_sudo="${3:-}"
  mkdir -p "$(dirname "$dst")"
  if [ -n "$use_sudo" ]; then
    sudo test -r "$src" 2>/dev/null && { sudo cat "$src" > "$dst" 2>/dev/null; sudo chown "$USER" "$dst"; }
  else
    [ -r "$src" ] && cp "$src" "$dst" 2>/dev/null
  fi
  if [ -s "$dst" ]; then ok "$src"; else warn "нет доступа или пусто: $src"; rm -f "$dst"; fi
}

grab "$HOME/frigate/config/config.yaml"                 "frigate/config.yaml"
grab "$HOME/frigate/docker-compose.yml"                 "frigate/docker-compose.yml"
grab "$HOME/frigate/yolov9.Dockerfile"                  "models/yolov9/Dockerfile"
grab "$HOME/PycharmProjects/WelcomeScreen/vlm_test.py"       "services/vision/vlm_test.py"
grab "$HOME/PycharmProjects/WelcomeScreen/vlm_video_test.py" "services/vision/vlm_video_test.py"
grab "/usr/local/bin/check-ha-health.sh"                "ops/scripts/check-ha-health.sh" sudo
grab "/etc/cloudflared/config.yml"                      "ops/cloudflared/config.example.yml" sudo

for u in /etc/systemd/system/*.service; do
  [ -e "$u" ] || continue
  b="$(basename "$u")"
  case "$b" in
    *homeassistant*|*cloudflared*|*eee*|*frigate*|*nic-*) grab "$u" "ops/systemd/$b" sudo ;;
  esac
done

if crontab -l >/dev/null 2>&1; then
  crontab -l > "$RAW/ops/crontab.txt" 2>/dev/null && ok "crontab пользователя"
fi

# automations.yaml из HA живёт внутри VM — подхватываем, если ты его выгрузил
for cand in "$HOME/repo_staging/automations.yaml" "$HOME/Downloads/automations.yaml"; do
  [ -r "$cand" ] && { mkdir -p "$RAW/homeassistant/automations"; \
    cp "$cand" "$RAW/homeassistant/automations/automations.yaml"; ok "$cand"; break; }
done
[ -e "$RAW/homeassistant/automations/automations.yaml" ] || \
  warn "automations.yaml из HA не найден (выгрузи в ~/repo_staging/ — он внутри VM)"

# ===========================================================================
say "2. СБОР СЕКРЕТОВ ДЛЯ ВЫЧИСТКИ"
# ===========================================================================
SECRETS="$STAGE/secrets.list"; : > "$SECRETS"; chmod 600 "$SECRETS"
add(){ [ -n "${1:-}" ] && [ ${#1} -ge 4 ] && printf '%s\n' "$1" >> "$SECRETS"; }

CFG="$RAW/frigate/config.yaml"
if [ -s "$CFG" ]; then
  add "$(grep -oP '^\s*password:\s*\K\S+' "$CFG" | head -1)"
  add "$(grep -oP '^\s*user:\s*\K\S+'     "$CFG" | head -1)"
  # логины/пароли из RTSP-URL
  grep -oP 'rtsp://\K[^:]+(?=:)'  "$CFG" | sort -u | while read -r v; do add "$v"; done
  grep -oP 'rtsp://[^:]+:\K[^@]+' "$CFG" | sort -u | while read -r v; do add "$v"; done
fi

# приватные IP всей сети
grep -rhoE '\b10\.0\.0\.[0-9]{1,3}\b' "$RAW" 2>/dev/null | sort -u | while read -r v; do add "$v"; done
grep -rhoE '\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b' "$RAW" 2>/dev/null | sort -u | while read -r v; do add "$v"; done
# tunnel UUID
grep -rhoE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' "$RAW" 2>/dev/null \
  | grep -v '^00000000-' | sort -u | while read -r v; do add "$v"; done
# токены Telegram (формат <bot_id>:<строка>, длина строки у Telegram плавала)
grep -rhoE '[0-9]{6,12}:[A-Za-z0-9_-]{30,45}' "$RAW" 2>/dev/null | sort -u | while read -r v; do add "$v"; done
# chat_id получателей — тоже персональные данные
grep -rhoP '(?i)chat_id\s*[:=]\s*\K-?[0-9]{6,15}' "$RAW" 2>/dev/null | sort -u | while read -r v; do add "$v"; done
grep -rhoP '(?i)allowed_chat_ids?.*?\K-?[0-9]{6,15}'  "$RAW" 2>/dev/null | sort -u | while read -r v; do add "$v"; done
# api_key / token в открытом виде в любых собранных yaml
grep -rhoP '^\s*(api_key|token|password|passwd)\s*[:=]\s*["'"'"']?\K[^"'"'"'\s]{6,}' "$RAW" 2>/dev/null \
  | grep -vE '^(!secret|\{|<|\$)' | sort -u | while read -r v; do add "$v"; done
# личный список
[ -r "$SECRETS_LOCAL" ] && grep -vE '^\s*(#|$)' "$SECRETS_LOCAL" >> "$SECRETS"

sort -u "$SECRETS" | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2- > "$SECRETS.sorted"
mv "$SECRETS.sorted" "$SECRETS"
echo "  найдено значений к вычистке: $(wc -l < "$SECRETS")"
echo "  (сами значения не печатаю; список: $SECRETS, права 600)"

# ===========================================================================
say "3. САНИТИЗАЦИЯ"
# ===========================================================================
placeholder(){ # осмысленная замена вместо безликого REDACTED
  case "$1" in
    10.0.0.*|192.168.*)                     echo "LOCAL_IP" ;;
    *-*-*-*-*)                              echo "00000000-0000-0000-0000-000000000000" ;;
    *[0-9]:[A-Za-z]*)                       echo "000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" ;;
    [0-9][0-9][0-9][0-9][0-9][0-9]*)        echo "000000000" ;;
    *)                                      echo "CHANGEME" ;;
  esac
}

mkdir -p "$CLEAN"
(cd "$RAW" && find . -type f) | while read -r rel; do
  mkdir -p "$CLEAN/$(dirname "$rel")"
  cp "$RAW/$rel" "$CLEAN/$rel"
  while IFS= read -r s; do
    [ -z "$s" ] && continue
    esc=$(printf '%s' "$s" | sed -e 's/[][\.*^$/&]/\\&/g')
    rep=$(placeholder "$s")
    sed -i "s/$esc/$rep/g" "$CLEAN/$rel"
  done < "$SECRETS"
done
# уточняем LOCAL_IP по порту — так понятнее, что это за адрес
(cd "$CLEAN" && find . -type f) | while read -r rel; do
  sed -i -e 's#LOCAL_IP:11434#OLLAMA_HOST_IP:11434#g' \
         -e 's#LOCAL_IP:8123#HA_VM_IP:8123#g'         \
         -e 's#LOCAL_IP:554#CAMERA_IP:554#g'          \
         -e 's#LOCAL_IP:1883#MQTT_HOST:1883#g'        \
         -e 's#LOCAL_IP:5000#FRIGATE_HOST:5000#g'     \
         "$CLEAN/$rel"
done
ok "вычищено файлов: $( (cd "$CLEAN" && find . -type f) | wc -l )"

# ===========================================================================
say "4. ГЕЙТ: ОСТАЛОСЬ ЛИ ЧТО-ТО ЧУВСТВИТЕЛЬНОЕ"
# ===========================================================================
LEAK=0
# всё, что уже заменено плейсхолдером, гейт должен пропускать
PLACEHOLDERS='CHANGEME|LOCAL_IP|AAAAAAAAAA|0000000000|00000000-0000|CAMERA_IP|HA_VM_IP|OLLAMA_HOST_IP|FRIGATE_|YOUR_USERNAME|USER:PASSWORD|MQTT_HOST|example\.(net|com)|10\.0\.0\.[Xx]'
scan(){ # scan <регэксп> <описание>
  local hits
  hits=$(grep -rInE "$1" "$CLEAN" 2>/dev/null | grep -vE "$PLACEHOLDERS" || true)
  if [ -n "$hits" ]; then LEAK=1; err "$2"; echo "$hits" | head -5 | sed 's/^/      /'; fi
}
scan 'rtsp://[^:/]+:[^@]+@'                                   'логин:пароль в RTSP-URL'
scan '\b10\.0\.0\.[0-9]{1,3}\b'                               'приватный IP 10.0.0.x'
scan '\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b'                   'приватный IP 192.168.x.x'
scan '[0-9]{8,10}:AA[A-Za-z0-9_-]{33}'                        'токен Telegram-бота'
scan '(ghp|gho|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}' 'токен GitHub'
scan '-----BEGIN [A-Z ]*PRIVATE KEY-----'                     'приватный ключ'
scanP(){ # то же, но PCRE (нужен lookahead)
  local hits
  hits=$(grep -rInP "$1" "$CLEAN" 2>/dev/null | grep -vE "$PLACEHOLDERS" || true)
  if [ -n "$hits" ]; then LEAK=1; err "$2"; echo "$hits" | head -5 | sed 's/^/      /'; fi
}
scanP '^\s*(password|passwd|api_key|token|secret)\s*[:=]\s*(?!.*(CHANGEME|changeme|\{|<|\$|!secret|""))\S{6,}' 'пароль/токен в открытом виде'

if [ "$LEAK" -eq 1 ]; then
  echo
  err "СТОП. Найдено чувствительное. Добавь значения в $SECRETS_LOCAL и запусти снова."
  exit 2
fi
ok "чисто — секретов не осталось"

# ===========================================================================
say "5. ЧТО ИЗМЕНИТСЯ В РЕПОЗИТОРИИ"
# ===========================================================================
CHANGED=0
(cd "$CLEAN" && find . -type f) | while read -r rel; do
  tgt="$REPO/${rel#./}"
  if [ ! -f "$tgt" ]; then
    echo "  ${c_g}НОВЫЙ${c_0}    ${rel#./}"
  elif ! diff -q "$CLEAN/$rel" "$tgt" >/dev/null 2>&1; then
    echo "  ${c_y}ИЗМЕНЁН${c_0}  ${rel#./}"
    diff -u "$tgt" "$CLEAN/$rel" | sed -n '3,15p' | sed 's/^/        /'
  fi
done
echo
warn "Файлы с ручными комментариями (frigate/config.example.yaml, docs/) НЕ трогаются."
warn "Скрипт обновляет только то, что снято с живой машины."

# ===========================================================================
if [ "$APPLY" -eq 0 ]; then
  say "РЕЖИМ ПРОСМОТРА"
  echo "  Ничего не записано. Чтобы применить:"
  echo "      $0 --apply"
  echo "      $0 --apply --commit --push"
  exit 0
fi

say "6. ПРИМЕНЯЮ"
# ===========================================================================
# Файлы, в которые вручную добавлены комментарии, НЕ перезаписываем вслепую.
# Оригинал с машины кладём рядом как <имя>.from-host для сверки и слияния.
is_curated(){
  case "$1" in
    frigate/config.yaml|frigate/docker-compose.yml|\
    models/yolov9/Dockerfile|\
    ops/scripts/check-ha-health.sh|\
    ops/cloudflared/config.example.yml) return 0 ;;
    *) return 1 ;;
  esac
}
(cd "$CLEAN" && find . -type f) | while read -r rel; do
  r="${rel#./}"
  mkdir -p "$REPO/$(dirname "$r")"
  if is_curated "$r"; then
    cp "$CLEAN/$rel" "$REPO/$r.from-host"
    warn "$r.from-host — оригинал с машины, кураторский файл НЕ тронут"
  else
    cp "$CLEAN/$rel" "$REPO/$r"
    ok "$r"
  fi
done
chmod +x "$REPO"/ops/scripts/*.sh 2>/dev/null

# ===========================================================================
say "7. ПРОВЕРКА УЖЕ ВНУТРИ РЕПОЗИТОРИЯ"
# ===========================================================================
cd "$REPO" || exit 1
[ -d .git ] || { git init -q -b main; ok "git init"; }
git add -A

BAD=$(git diff --cached --name-only | grep -EI '\.env$|secrets\.ya?ml$|\.db$|\.(mp4|mkv|jpg|jpeg|png|onnx|pt|pem|key)$' \
      | grep -v example || true)
if [ -n "$BAD" ]; then
  err "В индекс попало запрещённое:"; echo "$BAD" | sed 's/^/      /'
  err "Проверь .gitignore. Коммит остановлен."; exit 3
fi
ok "в индексе только разрешённое: $(git diff --cached --name-only | wc -l) файлов"

# финальный скан ровно того, что уйдёт в коммит
TMPD=$(mktemp -d); git checkout-index -a -f --prefix="$TMPD/"
FINAL=$(grep -rInE 'rtsp://[^:/]+:[^@]+@|\b10\.0\.0\.[0-9]{1,3}\b|[0-9]{6,12}:[A-Za-z0-9_-]{30,45}' "$TMPD" 2>/dev/null \
        | grep -vE "$PLACEHOLDERS" \
        | grep -v 'ops/scripts/sync-to-repo\.sh' || true)
rm -rf "$TMPD"
if [ -n "$FINAL" ]; then
  err "Финальный скан индекса нашёл чувствительное:"; echo "$FINAL" | head -10 | sed 's/^/      /'; exit 4
fi
ok "финальный скан индекса чист"

if [ "$COMMIT" -eq 0 ]; then
  say "ГОТОВО (без коммита)"; git status --short | head -30; exit 0
fi

# ===========================================================================
say "8. КОММИТ"
# ===========================================================================
git config user.name  >/dev/null || git config user.name "$GH_USER"
git config user.email >/dev/null || { err "Задай почту: git config --global user.email '...'"; exit 5; }

if git rev-parse HEAD >/dev/null 2>&1; then
  MSG="Синхронизация конфигов с рабочей системы

Автоматический сбор через ops/scripts/sync-to-repo.sh,
секреты вычищены, проверка индекса пройдена."
else
  MSG="Локальная система мониторинга для ухода за пожилыми родственниками

Frigate NVR + Home Assistant + Telegram, всё локально.
Конфиги, автоматизации, сборка YOLOv9, ops-обвязка.
Документация на RU/EN/HE."
fi

if git diff --cached --quiet; then warn "нечего коммитить"; else
  git commit -q -m "$MSG" && ok "коммит создан"
fi

[ "$PUSH" -eq 0 ] && { say "ГОТОВО (без push)"; git log --oneline -3; exit 0; }

# ===========================================================================
say "9. PUSH"
# ===========================================================================
command -v gh >/dev/null || { err "gh не установлен: sudo apt install -y gh"; exit 6; }
gh auth status >/dev/null 2>&1 || { err "Не авторизован: gh auth login"; exit 7; }

if gh repo view "$GH_USER/$GH_REPO" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || \
    git remote add origin "https://github.com/$GH_USER/$GH_REPO.git"
  git push -u origin main && ok "запушено"
else
  gh repo create "$GH_REPO" --public --source=. --remote=origin --push \
    --description "Локальная privacy-first система мониторинга для ухода за пожилыми родственниками" \
    && ok "репозиторий создан и запушен"
fi

say "ГОТОВО"
echo "  https://github.com/$GH_USER/$GH_REPO"
