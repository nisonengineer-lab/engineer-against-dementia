# Hardware

## What's running right now

| Component | What's installed | Assessment |
|---|---|---|
| NVR host | ASUS TUF Dash F15 laptop (FX516PC), RTX 3050 4GB, 14 GB RAM, Ubuntu | Works, but not what this should live on |
| Yard camera | EZVIZ H3, Wi-Fi, RTSP | ⚠️ unsatisfactory |
| Entrance camera | EZVIZ E6, Wi-Fi, RTSP | ⚠️ unsatisfactory |
| LLM machine | Separate GPU PC (Windows), Ollama | Works |

### Why the laptop is a bad host

It occasionally **shuts down completely** — it doesn't go to sleep, it
powers off. The bug is documented on Launchpad, the cause hasn't been
established, and it looks like an ASUS BIOS/EC + Linux kernel
combination. Not fixable in software.

For a system that's supposed to wake you at night, "occasionally shuts
down" is a disqualifying property. Hence the move to a Pi 5.

### Why EZVIZ are bad cameras

Not "cheap but tolerable" — specific failures:

- weak substream, poorly suited to detection;
- **Local RTSP turns itself off** in the settings — the camera simply
  stops serving the stream until you go in and switch it back on;
- the IP drifts over DHCP and the config breaks;
- Wi-Fi instead of PoE: an extra point of failure and an extra power
  cable.

The RTSP password on EZVIZ is the **verification code on the sticker on
the body**, not your account password. It can only be changed by
factory-resetting the camera.

## Ordered / in transit

| Component | What for |
|---|---|
| **Raspberry Pi 5** | New NVR host: low power draw, predictable, doesn't shut itself down |
| **GeeekPi AI HAT+ 26 TOPS** (Hailo-8) | Hardware detection accelerator, with a metal case and active cooling |
| **2× Seeed MR60FDA2** | 60 GHz mmWave, purpose-built fall detection, ESPHome out of the box, 3×3×3 m coverage — for the bedroom, **no camera** |

## Planned purchases

- **Cameras to replace EZVIZ** — PoE, not Wi-Fi:
  - Reolink RLC-520A, 5 MP — the baseline option
  - EmpireTech / Dahua IPC-T5442T-ZE, varifocal — where a 10–15 m range is needed
- **Smart lighting** (Yeelight / Xiaomi) — night lighting along the route when someone gets out of bed. Less about monitoring, more about reducing fall risk.

## Settled hardware questions

Verified, so nobody has to work it out again:

**The AI HAT+ only works with a Raspberry Pi 5.**
It needs PCIe. Not a Pi 2/3/4. Not an Orange Pi (except for the separate
"bare" Hailo M.2 module — that's not the official HAT).

**One Pi 5 = one AI HAT.**
Physically one PCIe slot. You can't put a second Hailo on the same board.

**13 TOPS (Hailo-8L) is enough** for 3–5 cameras with detection and face
recognition. 26 TOPS is taken as headroom: more cameras, or several
models at once.

**The GPU for heavy models stays on the big PC.**
The hybrid architecture isn't down to poverty, it's by design: an LLM
won't fit on a Pi either in memory or in speed, while an accelerator on
the Pi is sufficient for detection.

## Medical devices — research conclusions

Nothing has been bought. Conclusions, so this doesn't have to be
researched again:

### Recommended

**Withings ScanWatch 2 + BPM Connect** — the best option overall:

- official Home Assistant integration;
- a **real cuff** for blood pressure (BPM Connect), FDA-cleared, not an optical estimate from the wrist;
- one-time purchase, no subscription.

### Rejected, and why

| Device | Reason for rejection |
|---|---|
| Omron HeartGuide | discontinued |
| Samsung Galaxy Watch | doesn't give a real blood pressure measurement — the manufacturer says so itself |
| Hilo Band / Aktiia | expensive **mandatory** subscription + an age contraindication above 85. My grandfather is 85 — that's a blocker, not a detail |
| Garmin | expensive, monochrome screen, unofficial and unstable API |
| Amazfit | considered as a budget option for heart rate (Zepp2Hass, community integration), but no good for blood pressure |

The key criterion: **blood pressure is measured with a cuff**. Anything
promising blood pressure from the wrist by optical means is not a
measuring instrument for an 85-year-old with real risks.
