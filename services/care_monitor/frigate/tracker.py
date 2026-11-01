import logging

from .person import TrackedPerson

log = logging.getLogger("tracker")


class EventTracker:
    def __init__(self, target, cameras=None):
        self.target = (target or "").strip().casefold()
        self.cameras = cameras
        self.people = {}

    def handle(self, topic, data):
        if topic.endswith("tracked_object_update"):
            self._late_update(data)
            return None
        return self._event(data)

    def _event(self, data):
        after = data.get("after") or {}
        event_id = after.get("id")

        if not event_id or after.get("label") != "person":
            return None
        if self.cameras and after.get("camera") not in self.cameras:
            return None

        person = self.people.get(event_id)
        if person is None:
            person = TrackedPerson(event_id)
            self.people[event_id] = person
            log.info("новое событие %s  %s  (в работе %d)",
                     self._short(event_id), after.get("camera"), len(self.people))
        person.update(after, data.get("type"))

        if not person.ended:
            return None

        del self.people[event_id]

        if person.identity and person.identity.strip().casefold() == self.target:
            log.info("КАРТОЧКА ГОТОВА %s  %s (%s %s)  %s  %.1f с  (в работе %d)",
                     self._short(event_id), person.identity, person.identity_source,
                     person.identity_score, person.zone_route or "вне зон",
                     person.duration, len(self.people))
            return person

        log.info("событие %s закрыто, это не цель: %s  (в работе %d)",
                 self._short(event_id), person.identity or "без имени", len(self.people))
        return None

    @staticmethod
    def _short(event_id):
        return event_id.split("-")[-1]

    def _late_update(self, data):
        person = self.people.get(data.get("id"))
        if person is None:
            return
        if data.get("type") == "face" and data.get("name"):
            log.info("догнало распознавание лица для %s: %s (%s)",
                     self._short(person.event_id), data["name"], data.get("score"))
            person.identity = data["name"]
            person.identity_source = "face"
            person.identity_score = data.get("score")
