# Configuration

A walkthrough of `frigate/config.example.yaml`. The values were picked
empirically; Frigate's defaults are noticeably softer and on this
installation they produced false positives.

---

## Masks: mask first, everything else after

A mask removes from processing the areas where detection isn't needed:
the street beyond the fence, the neighbour's yard, a tree canopy moving
in the wind.

This isn't resource optimisation, it's a condition for the thing working
at all. Without a mask the yard camera picks up passers-by on the
pavement — and the system wakes you ten times a night. On the third night
it gets switched off.

Draw them in the UI: **Settings → Mask & Zone Editor**. Coordinates are
normalised (0–1), so they don't depend on resolution.

---

## Zones: split by meaning, not by geometry

```yaml
zones:
  home:    # porch — "the person came back"
  yard:    # yard — "went out, but on the property", no alert
  Exit:    # gate — THIS is an alert
```

Three zones instead of one, because they have different response logic.
"Went out into the yard" and "went out through the gate" are
fundamentally different events for a person with dementia.

### `inertia`

How many consecutive frames an object has to be in a zone before the zone
counts as occupied.

| Value | Where | Why |
|---|---|---|
| `1` | alert zone (gate) | a person crosses it in seconds; you can't wait |
| `3` | everyday zones (kitchen, bedroom) | suppresses twitching at someone walking past |

### ⚠️ Zone name casing

In the config the zone is named `Exit`. In Home Assistant templates the
comparison is case-sensitive. If you don't lowercase it:

```jinja
{{ 'exit' in trigger.payload_json['after']['entered_zones'] | map('lower') | list }}
```

— the trigger **won't fire, silently**, with no error in the logs.

---

## Detection thresholds

```yaml
filters:
  person:
    min_score: 0.6      # below this, the detection isn't considered at all
    threshold: 0.7      # threshold for an object to count as confirmed
    min_area: 500       # smaller than this is junk
```

**The outdoor camera is softer, the indoor one stricter** (`0.6` versus
`0.8`). Indoors there are plenty of false positives on furniture,
reflections and shadows; outdoors it matters more not to miss a person in
the distance.

---

## Splitting detect / record

```yaml
inputs:
  - path: rtsp://...
    roles: [detect]
  - path: rtsp://...
    roles: [record]
```

The same URL, two roles. Detection needs low latency and tolerates a loss
of quality; recording needs the full bitrate and tolerates delay. On a
single stream the requirements conflict.

---

## `stationary`

```yaml
stationary:
  threshold: 5
```

After how many frames a motionless object stops being re-evaluated on
every frame. It saves GPU, but there's a subtlety here: **a person lying
motionless is exactly the event we're after**. Too aggressive a setting
will "forget" them. `5` is the compromise.

---

## Face recognition

```yaml
face_recognition:
  detection_threshold: 0.75    # confidence that a face was found at all
  recognition_threshold: 0.87  # confidence in the match against a known person
  unknown_score: 0.78          # below this — "unknown"
  min_area: 2000               # don't try to recognise small faces
  min_faces: 2                 # consecutive frames before a name is assigned
  blur_confidence_filter: true # penalty for blurred frames
```

The thresholds are above the defaults: with the standard ones the system
confused family members.

**Thresholds are secondary. Discipline in the training set comes first.**
20–50 good photos per person work better than 500 random ones. More
detail — [06-ai-models.md](06-ai-models.md).

---

## Classification by appearance ⚠️

```yaml
classification:
  custom:
    person_by_appearance:
      object_config:
        classification_type: attribute   # ← not sub_label
```

A fallback for when the face isn't visible: silhouette, clothing.

**`attribute`, not `sub_label`.** With type `sub_label` the result
conflicts with face recognition, which writes to the same field.

**Status: experimental.** After this section was added, Frigate started
returning 502 on the auth service. Not diagnosed. If you hit a 502 —
comment the block out and restart the container.

---

## Retention

```yaml
record:
  alerts:    { retain: { days: 7, mode: motion } }
  detections:{ retain: { days: 7, mode: motion } }
snapshots:
  retain:    { default: 14 }
```

Seven days. More runs into both disk space and common sense: nobody is
going to review a week-old incident anyway. It's also a reasonable limit
on storing video of people in their own home.

---

## Hardware acceleration

```yaml
ffmpeg:
  hwaccel_args: preset-nvidia
```

| Platform | Value |
|---|---|
| NVIDIA GPU | `preset-nvidia` |
| Raspberry Pi 5 | `preset-rpi-64-h264` |
| Intel iGPU | `preset-vaapi` |

Without hardware decoding the CPU pins at 100% with two 4K cameras.

---

## Secrets

Not a single password is written into `config.yaml`:

```yaml
mqtt:
  password: "{FRIGATE_MQTT_PASSWORD}"
```

Frigate substitutes environment variables prefixed with `FRIGATE_` — but
**only in the fields where that's allowed** in the reference config
(`mqtt.*`, `ffmpeg.inputs.path` and a few others). It isn't a universal
substitution mechanism across the whole file.

The values live in `.env`, which is in `.gitignore`.
