# Installation

Deploying from scratch on a host with an NVIDIA GPU and Ubuntu.

> These instructions describe the current configuration (x86 host with a
> GPU). The Raspberry Pi 5 + AI HAT+ variant will be added after the
> move — see [02-hardware.md](02-hardware.md).

## Prerequisites

- Ubuntu (tested on 26.04)
- NVIDIA GPU + drivers + `nvidia-container-toolkit`
- Docker and `docker compose`
- RTSP cameras, **with static IPs** (see below)
- VirtualBox — for the Home Assistant VM

Check that Docker can see the GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

---

## 1. Network and cameras

**Pin down the camera IPs.** With DHCP the address will drift sooner or
later and the Frigate config will break. Either a MAC reservation on the
router, or a static address on the camera itself.

Test RTSP before touching Frigate at all:

```bash
ffprobe -rtsp_transport tcp "rtsp://USER:PASSWORD@CAMERA_IP:554/h264/ch1/main/av_stream"
```

If it doesn't work here, Frigate won't help.

On EZVIZ the RTSP password is the verification code **on the sticker on
the body**, not the password for your account in the app. Also check that
`Local RTSP` is enabled: it turns itself off.

---

## 2. Frigate

```bash
git clone https://github.com/nisonengineer-lab/engineer-against-dementia.git
cd engineer-against-dementia/frigate

cp .env.example .env                   # camera and MQTT credentials
cp config.example.yaml config.yaml
```

Fill in `.env`. Passwords are **never written** into `config.yaml` —
Frigate substitutes the `{FRIGATE_*}` variables.

Build the detector:

```bash
../models/yolov9/build.sh s 320
```

Start it:

```bash
docker compose up -d
docker compose logs -f frigate
```

Interface: `http://HOST_IP:5000`

### Zones and masks

The coordinates in `config.example.yaml` come from one specific
installation. Copying them is pointless. Draw your own:
**Settings → Mask & Zone Editor**.

The order is:

1. **Mask first.** Everything that isn't your property — the street, the
   neighbour's yard, the tree canopy. Without a mask, passers-by generate
   alerts around the clock.
2. **Then zones.** Split them by meaning: "yard" (went out, but still on
   the property) and "gate" (alert) are different zones with different
   logic.
3. `inertia: 1` for the alert zone — a person crosses the gate in
   seconds. `inertia: 3` for everyday zones, so it doesn't twitch at
   someone walking past.

More detail: [04-configuration.md](04-configuration.md).

---

## 3. Home Assistant in a VM

Home Assistant OS is installed from an image in VirtualBox. Inside the
VM — the **Mosquitto broker** add-on, which is also the MQTT broker for
Frigate.

After installation:

- pin the VM's IP statically;
- create an MQTT user; its login and password go into `frigate/.env`;
- install the **Frigate** integration (via HACS) — it will pick up the
  cameras and events automatically;
- set `trusted_proxies` (see step 5).

Autostarting the VM without a graphical session — `ops/systemd/`.

---

## 4. Notifications

The Telegram bot and the Home Assistant automations have their own
document: [05-alerts.md](05-alerts.md).

---

## 5. External access (Cloudflare Tunnel)

Ports on the router are **not forwarded**. The tunnel opens an outbound
connection from inside the house.

Config: `ops/cloudflared/config.example.yml`, a subdomain per service.

**Required** in Home Assistant's `configuration.yaml`:

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 10.0.0.X        # IP of the host the tunnel arrives from
```

Without this, HA sees every request as coming from the proxy and
authentication breaks.

---

## 6. Resilience

Set up once, saves a lot of nights.

### Cron health check

`ops/scripts/check-ha-health.sh` — every 5 minutes it checks that HA and
Frigate are reachable and brings back whatever has crashed.

```bash
crontab -e
# */5 * * * * /usr/local/bin/check-ha-health.sh >> /var/log/ha-health.log 2>&1
```

**As a regular user, not as root** — the VirtualBox VM belongs to the
user, and `VBoxManage` won't see it from under root.

### Blocking sleep

The laptop host must not fall asleep with the lid closed:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

### Disabling EEE on the network card

Energy-Efficient Ethernet was causing intermittent network drops:

```bash
sudo ethtool --set-eee eno2 eee off
```

To make it survive a reboot — a systemd unit in `ops/systemd/`.

---

## Verification

| What to check | How |
|---|---|
| Frigate sees the cameras | UI on :5000, live picture from both |
| The detector works | walk in front of the camera, the detection counter goes up |
| MQTT is connected | `binary_sensor.CAMERA_person` entities appear in HA |
| The zone fires | step into the alert zone, an event shows up in `frigate/events` |
| The notification arrives | Telegram + push on the phone |
| The health check is alive | stop the container; it should come back up within 5 minutes |
| The tunnel works | open the domain over mobile data, not from the home network |

What to do if any of this didn't work —
[07-troubleshooting.md](07-troubleshooting.md).
