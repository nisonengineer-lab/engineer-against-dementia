# Alerts

A notification that didn't wake anyone up is worth zero. That's why there
are two channels and they are independent: Telegram and push through the
Home Assistant app.

---

## Telegram bot

Why not WhatsApp — [ADR-002](08-decisions.md). Short version: the Twilio
Sandbox dropped out every 72 hours.

### Setup

1. Create a bot with [@BotFather](https://t.me/BotFather), get the token.
2. Message the bot from every phone that should receive alerts.
3. Find out the `chat_id` of each recipient.
4. In the Home Assistant `configuration.yaml`:

```yaml
telegram_bot:
  - platform: polling
    api_key: !secret telegram_bot_token
    allowed_chat_ids:
      - !secret telegram_chat_id_primary
      - !secret telegram_chat_id_secondary
```

The token and the IDs go into `secrets.yaml`, which is **not committed**.
Template: `homeassistant/secrets.example.yaml`.

### ⚠️ `chat_id`, not `target`

In the `telegram_bot.send_message` action the recipient is set with the
`chat_id` field. There is no `target` parameter in this action — if you
copied it from old examples on the internet, fix it.

Sending to several recipients means several action calls, one per
`chat_id`.

---

## Push through the Home Assistant app

Telegram depends on the internet and on the person not having muted the
chat. The second channel is a direct push into the HA app, bypassing
silent mode.

```yaml
- action: notify.mobile_app_DEVICE_NAME
  data:
    title: "⚠️ Grandpa at the exit"
    message: "Person in the gate zone"
    data:
      ttl: 0
      priority: high
      channel: alarm_stream   # bypasses "Do Not Disturb" on Android
```

### ⚠️ `alarm_stream`, not `alarm_stream_max`

A common mix-up, worth memorizing:

| Value | Where it goes | What it does |
|---|---|---|
| `alarm_stream` | `channel` | sound on the alarm channel, bypasses "Do Not Disturb" |
| `alarm_stream_max` | `media_stream` | **for TTS**: speak at maximum volume, then restore the normal one |

These are different fields. `channel: alarm_stream_max` is not what you
meant.

Full spoken message at maximum volume:

```yaml
data:
  message: TTS
  data:
    media_stream: alarm_stream_max
    tts_text: "Grandpa at the exit"
```

Source: [Critical notifications, HA Companion](https://companion.home-assistant.io/docs/notifications/critical-notifications/).

---

## Automation: leaving through the gate

Ready-made file — `homeassistant/automations/exit-alert.yaml`.

The trigger is an MQTT event from Frigate, not an entity. That is faster
and more precise: the event arrives at the moment of entering the zone.

```yaml
trigger:
  - platform: mqtt
    topic: frigate/events
```

The conditions check the payload:

```jinja
{{ trigger.payload_json['after']['label'] == 'person' }}
{{ trigger.payload_json['after']['camera'] == 'yard_cam' }}
{{ 'exit' in trigger.payload_json['after']['entered_zones'] | map('lower') | list }}
```

### ⚠️ About `map('lower')`

In the Frigate config the zone is named `Exit`, capitalized. Comparison
in Jinja is case-sensitive. Without `map('lower')` the condition will
**never become true** — and it happens silently, with no error in the
logs. The automation looks like it works and never fires.

---

## Troubleshooting: the notification didn't arrive

Debug down the chain, top to bottom. Don't guess — look at the payload.

**1. Was there an event at all?**
Subscribe to the topic and walk in front of the camera:

```bash
mosquitto_sub -h MQTT_HOST -u USER -P PASSWORD -t 'frigate/events' -v
```

Nothing — the problem is in Frigate (zone, threshold, mask), not in HA.

**2. Does the payload contain what you're checking?**
Look at the actual values of `label`, `camera`, `entered_zones`. Case,
camera name, presence of the zone in the list — with your own eyes, as
they really are.

**3. Did the automation fire?**
Settings → Automations → trace. It shows exactly which condition stopped
it.

**4. The action ran but no message arrived?**
Telegram: check that the `chat_id` is in `allowed_chat_ids`.
Push: check that the device name in `notify.mobile_app_*` is current —
it changes when the app is reinstalled.

---

## Where this is going

- Different logic by time of day: getting out of bed at night ≠ during the day
- Escalation: no response within N minutes → next recipient
- Voice alert in the room ([ADR-009](08-decisions.md) — blocked, looking for an alternative)
- A daily summary instead of a stream of individual events
