# Оповещения

Уведомление, которое не разбудило — это ноль. Поэтому каналов два и они
независимы: Telegram и push через приложение Home Assistant.

---

## Telegram-бот

Почему не WhatsApp — [ADR-002](08-decisions.md#adr-002--telegram-вместо-whatsappttwilio).
Коротко: Twilio Sandbox отваливался каждые 72 часа.

### Настройка

1. Создать бота у [@BotFather](https://t.me/BotFather), получить токен.
2. Написать боту с каждого телефона, который должен получать тревоги.
3. Узнать `chat_id` каждого получателя.
4. В `configuration.yaml` Home Assistant:

```yaml
telegram_bot:
  - platform: polling
    api_key: !secret telegram_bot_token
    allowed_chat_ids:
      - !secret telegram_chat_id_primary
      - !secret telegram_chat_id_secondary
```

Токен и ID — в `secrets.yaml`, который **не коммитится**. Шаблон:
`homeassistant/secrets.example.yaml`.

### ⚠️ `chat_id`, не `target`

В действии `telegram_bot.send_message` получатель задаётся полем
`chat_id`. Параметра `target` в этом действии нет — если приехали из
старых примеров в интернете, поправьте.

Отправка нескольким адресатам — несколько вызовов действия, по одному на
`chat_id`.

---

## Push через приложение Home Assistant

Telegram зависит от интернета и от того, что человек не заглушил чат.
Второй канал — прямой push в приложение HA, с обходом беззвучного режима.

```yaml
- action: notify.mobile_app_ИМЯ_УСТРОЙСТВА
  data:
    title: "⚠️ Дед у выхода"
    message: "Человек в зоне калитки"
    data:
      ttl: 0
      priority: high
      channel: alarm_stream   # обход «Не беспокоить» на Android
```

### ⚠️ `alarm_stream`, а не `alarm_stream_max`

Частая путаница, стоит запомнить:

| Значение | Куда ставится | Что делает |
|---|---|---|
| `alarm_stream` | `channel` | звук по каналу будильника, обходит «Не беспокоить» |
| `alarm_stream_max` | `media_stream` | **для TTS**: озвучить на максимальной громкости, потом вернуть обычную |

Это разные поля. `channel: alarm_stream_max` — не то, что вы имели в виду.

Полная озвучка сообщения на максимуме:

```yaml
data:
  message: TTS
  data:
    media_stream: alarm_stream_max
    tts_text: "Дед у выхода"
```

Источник: [Critical notifications, HA Companion](https://companion.home-assistant.io/docs/notifications/critical-notifications/).

---

## Автоматизация: выход за калитку

Готовый файл — `homeassistant/automations/exit-alert.yaml`.

Триггер — событие MQTT от Frigate, не сущность. Так быстрее и точнее:
событие приходит в момент входа в зону.

```yaml
trigger:
  - platform: mqtt
    topic: frigate/events
```

Условия проверяют payload:

```jinja
{{ trigger.payload_json['after']['label'] == 'person' }}
{{ trigger.payload_json['after']['camera'] == 'yard_cam' }}
{{ 'exit' in trigger.payload_json['after']['entered_zones'] | map('lower') | list }}
```

### ⚠️ Про `map('lower')`

В конфиге Frigate зона называется `Exit`, с большой буквы. Сравнение в
Jinja регистрозависимое. Без `map('lower')` условие **никогда не станет
истинным** — и это происходит молча, без ошибки в логах. Автоматизация
выглядит рабочей и не срабатывает.

---

## Диагностика: уведомление не пришло

Отладка по цепочке, сверху вниз. Не угадывать — смотреть payload.

**1. Событие вообще было?**
Подпишись на топик и пройди перед камерой:

```bash
mosquitto_sub -h MQTT_HOST -u USER -P PASSWORD -t 'frigate/events' -v
```

Пусто — проблема во Frigate (зона, порог, маска), а не в HA.

**2. Payload содержит то, что ты проверяешь?**
Смотри реальные значения `label`, `camera`, `entered_zones`. Регистр,
имя камеры, наличие зоны в списке — глазами, по факту.

**3. Автоматизация сработала?**
Settings → Automations → трассировка. Видно, на каком именно условии
остановилось.

**4. Действие выполнилось, а сообщения нет?**
Telegram: проверь, что `chat_id` есть в `allowed_chat_ids`.
Push: проверь, что имя устройства в `notify.mobile_app_*` актуально —
оно меняется при переустановке приложения.

---

## Куда это движется

- Разная логика по времени суток: ночной выход из кровати ≠ дневной
- Эскалация: нет реакции за N минут → следующий получатель
- Голосовое оповещение в комнате ([ADR-009](08-decisions.md#adr-009--голосовое-оповещение-через-яндексстанцию--заблокировано) — заблокировано, ищем альтернативу)
- Дневная сводка вместо потока отдельных событий
