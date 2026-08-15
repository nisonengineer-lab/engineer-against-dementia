# Architecture

## The big picture

```
Cameras (RTSP) ─┐
                ├─→  Frigate NVR  ─── MQTT ───→  Home Assistant  ──→  Telegram
mmWave sensor  ─┘      (GPU)                        (VM)          └─→  Push to the app
   (ESPHome)             │                            ↑
                         └── frames ──→ Ollama ───────┘
                                    (separate GPU)
```

Three machines, each with its own role:

| Node | What it does | Why it's separate |
|---|---|---|
| **NVR host** | Frigate: stream decoding, detection, faces, recording | Needs a GPU and constant uptime |
| **HA VM** | Rules, automations, MQTT broker, notifications | Home Assistant OS won't be installed alongside anything else |
| **GPU PC** | Ollama with the vision model | Heavy models can't sit on the same GPU as detection |

## Event flow

This is the path from "a person walked through the gate" to "the phone
rang":

```
1.  The camera serves an RTSP stream
2.  Frigate decodes it (in hardware, on the GPU) and runs the frame through YOLOv9
3.  A `person` object is found with a score above the threshold
4.  Check whether the bounding box falls inside the `Exit` zone
5.  In parallel: if the face is large and sharp enough — face recognition
6.  Frigate publishes the event to MQTT: topic `frigate/events`
7.  Home Assistant catches the event and checks the conditions:
       label == 'person'
       zone contains 'exit'   ← lowercased, see below
       camera == the right one
8.  Actions: a Telegram message (several chat_id values) + a push with
    channel: alarm_stream_max — a full-screen alert past silent mode
```

**The case-sensitivity trap.** In the Frigate config the zone is named
`Exit`. In the Home Assistant template the comparison is case-sensitive.
If you don't run it through `map('lower')`, the trigger won't fire —
silently, with no error in the logs. This cost several hours of
debugging.

## Splitting the detect / record streams

The yard camera has two inputs from the exact same URL, but with
different roles:

```yaml
inputs:
  - path: rtsp://.../av_stream
    roles: [detect]
  - path: rtsp://.../av_stream
    roles: [record]
```

The reason: detection needs low latency and is willing to lose quality;
recording needs the full bitrate and tolerates delay. On a single stream
those requirements conflict — either detection lags behind, or the
recording breaks up.

## Which component answers which question

This is the main architectural decision in the project. Every question is
handled by the cheapest component capable of answering it:

| Question | Component | Latency | Why not otherwise |
|---|---|---|---|
| Is there a person in frame? | YOLOv9 (ONNX, GPU) | ~30–80 ms | the basic operation |
| Who is it? | Frigate face recognition | ~100 ms | an LLM solves this 30× slower and worse |
| Which room are they in? | static `camera → zone` map | 0 | the room doesn't change, it's a config constant |
| Did they fall? | mmWave sensor / pose estimation | < 1 s | **an LLM is no good here:** 1–3 s per frame |
| What are they doing? | vision LLM (Qwen3-VL) | 10–30 s | nothing else can |
| Is this usual for them? | frequency statistics (SQLite) | — | this is arithmetic, not reasoning |

The temptation to hand everything to one big model exists, and it's
wrong: it will be slower, more expensive and less predictable in each of
these roles taken separately.

## Vision LLM: a window instead of a frame

Ollama doesn't accept a video file. The scheme is this:

```
clip  →  ffmpeg cuts N evenly spaced frames  →  batch into the `images` field
      →  the model reads it as a sequence in time  →  one line of description
```

Estimated budget: ~5 s per frame, 5 frames per batch ⇒ a ~25 s window.
This isn't a reaction to an event, it's **a diary entry**: "got up,
walked to the kitchen, came back". Hence the name of the "Journal" page
on the website.

The prompt is tightly constrained: describe only what's visible, don't
guess direction or intent. A model that's allowed to fill in the blanks
starts writing "went to the kitchen" where the person simply walked out
of frame.

## Private areas without a camera

The bedroom is covered by a 60 GHz mmWave sensor (Seeed MR60FDA2,
ESPHome out of the box, 3×3×3 m coverage). It sees presence, position and
falls, but produces no image. For a room where a camera is
inappropriate, this is the only acceptable option.

## Remote access

Cloudflare Tunnel, a permanent domain, a subdomain per service. No ports
are forwarded on the router — the tunnel opens an outbound connection
from the inside. `trusted_proxies` in Home Assistant is set to the host's
IP, otherwise HA sees every request as coming from the proxy and
authentication breaks.

What's reachable from outside is the **interface**, not the video stream.

## Resilience

A home system with no administrator. What has been needed so far:

- **cron health check** (every 5 minutes): checks that HA and Frigate are
  reachable, restarts the VM through `VBoxManage` and the container
  through `docker compose restart`. Runs as a regular user, not as root —
  the VM belongs to the user.
- **Disabling EEE on the network card** (`ethtool --set-eee ... off` +
  a systemd unit): this fixed intermittent network drops.
- **Blocking sleep** (`systemctl mask sleep.target suspend.target ...`):
  the laptop host must not go to sleep with the lid closed.
- **Known problem with no solution:** the host occasionally shuts down
  completely — not sleep, an actual power off. The bug is documented on
  Launchpad and looks like an ASUS BIOS/EC + Linux kernel combination. Not
  solvable in software. This is one of the reasons for moving to a
  Raspberry Pi 5.

## Where this is heading

The current host is a laptop, which is bad for a life-safety system. The
plan: Raspberry Pi 5 + AI HAT+ (Hailo-8, 26 TOPS) as the main NVR, with
the GPU machine left for heavy models only. The reasoning behind the
hardware choices is in [02-hardware.md](02-hardware.md).
