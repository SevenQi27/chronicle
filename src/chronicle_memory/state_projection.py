"""Project facts, rules and plans from their temporal histories in Neo4j."""
import json
from collections import defaultdict
from datetime import datetime
from .models import stamp, state_key


class StateProjection:
    @staticmethod
    def _event(row):
        return {"event_id":row["event_id"],"source_id":row["source_id"],
                "recorded_at":row["recorded_at"], **json.loads(row["body"])}

    def states(self, *, at: datetime | None = None, known_at: datetime | None = None, subject: str | None = None, view=None,logical_ids=None):
        view=view or self.capture_view(at,known_at)
        at,known_at=view.at,view.known_at
        _, rows = self._visible(known_at, subject,logical_ids=logical_ids,revision=view.revision)
        groups = defaultdict(list)
        for row in rows:
            groups[(row["subject"],row["key"])].append(row)
        output = []
        for (subj,key), history in groups.items():
            eligible = [row for row in history if row["effective_at"] <= stamp(at)]
            if not eligible:
                continue
            start = max(row["effective_at"] for row in eligible)
            selected = [row for row in eligible if row["effective_at"] == start]
            live = [row for row in selected if not json.loads(row["body"])["expires_at"]
                    or datetime.fromisoformat(json.loads(row["body"])["expires_at"])>at]
            if live:
                selected = live
            next_times = [row["effective_at"] for row in history if row["effective_at"] > start]
            candidates = defaultdict(list)
            for row in selected:
                payload = json.loads(row["body"])
                # Different wording or corroborating sources do not create a conflict.
                semantic = {k:payload[k] for k in ("kind","value","expires_at","due_at","review_at","plan_status")}
                candidates[json.dumps(semantic,ensure_ascii=False,sort_keys=True)].append(self._event(row))
            result = {"subject":subj,"key":key,"valid_from":start,
                      "valid_to":min(next_times) if next_times else None,
                      "status":"conflict" if len(candidates)>1 else "active",
                      "candidates":[{"memory":items[-1],"evidence":items} for items in candidates.values()]}
            if len(candidates)==1:
                memory = result["candidates"][0]["memory"]
                result["memory"] = memory
                expiry = memory["expires_at"]
                if expiry and (result["valid_to"] is None or stamp(datetime.fromisoformat(expiry)) < result["valid_to"]):
                    result["valid_to"] = stamp(datetime.fromisoformat(expiry))
                if expiry and datetime.fromisoformat(expiry) <= at:
                    result["status"] = "expired"
                elif memory["kind"] == "plan" and memory["plan_status"] != "open":
                    result["status"] = memory["plan_status"]
                elif memory["kind"] == "plan" and memory["due_at"] and datetime.fromisoformat(memory["due_at"]) <= at:
                    result["status"] = "awaiting_outcome"
                elif memory["review_at"] and datetime.fromisoformat(memory["review_at"]) <= at:
                    result["status"] = "needs_review"
                elif memory["kind"] == "plan":
                    result["status"] = "scheduled"
            elif not live:
                result["status"] = "expired"
            output.append(result)
        return output

    def state(self, subject: str, key: str, at=None, known_at=None,*,view=None):
        return next((s for s in self.states(at=at, known_at=known_at, subject=subject,view=view,
                    logical_ids=[state_key(subject,key)]) if s["key"]==key),
                    {"subject":subject,"key":key,"status":"unknown","candidates":[]})

    def timeline(self, subject: str, key: str, known_at=None):
        snapshot=self.capture_view(known_at=known_at)
        known_at=snapshot.known_at
        rows, active = self._visible(known_at, subject, key,revision=snapshot.revision)
        times = {row["effective_at"] for row in active}
        for row in active:
            payload=json.loads(row["body"])
            for field in ("expires_at","review_at","due_at"):
                if payload[field]:
                    times.add(stamp(datetime.fromisoformat(payload[field])))
        projected=[]
        previous=None
        for point in sorted(times):
            view=self.state(subject,key,view=snapshot.at_time(datetime.fromisoformat(point)))
            signature=json.dumps(view,ensure_ascii=False,sort_keys=True)
            if signature!=previous:
                projected.append({"at":point,**view})
                previous=signature
        return {"subject":subject,"key":key,"known_at":stamp(known_at),
                "events":[self._event(row) for row in rows],
                "states":projected}
