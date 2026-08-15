---
name: Bug report
about: Something is broken
title: ''
labels: bug
assignees: ''
---

**⚠️ Do not paste passwords, tokens, real IP addresses, or images of people.**
Redact before posting.

## What happened

## What you expected

## Steps to reproduce

1.
2.
3.

## Environment

- Host / OS:
- Frigate version:
- Home Assistant version:
- Detector (GPU / Hailo / CPU):
- Cameras:

## Logs

<details>
<summary>docker compose logs frigate</summary>

```
paste here (redacted)
```

</details>

<details>
<summary>MQTT payload, if relevant</summary>

```
mosquitto_sub -h HOST -u USER -P PASS -t 'frigate/events' -v
```

</details>
