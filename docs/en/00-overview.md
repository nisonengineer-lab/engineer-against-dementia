# Overview

## The problem

Caring for an elderly person at home doesn't fail for lack of care, it
fails for lack of information. A relative can't watch 24 hours a day. And
the events that matter last seconds:

- a person with dementia walks out the gate and keeps going;
- a person falls and can't get up or call for help;
- a person with schizophrenia hasn't slept for a third night in a row —
  and that's the first sign the condition is deteriorating, long before
  it becomes visible in conversation.

All three have one thing in common: **the recording isn't what matters,
the timely notification is**. A camera that writes to disk and stays
silent is useful to an investigator, not to a caregiver.

## Who this is for

Three real people the system was built around:

| | Condition | Key risk | What covers it |
|---|---|---|---|
| **Grandfather, 85** | dementia | leaving the house, falling | yard camera, gate zone, alert |
| **Father** | schizophrenia | disrupted night sleep | indoor camera, zones, night logic |
| **Grandmother** | planned | — | third deployment site |

## Principles

**1. Local isn't a feature, it's a precondition.**
We're talking about video from my parents' bedroom. It does not go to
anyone else's server under any circumstances. What goes out is the
notification text and, on request, a single frame into a protected chat.
This also settles the subscription question: cloud services charge money
every month for something the hardware already does on its own.

**2. Notification beats recording.**
Every design decision is checked against one question: does it shorten
the time between the event and a human finding out about it. A nice
archive nobody opens doesn't help with that.

**3. Response speed dictates the tool.**

| Task | Time budget | Tool |
|---|---|---|
| Fall | < 1 s | mmWave sensor, pose estimation |
| Walking out the gate | 1–3 s | object detection + zone |
| "What is the person doing" | 10–30 s | vision LLM |
| "Is this usual for them" | minutes–days | SQLite statistics |

An LLM does not go where an instant reaction is needed. Three seconds per
frame is an eternity for a fall.

**4. Every question is answered by the cheapest component that can.**

- Who is this? → face recognition. Not an LLM.
- Which room? → a static `camera → zone` map in the config. Not an LLM.
- What are they doing? → here, an LLM, because there's nothing else.
- Is this usual? → frequency statistics. Not an LLM.

**5. A false alarm costs more than it looks.**
A system that wakes you ten times a night for a cat and the shadow of a
tree gets switched off on the third night. After that it saves nobody.
That's why masks, thresholds and per-zone logic aren't fine-tuning —
they're the condition for the system staying switched on at all.

**6. It has to fix itself.**
A home system with no on-duty administrator. A camera drops off, a
container crashes, a VM hangs — these aren't hypothetical scenarios,
they're Monday. A cron health check brings services back up with no human
involved.

## Boundaries

**This is not a medical device.** It does not diagnose, does not treat,
does not give treatment advice. It does not replace a caregiver, a doctor
or a person being there.

**Consent is mandatory.** Everyone who appears on camera knows about the
system and has agreed to it. Areas where a camera is inappropriate are
covered by a mmWave sensor that sees presence and falls but produces no
image.

## Next

- [Architecture](01-architecture.md) — how it's put together
- [Hardware](02-hardware.md) — what it runs on
- [Decisions](08-decisions.md) — what we already ran into
