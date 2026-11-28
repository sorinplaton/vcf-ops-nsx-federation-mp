"""An offline evaluator for the alert definitions of the pack.

It reads pack/content/alertdefs/nsxfederation_alerts.xml and decides, for the output of one collection cycle, which
alert definitions VCF Operations would raise on which objects. It follows the parts of the format the pack uses:
static metric thresholds, property conditions, message events (regex), symptom sets with "and" and negated symptoms,
and an "or" of symptom sets. Two things it cannot judge offline are reported, not guessed:

  * dynamic thresholds (type "dtmetric"): VCF Operations learns the usual range from weeks of data. A symptom of this
    kind counts as "met" only when the metric it watches is present, and the alert is then listed as "dt" (it can fire
    once a range is learnt), never as fired.
  * wait and cancel cycles: a cycle is judged on its own. run_cycles() in the scenarios repeats a fault for as many
    cycles as the alert waits.

The output of a cycle is the dict that conftest.run() returns: {object kind: {name: {"m", "p", "e"}}}.
"""
import operator
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS_XML = os.path.join(ROOT, "pack", "content", "alertdefs", "nsxfederation_alerts.xml")
_NUM = {">": operator.gt, "<": operator.lt, "=": operator.eq, "!=": operator.ne, ">=": operator.ge, "<=": operator.le}


def _short(ref: str) -> str:
    return ref.rsplit("-", 1)[-1] if ref.startswith(("AlertDefinition-", "SymptomDefinition-")) else ref


class AlertDefs:
    def __init__(self, path: str = ALERTS_XML) -> None:
        root = ET.parse(path).getroot()
        self.symptoms: Dict[str, Dict[str, Any]] = {}
        for s in root.iter("SymptomDefinition"):
            state = s.find("State")
            cond = state.find("Condition")
            self.symptoms[s.get("id")] = {
                "id": _short(s.get("id")), "kind": s.get("resourceKind"), "name": s.get("name"),
                "wait": int(s.get("waitCycle") or 1), "severity": state.get("severity"), "cond": dict(cond.attrib)}
        self.alerts: Dict[str, Dict[str, Any]] = {}
        for a in root.iter("AlertDefinition"):
            state = a.find("State")
            sets = []  # an "or" of sets; each set is an "and" of (symptom id, negated)
            for ss in state.iter("SymptomSet"):
                if ss.get("ref"):
                    sets.append([(ss.get("ref"), False)])
                else:
                    sets.append([(x.get("ref"), x.get("negateCondition") == "true") for x in ss.findall("Symptom")])
            self.alerts[_short(a.get("id"))] = {
                "id": _short(a.get("id")), "kind": a.get("resourceKind"), "name": a.get("name"), "sets": sets,
                "wait": max(self.symptoms[r]["wait"] for s in sets for r, neg in s if not neg)}

    # ---- one symptom on one object: True, False, or "dt" (dynamic threshold, metric present)
    def symptom(self, ref: str, obj: Dict[str, Any]) -> Any:
        c = self.symptoms[ref]["cond"]
        kind = c.get("type")
        if kind == "metric":
            v = obj["m"].get(c["key"])
            return v is not None and _NUM[c["operator"]](float(v), float(c["value"]))
        if kind == "dtmetric":
            return "dt" if obj["m"].get(c["key"]) is not None else False
        if kind == "property":
            v = obj["p"].get(c["key"])
            if v is None:
                return False
            v, want = str(v), c["value"]
            if c["operator"] == "contains":
                return want in v
            return (v == want) if c["operator"] == "=" else (v != want)
        if kind == "msg_event":
            # VCF Operations matches with Java regexes, which take an inline flag anywhere in the pattern; Python
            # takes it only at the start. Java's matches() is a full match.
            pattern = c["eventMsg"]
            flags = re.S if "(?s)" in pattern else 0
            rx = re.compile(pattern.replace("(?s)", ""), flags)
            return any(rx.fullmatch(m or "") for m in obj.get("e", []))
        raise ValueError(f"condition type {kind} of {ref} is not handled")

    def alert_on(self, alert_id: str, obj: Dict[str, Any]) -> Any:
        """True (fires), "dt" (would fire once a dynamic range is learnt), or False."""
        best: Any = False
        for s in self.alerts[alert_id]["sets"]:
            vals = []
            for ref, neg in s:
                v = self.symptom(ref, obj)
                vals.append((not v) if neg else v)
            if all(v is True for v in vals):
                return True
            if all(v for v in vals):
                best = "dt"
        return best

    def evaluate(self, cycle: Dict[str, Any]) -> Dict[str, List[Tuple[str, Any]]]:
        """alert id -> [(object name, True | "dt")] for every alert met on at least one object."""
        out: Dict[str, List[Tuple[str, Any]]] = {}
        for aid, a in self.alerts.items():
            for name, obj in (cycle.get(a["kind"]) or {}).items():
                v = self.alert_on(aid, obj)
                if v:
                    out.setdefault(aid, []).append((name, v))
        return out

    def fired(self, cycle: Dict[str, Any]) -> Dict[str, List[str]]:
        """alert id -> objects, only the alerts that fire (dynamic-threshold ones left out)."""
        return {a: [n for n, v in hits if v is True] for a, hits in self.evaluate(cycle).items()
                if any(v is True for _, v in hits)}


_DEFS: Optional[AlertDefs] = None


def defs() -> AlertDefs:
    global _DEFS
    if _DEFS is None:
        _DEFS = AlertDefs()
    return _DEFS
