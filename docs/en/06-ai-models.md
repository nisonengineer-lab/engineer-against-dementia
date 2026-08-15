# AI models

Four different tasks, four different tools. The temptation to hand
everything to one big model exists and it's a mistake — see
[ADR-005](08-decisions.md).

| Task | Model | Latency | Status |
|---|---|---|---|
| Person detection | YOLOv9-s @ 320, ONNX | ~30–80 ms | ✅ works |
| Face recognition | Frigate face recognition | ~100 ms | ⚠️ works, inaccurate |
| Appearance classification | MobileNetV2 (custom) | — | ⚠️ stuck on a 502 |
| Activity description | Qwen3-VL 8B via Ollama | 10–30 s | 🧪 experiment |
| Fall | pose estimation / mmWave | < 1 s | 📋 designed |

---

## Person detection — YOLOv9

Build: `models/yolov9/build.sh s 320`. Weights are not committed to the
repository.

### `-converted.pt` is not optional

Upstream releases contain both `yolov9-s.pt` and `yolov9-s-converted.pt`.
You must take **only `-converted`**.

The regular weights produce a different output tensor structure.
`post_process_yolo` in Frigate returns an empty detection list — **with no
error in the logs**. The system looks like it's running and doesn't see a
single person. The only symptom: the detection counter sits at zero on a
frame that is obviously not empty.

### Why `s` and not `t`

`yolov9-t` missed people in difficult poses: lying on the couch, partly
under a blanket, half behind furniture. For general-purpose surveillance
that's a detail. Here those are exactly the poses that are the target
event.

### Input size

| imgsz | What you get | What you pay |
|---|---|---|
| 320 | current choice, good up to ~8 m | small and distant objects are lost |
| 640 | noticeably better at 10–15 m | ~4× the GPU load |

When moving to Pi 5 + Hailo-8 the model has to be recompiled into `.hef` —
ONNX does not execute there directly.

---

## Face recognition

It works, but it confuses family members and does poorly at a distance
and at an angle. This is an open problem.

### What's already been done

Thresholds raised above the defaults:

```yaml
detection_threshold: 0.75
recognition_threshold: 0.87
unknown_score: 0.78
min_area: 2000
min_faces: 2
blur_confidence_filter: true
```

### Training set discipline matters more than thresholds

**20–50 good photos per person work better than 500 random ones.** An
accumulated dump of hundreds of frames of varying quality, angles and
lighting doesn't improve recognition, it blurs the embedding.

What should be in the training set:

- different angles: full face, three-quarter, profile;
- different lighting: day, evening, artificial light;
- **the distance the camera actually shoots from** — training on close-up
  portraits and expecting recognition from 8 meters is pointless;
- glasses / no glasses, headwear — if the person wears them.

What should not be in the training set: blurry, too small, face covered,
and frames that are actually a different person.

### 🪤 UI trap: the "Recent Recognitions" tab

In the Frigate interface **"Recent Recognitions" is a hidden dropdown, not
a regular tab**. You have to click **on the text with the number itself**,
not look for a separate "Train" button next to it.

This is a usability problem officially acknowledged by the developers.
Until you know it, it looks like the training feature simply isn't there.

### What's left

- clean the training set from 500+ down to 20–50 good photos per person;
- work with Train **daily and with discipline** — this is not a one-time
  setup, it's a habit.

### A dead end you shouldn't walk into

Face enhancement via GPEN/GFPGAN on a separate RTSP proxy is **cancelled
for good**: technically it worked, but GPU load hit ~92%, the stream was
choppy, and it fought Frigate itself for the GPU directly.
[ADR-003](08-decisions.md).

---

## Appearance classification ⚠️

A fallback for when the face isn't visible: silhouette, clothing. Frigate
0.17+, custom MobileNetV2.

**`classification_type: attribute`, not `sub_label`** — with `sub_label`
the result conflicts with face recognition, which writes into the same
field.

**Open problem:** after adding the `classification` section Frigate
started returning 502 on the auth service. Not diagnosed. On a 502 —
comment out the block and restart the container.

---

## Vision LLM — activity description

Code: `services/vision/`. Model: Qwen3-VL 8B via Ollama on a **separate**
machine with a GPU.

### What it does and what it doesn't

| Question | Answers |
|---|---|
| What is the person doing? | ✅ that's its job |
| Who is it? | ❌ face recognition — faster and more accurate |
| Which room? | ❌ a static `camera → zone` map, that's a config constant |
| Did they fall? | ❌ **1–3 s per frame. For a fall that's an eternity** |

### A clip as a sequence of frames

Ollama does not accept a video file. `ffmpeg` cuts the clip into N evenly
spaced frames, they go out as a batch in the `images` field, and the model
reads them as a sequence in time.

Budget: ~5 s per frame × 5 frames ⇒ a ~25 s window. This is not a reaction
to an event, this is **a diary entry**.

### The prompt forbids filling in the blanks

A model that's allowed to complete the picture starts writing "went to the
kitchen" where the person just walked out of frame. So the prompt says it
outright: describe only what is visible, don't guess direction, better to
say something general but correct.

### Known qwen3-vl + Ollama behavior

The model sometimes goes off "thinking" and hits the generation limit
before the answer — an empty string with `done_reason: "length"`. That's
why `num_predict` defaults to 1024: it's a **ceiling, not a target**, it
just keeps the answer from being cut off.

---

## Fall detection — designed

**Pose estimation (YOLOv8n-pose), not a bounding box heuristic.**

The simple rule "bbox wider than tall ⇒ lying down" breaks on camera
angle: from a ceiling angle a standing person also produces a wide bbox.
Skeleton keypoints are robust to the angle.

For private areas (the bedroom) — 60 GHz mmWave (Seeed MR60FDA2):
purpose-built fall detection with no image at all.

---

## "Typical / unusual" patterns — designed

Frequency statistics in SQLite, **not an LLM**. A function along the lines
of `check_typical()`: how much the current activity differs from what is
usual for this person, this hour and this day of the week.

This is arithmetic over accumulated events. A language model isn't needed
here and would only add nondeterminism.
