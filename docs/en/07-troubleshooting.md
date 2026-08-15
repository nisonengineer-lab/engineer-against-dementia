# Troubleshooting

Real problems from this installation and what fixed them. Not hypothetical
ones.

**Debugging principle:** look at logs and payloads, don't guess from the
config. Almost everything on this list looked like one thing and turned
out to be another.

---

## Detection "works" but finds nobody

**Symptom:** Frigate started, the picture is coming through, no errors in
the logs, the detection counter sits at zero with a person in frame.

**Cause:** the `yolov9-s.pt` weights were used instead of
`yolov9-s-converted.pt`. The output tensor structure is different,
`post_process_yolo` silently returns an empty list.

**Fix:** rebuild the model from `-converted.pt` — `models/yolov9/build.sh`
does it correctly.

---

## The automation doesn't fire, no errors

**Symptom:** a person enters the zone, the event is in MQTT, the
automation stays silent. In the trace — it stopped at a condition.

**Cause:** the case of the zone name. In the config it's `Exit`, in the
template it's compared against `'exit'`. Jinja is case-sensitive.

**Fix:**

```jinja
{{ 'exit' in trigger.payload_json['after']['entered_zones'] | map('lower') | list }}
```

**How to find it:** subscribe to the topic and look at the actual payload:

```bash
mosquitto_sub -h MQTT_HOST -u USER -P PASSWORD -t 'frigate/events' -v
```

---

## The entire face training set was lost

**Symptom:** after `docker compose down && up` recognition doesn't know
anyone. 500+ training photos gone.

**Cause:** the `./media:/media/frigate` volume was missing. The face
training set lives in `/media/frigate`, not in `/config`. Without an
external volume it lives inside the container and dies with it.

**Fix:**

```yaml
volumes:
  - ./config:/config
  - ./media:/media/frigate    # ← mandatory
```

Not recoverable. Check yours **before** you recreate the container for the
first time.

---

## 502 on the Frigate auth service

**Symptom:** after adding the `classification` section to the config,
Frigate starts returning 502 on the authentication endpoint.

**Status:** ⚠️ **not diagnosed**.

**Workaround:** comment out the whole `classification` block and restart
the container.

If you've figured it out — an issue is welcome.

---

## Can't find the "Train" tab for face training

**Symptom:** in the Frigate UI there's nowhere to add a photo to the
training set.

**Cause:** **"Recent Recognitions" is a hidden dropdown, not a tab.** You
have to click on the text with the number itself. There is no separate
"Train" button next to it.

A usability problem officially acknowledged by the developers. Until you
know it, it looks like the feature simply isn't there.

---

## Camera gone: RTSP turned itself off

**Symptom:** an EZVIZ camera was in the system and stopped serving the
stream.

**Cause:** **`Local RTSP` in the camera settings turns itself off.**

**Fix:** go into the app and turn it back on. There is no durable
solution — it's one of the reasons for replacing EZVIZ with PoE cameras
([02-hardware.md](02-hardware.md)).

**Checking the stream bypassing Frigate:**

```bash
ffprobe -rtsp_transport tcp "rtsp://USER:PASSWORD@CAMERA_IP:554/h264/ch1/main/av_stream"
```

The EZVIZ RTSP password is the verification code **from the sticker on the
body**, not the account password from the app.

---

## Camera gone: the IP moved

**Cause:** DHCP handed out a different address.

**Fix:** a MAC reservation on the router or a static address on the
camera. Mandatory for all cameras, no exceptions.

---

## Periodic network drops

**Symptom:** the network on the host drops for a few seconds, with no
visible cause, irregularly.

**Cause:** Energy-Efficient Ethernet on the network card.

**Fix:**

```bash
sudo ethtool --set-eee eno2 eee off
```

To survive a reboot — a systemd unit in `ops/systemd/`.

---

## Home Assistant breaks authentication through the tunnel

**Symptom:** through the Cloudflare Tunnel login fails / an error about an
untrusted proxy.

**Cause:** `trusted_proxies` isn't set. HA sees all requests as coming
from a single address — the proxy.

**Fix:**

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 10.0.0.X    # IP of the host the tunnel comes from
```

---

## The host sleeps when the lid is closed

**Fix:**

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

---

## The host powers itself off completely

**Symptom:** the machine isn't sleeping, it's an actual **power off**.
Irregular, no pattern, nothing sensible in the logs.

**Status:** ❌ **not solved.**

A known documented bug (Launchpad), looks like the ASUS BIOS/EC + Linux
kernel combination. Not fixable in software.

**Workaround:** move to a Raspberry Pi 5 as the main host. For a system
that's supposed to wake you at night, "occasionally powers off" is a
disqualifying property.

---

## The health check doesn't bring the VM up

**Symptom:** the script runs, but the VM with HA doesn't start.

**Cause:** the script was run as root. The VirtualBox VM belongs to a
regular user, and `VBoxManage` under root simply doesn't see it.

**Fix:** cron of the **regular user**, not root.

---

## Where to look

```bash
# Frigate
docker compose logs -f frigate

# MQTT events in real time
mosquitto_sub -h MQTT_HOST -u USER -P PASSWORD -t 'frigate/#' -v

# Home Assistant
# Settings → System → Logs, the trace of a specific automation
# in Settings → Automations → ⋮ → Traces

# Checking RTSP bypassing everything
ffprobe -rtsp_transport tcp "rtsp://..."

# GPU
nvidia-smi
watch -n1 nvidia-smi
```
