import math
import time

import config

COMPASS = ("вправо", "вправо-вверх", "вверх", "влево-вверх",
           "влево", "влево-вниз", "вниз", "вправо-вниз")


def _name_and_score(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, str) and value.strip():
        return value.strip(), None
    return None, None


class TrackedPerson:
    def __init__(self, event_id):
        self.event_id = event_id
        self.camera = None
        self.identity = None
        self.identity_source = None
        self.identity_score = None
        self.start_time = None
        self.end_time = None
        self.current_zones = []
        self.entered_zones = []
        self.path = []
        self.shape = []
        self.has_clip = False
        self.has_snapshot = False
        self.ended = False

    def update(self, after, event_type=None):
        self.camera = after.get("camera") or self.camera
        self.start_time = after.get("start_time") or self.start_time
        self.end_time = after.get("end_time") or self.end_time
        self.current_zones = after.get("current_zones") or []
        self.entered_zones = after.get("entered_zones") or self.entered_zones
        self.path = after.get("path_data") or self.path
        self.has_clip = after.get("has_clip", self.has_clip)
        self.has_snapshot = after.get("has_snapshot", self.has_snapshot)

        ratio = after.get("ratio")
        frame_time = after.get("frame_time")
        if ratio and frame_time:
            self.shape.append((frame_time, round(ratio, 3)))

        self._read_identity(after)

        if event_type == "end":
            self.ended = True

    def _read_identity(self, after):
        name, score = _name_and_score(after.get("sub_label"))
        if name:
            self.identity = name
            self.identity_source = "face"
            self.identity_score = score
            return

        if self.identity_source == "face":
            return

        name, score = _name_and_score(after.get(config.ATTRIBUTE_FIELD))
        if name:
            self.identity = name
            self.identity_source = "attribute"
            self.identity_score = score

    @property
    def points(self):
        out = []
        for item in self.path:
            try:
                (x, y), t = item
                out.append((float(x), float(y), float(t)))
            except (TypeError, ValueError):
                continue
        return out

    @property
    def segments(self):
        pts = self.points
        result = []
        for a, b in zip(pts, pts[1:]):
            dist = math.hypot(b[0] - a[0], b[1] - a[1])
            dt = b[2] - a[2]
            glitch = dist > config.GLITCH_JUMP and 0 <= dt < config.GLITCH_TIME
            result.append((a, b, dist, dt, glitch))
        return result

    @property
    def duration(self):
        if not self.start_time:
            return 0.0
        end = self.end_time or time.time()
        return round(end - self.start_time, 1)

    @property
    def path_seconds(self):
        pts = self.points
        return round(pts[-1][2] - pts[0][2], 1) if len(pts) > 1 else 0.0

    @property
    def still_seconds(self):
        return round(max(0.0, self.duration - self.path_seconds), 1)

    @property
    def zone_route_list(self):
        route = []
        for zone in list(self.entered_zones) + list(self.current_zones):
            if not route or route[-1] != zone:
                route.append(zone)
        return route

    @property
    def zone_route(self):
        return " → ".join(self.zone_route_list)

    @property
    def path_length(self):
        return round(sum(s[2] for s in self.segments), 2)

    @property
    def clean_length(self):
        return round(sum(s[2] for s in self.segments if not s[4]), 2)

    @property
    def glitches(self):
        return sum(1 for s in self.segments if s[4])

    @property
    def displacement(self):
        pts = self.points
        if len(pts) < 2:
            return 0.0
        return round(math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]), 2)

    @property
    def frame_speed(self):
        moved = seconds = 0.0
        for a, b, dist, dt, glitch in self.segments:
            if glitch or dt <= 0:
                continue
            moved += dist
            seconds += dt
        return round(moved / seconds, 3) if seconds else 0.0

    @property
    def heading(self):
        for a, b, dist, dt, glitch in reversed(self.segments):
            if glitch or dist < config.MIN_STEP:
                continue
            return round(math.degrees(math.atan2(-(b[1] - a[1]), b[0] - a[0])) % 360)
        return None

    @property
    def heading_text(self):
        if self.heading is None:
            return "неизвестно"
        return COMPASS[round(self.heading / 45) % 8]

    @property
    def near_edge(self):
        pts = self.points
        if not pts:
            return None
        x, y, _ = pts[-1]
        m = config.EDGE_MARGIN
        if x < m:
            return "левый"
        if x > 1 - m:
            return "правый"
        if y < m:
            return "верхний"
        if y > 1 - m:
            return "нижний"
        return None

    @property
    def is_wandering(self):
        return (self.clean_length > config.WANDER_PATH
                and self.displacement < config.WANDER_MOVE)

    @property
    def posture(self):
        if not self.shape:
            return "неизвестно"
        ratio = self.shape[-1][1]
        if ratio < 0.5:
            return "стоит"
        if ratio > 1.2:
            return "лежит?"
        return "сидит/наклонился?"

    @property
    def snapshot_url(self):
        if not self.has_snapshot:
            return None
        return f"{config.FRIGATE_URL}/api/events/{self.event_id}/snapshot.jpg"

    @property
    def snapshot_clean_url(self):
        if not self.has_snapshot:
            return None
        return f"{config.FRIGATE_URL}/api/events/{self.event_id}/snapshot-clean.webp"

    @property
    def thumbnail_url(self):
        return f"{config.FRIGATE_URL}/api/events/{self.event_id}/thumbnail.jpg"

    @property
    def clip_url(self):
        """Ссылка на запись события у Frigate. Отдаём ВСЕГДА.

        Здесь стояла проверка has_clip, и она стоила почти половины всех
        событий: 431 из 928 в боевом логе выброшены с «КАДРОВ НЕТ» в ту же
        секунду, без единой попытки скачать.

        Причина в том, что has_clip приходит из MQTT-сообщения о КОНЦЕ
        события, а Frigate в этот момент ещё дописывает сегменты записи и
        флаг у него False. Позже он становится True, но второго сообщения
        не будет — и событие уже выброшено навсегда.

        Поэтому флагу не верим, а просто идём за записью. Есть она или
        нет — ответит сам Frigate, и скачивание умеет ждать и повторять."""
        return f"{config.FRIGATE_URL}/api/events/{self.event_id}/clip.mp4"

    @property
    def media(self):
        return {
            "clip": self.clip_url,
            "snapshot": self.snapshot_url,
            "snapshot_clean": self.snapshot_clean_url,
            "thumbnail": self.thumbnail_url,
        }

    def summary(self):
        when = (time.strftime("%d.%m %H:%M:%S", time.localtime(self.start_time))
                if self.start_time else "время неизвестно")
        score = f" {self.identity_score:.2f}" if self.identity_score else ""
        lines = [
            f"{self.identity or 'БЕЗ ИМЕНИ'} ({self.identity_source or '-'}{score})   [{self.camera}]",
            f"  {when}   событие {self.duration} с   "
            f"(шёл {self.path_seconds} с, стоял {self.still_seconds} с)",
            f"  маршрут:   {self.zone_route or 'вне зон'}",
            f"  движение:  {self.heading_text} ({self.heading}°)   "
            f"путь {self.path_length}   смещение {self.displacement}",
            f"  темп:      {self.frame_speed} кадра/с   поза: {self.posture}",
        ]
        if self.is_wandering:
            lines.append("  ТОПТАНИЕ НА МЕСТЕ")
        if self.near_edge:
            lines.append(f"  у края кадра ({self.near_edge}) — геометрии верить нельзя")
        if self.glitches:
            lines.append(f"  рывков рамки: {self.glitches} (исключены из скорости)")
        if self.clip_url:
            lines.append(f"  видео: {self.clip_url}")
        if self.snapshot_url:
            lines.append(f"  кадр:  {self.snapshot_url}")
        return "\n".join(lines)

    def vlm_payload(self):
        return {
            "event_id": self.event_id,
            "person": self.identity,
            "identified_by": self.identity_source,
            "confidence": self.identity_score,
            "camera": self.camera,
            "started": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_time))
                        if self.start_time else None),
            "duration_s": self.duration,
            "moving_s": self.path_seconds,
            "still_s": self.still_seconds,
            "zone_route": self.zone_route,
            "heading_deg": self.heading,
            "path_length": self.path_length,
            "displacement": self.displacement,
            "frame_speed": self.frame_speed,
            "wandering": self.is_wandering,
            "near_frame_edge": self.near_edge,
            "tracker_glitches": self.glitches,
            "clip": self.clip_url,
            "frame": self.snapshot_clean_url,
        }

    def __repr__(self):
        return f"TrackedPerson({self.identity}, {self.camera}, {self.zone_route})"
