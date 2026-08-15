# Roadmap

The order is set by risk, not by what's more interesting to work on.

---

## Currently in progress

### Hardware reliability
The current host is a laptop that **occasionally powers itself off
completely** ([07-troubleshooting.md](07-troubleshooting.md)). For a
system that's supposed to wake you at night, that's a disqualifying
property.

- [ ] Raspberry Pi 5 + AI HAT+ (Hailo-8, 26 TOPS) as the main NVR host
- [ ] Recompile the detector into `.hef` — ONNX does not execute on Hailo
- [ ] Replace EZVIZ with PoE cameras (Reolink RLC-520A / Dahua IPC-T5442T-ZE)

### Face recognition quality
- [ ] Clean the training set from 500+ photos down to 20–50 good ones per person
- [ ] Daily work with Train — a habit, not a one-time setup
- [ ] Re-check the thresholds after cleaning the training set

### Open bugs
- [ ] Diagnose the 502 on auth after enabling `classification`
- [ ] Find a replacement for the Yandex.Station for voice alerts
      ([ADR-009](08-decisions.md))

---

## Next

### Fall detection
The most significant uncovered risk.

- [ ] Connect 2× Seeed MR60FDA2 (60 GHz mmWave) via ESPHome — bedroom, no camera
- [ ] Pose estimation (YOLOv8n-pose) on the common-area cameras
- [ ] Confirmation logic: sensor trigger + no movement for N seconds

Why not a bounding box heuristic —
[ADR-006](08-decisions.md).

### Vision LLM in production
The scripts work standalone, they're not wired into the event stream.

- [ ] `activity_watcher`: clip from Frigate → frames → description → into HA
- [ ] Write descriptions into a database, not just send them as a notification
- [ ] Rate-limit the calls: the LLM must not run on every event

### Journal and patterns
- [ ] SQLite: events, zones, faces, activity descriptions
- [ ] `check_typical()` — frequency statistics for "typical / unusual", **not an LLM**
- [ ] A daily summary instead of a stream of individual notifications
- [ ] A "Journal" page on the project site

### Smarter alerting
- [ ] Different logic by time of day
- [ ] Escalation: no response within N minutes → next recipient
- [ ] Night-time path lighting (smart lights) — reducing fall risk, not monitoring

---

## Later

### Health
- [ ] Withings ScanWatch 2 + BPM Connect, official HA integration
      ([02-hardware.md](02-hardware.md))
- [ ] Blood pressure and heart rate trends next to activity on one timeline

### Voice interaction
- [ ] Voice cloning (Coqui XTTS-v2) for talking to my father
- [ ] ⚠️ **Condition:** a consultation with his treating psychiatrist about
      LLM dialogue at night. The question isn't an engineering one
      ([ADR-010](08-decisions.md))

### Third site
- [ ] Deploy at my grandmother's — a test that the system can be installed
      in a new place from the documentation, not from the author's memory

---

## Long term: a product

Not a global startup. A small local business for the Russian-speaking
community in Israel: installing and supporting care systems for elderly
relatives.

Ballpark: a kit at ~2500–3500 ₪ one-time + ~250–300 ₪/month for support.

What has to be ready before the first paying customer:

- [ ] Installation fits into one day following a checklist
- [ ] The system survives a week without intervention — proven, not assumed
- [ ] False alarms at a level where nobody switches it off on the third night
- [ ] An interface a relative can understand: what happened, without reading logs
- [ ] A clear position on data and consent, in Russian and in Hebrew
- [ ] A backup alerting channel for when the internet is down

The most underrated item here is the third one. A technically perfect
system that got switched off because of night-time false alarms saves zero
people.
