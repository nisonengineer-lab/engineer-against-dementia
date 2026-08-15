# Decisions and cancelled directions (ADR)

The most useful document in the repository. This is where the dead ends I
have already walked into are recorded, with the reasons — so I don't walk
into them again and so nobody else wastes time on them.

---

## ADR-001 · Local, not cloud

**Status:** accepted · not up for revision

We're talking about a video stream from my parents' bedroom. Sending it to
someone else's server was never on the table at any stage — not for
convenience, not to save on hardware.

The side effect turned out to be economic: cloud subscriptions charge
money every month for processing that hardware in the house does by
itself. For the future business model (~250–300 ₪/month for support) that
is the difference between reselling someone else's service and having your
own product.

---

## ADR-002 · Telegram instead of WhatsApp/Twilio

**Status:** accepted · replaced a working solution

WhatsApp via the Twilio Sandbox **worked**. Dropped for two reasons:

- the Sandbox session expires every **72 hours** — you can't build an
  alerting system on a channel that turns itself off three times a week;
- moving to production Twilio is expensive with a constant stream of
  notifications.

Telegram Bot API: free, no sessions, sends to several chats at once,
supports photos.

**Gotcha:** the `target` parameter in the `telegram_bot.send_message`
service is deprecated. Use `chat_id`.

---

## ADR-003 · Face enhancement via GPEN/GFPGAN — CANCELLED

**Status:** ❌ cancelled for good

**Idea:** a separate RTSP proxy that runs the stream through GPEN/GFPGAN
to improve face quality before recognition.

**Result:** technically it worked. After a long fight with `CUDA_HOME`,
incompatible torch/nccl versions and mediamtx configuration — the stream
was flowing, faces were being enhanced.

**Why cancelled:** GPU load ~92%. Choppy stream. Direct competition for
the GPU with Frigate itself, the very thing it was all being done for.
Enhanced faces bought at the price of missed detections — a losing trade.

**Conclusion:** don't push this path. Raise recognition quality through
training set discipline and thresholds, not through post-processing the
stream.

---

## ADR-004 · YOLOv9-t → YOLOv9-s

**Status:** implemented

`yolov9-t` missed people in difficult poses: lying on the couch, partly
under a blanket, half behind furniture. For a general-purpose surveillance
system that's a detail. Here those are exactly the poses that are the
target event.

**Mandatory:** weights with the **`-converted.pt`** suffix. Regular `.pt`
files produce a different output tensor structure, `post_process_yolo` in
Frigate returns an empty list — **with no error in the logs**. The system
looks like it's running and sees nobody.

---

## ADR-005 · Division of responsibility between components

**Status:** accepted · defines all further development

Each question is handled by the cheapest component that can handle it:

| Question | Who | Who does NOT |
|---|---|---|
| Who is it? | face recognition | ❌ LLM — slower and worse |
| Which room? | static `camera → zone` map | ❌ LLM — it's a config constant |
| Did they fall? | mmWave / pose estimation | ❌ **LLM — 1–3 s per frame, unacceptable** |
| What are they doing? | vision LLM | — |
| Is this typical? | frequency statistics | ❌ LLM — it's arithmetic |

The temptation to hand everything to one big model exists and it's a
mistake.

---

## ADR-006 · Pose estimation, not a bounding box heuristic

**Status:** designed, not implemented

The simple heuristic "bbox wider than tall ⇒ the person is lying down"
breaks on camera angle: from a ceiling angle a standing person also
produces a wide bbox.

The solution is YOLOv8n-pose: skeleton keypoints are robust to the angle.

---

## ADR-007 · Object Classification instead of sub_label

**Status:** configured, ⚠️ hit a problem

Identification by silhouette and clothing as a fallback for when the face
isn't visible (Frigate 0.17+, custom MobileNetV2).

**`classification_type: attribute`, not `sub_label`** — with the
`sub_label` type the result conflicts with face recognition, which writes
into the same field.

**Problem:** after adding the `classification` section Frigate started
returning 502 on the auth service. Not fully diagnosed. The section is
left in the example config with a note; on a 502 it has to be commented
out.

---

## ADR-008 · Withings, not a smartwatch

**Status:** researched, not purchased

One criterion: **blood pressure is measured with a cuff**. Anything that
promises blood pressure from the wrist by optical means is not a
measuring instrument for an 85-year-old with real risks.

Withings ScanWatch 2 + BPM Connect: official HA integration, an
FDA-cleared cuff, one-time purchase.

Rejected: Omron HeartGuide (discontinued), Samsung Galaxy Watch (not a
real measurement — the manufacturer admits it), Hilo/Aktiia (expensive
mandatory subscription + contraindicated over 85), Garmin (expensive,
unstable unofficial API). Details — [02-hardware.md](02-hardware.md).

---

## ADR-009 · Voice alerts via Yandex.Station — BLOCKED

**Status:** ⏸ on hold

YandexStation authorization via HACS hangs at the QR confirmation step —
presumably geoblocking from Israel.

Tried: free VPNs (Proton, VPN Gate) — never worked reliably. A paid PIA
VPN was bought and installed, then **removed by the owner's decision**:
building a permanent alerting channel on top of a VPN crutch is a bad
idea, a proper solution comes first.

**Open.** Alternatives to evaluate: local TTS (Piper) via HA + any speaker
with media_player support, an ESP32 device with a speaker.

---

## ADR-010 · Voice cloning — agreed, deferred

**Status:** ⏸ deliberately deferred

Idea: synthesizing the owner's voice (Coqui XTTS-v2 on a machine with a
GPU) for talking to my father during moments of night-time anxiety. My
father knows about the idea, gave his consent and is interested.

**Condition before rollout:** a consultation with his treating
psychiatrist specifically about LLM dialogue at night. Technical readiness
is not enough here — the question isn't an engineering one.

---

## ADR-011 · Two volumes in docker-compose for Frigate

**Status:** implemented after data loss

```yaml
volumes:
  - ./config:/config
  - ./media:/media/frigate   # ← without this volume the face training set is lost
```

The face training photo set lives in `/media/frigate`, not in `/config`.
Without an external volume all of its contents die with the container on
`docker compose down/up`.

**500+ training photos were already lost this way.** That line wasn't
added as a precaution.

---

## ADR-012 · Frigate NVR as the foundation

**Status:** accepted

The alternatives (Shinobi, Agent DVR, a custom OpenCV pipeline) lost for
one reason: Frigate gives you "detection + zones + MQTT + event-based
recording" out of the box, and the MQTT integration with Home Assistant is
first-class, not through a shim.

---

## Open questions

| Question | Status |
|---|---|
| 502 on auth after enabling `classification` | not diagnosed |
| Voice alerts | ADR-009, need an alternative to Yandex |
| The host powers itself off | not solved, worked around by moving to a Pi 5 |
| Cleaning the face training set from 500+ down to 20–50 good photos per person | in progress |
