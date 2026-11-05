"""Bounded, shared factual evidence; never infer profit or strategy quality."""

import copy
import json
from collections import Counter


def date_of(state):
    clock = state.get("gamestate", {})
    date = [clock.get(key) for key in ("year", "season", "dayofmonth")]
    return date if None not in date else None


def bounded(value, limit=700):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return copy.deepcopy(value) if len(text) <= limit else {"truncated": True, "preview": text[:limit]}


def snapshot(state):
    player = state.get("player", {})
    result = {"date": date_of(state), "time": state.get("gamestate", {}).get("time"),
            **{key: bounded(player.get(key), 1200 if key == "inventory" else 160)
               for key in ("money", "stamina", "location", "position", "inventory")}}
    totals = Counter()
    for item in player.get('inventory', []):
        if item and item.get('name') and type(item.get('quantity')) is int:
            totals[item['name']] += item['quantity']
    result['inventory_totals'] = bounded(dict(totals), 1200)
    farming = state.get("farming")
    if isinstance(farming, dict) and isinstance(farming.get("soils"), list):
        # Do not leak the all-location soils returned by the transport into memory.
        visible = {tuple(tile["position"]) for tile in state.get("surroundingsdata", [])
                   if isinstance(tile.get("position"), (list, tuple))}
        soils = [soil for soil in farming["soils"] if isinstance(soil.get("position"), (list, tuple))
                 and tuple(soil["position"]) in visible]
        result["visible_soils"] = bounded(soils, 700)
    return result


class DailyExperience:
    """Archive every observed primitive; show only bounded recent evidence to the LLM."""

    def __init__(self):
        self.days = []
        self.previous = None
        self.evidence_ids = set()

    def record(self, event):
        if event.get("type") not in {"initial_observation", "step", "tool_primitive"} or not event.get("state"):
            return
        after = snapshot(event["state"])
        # Missing dates do not trigger reviews or invent a game day.
        if after["date"] is None:
            self.previous = after
            return
        before = self.previous
        new_day = not self.days or self.days[-1]["date"] != after["date"]
        if new_day:
            self.days.append({"date": after["date"], "start": after, "end": after,
                              "actions": Counter(), "failures": 0, "events": []})
        day = self.days[-1]
        day["end"] = after
        self.previous = after
        action = event.get("action")
        # Synthesized calls already emit their primitive events. Planning is not a game action.
        if not action or action in {"set_daily_plan", "define_tool", "test_tool", "run_tool", "finish"}:
            return
        result = event.get("result")
        if not isinstance(result, dict):
            return
        evidence_id = f"e{len(self.evidence_ids) + 1}"
        self.evidence_ids.add(evidence_id)
        cash_before, cash_after = (before or {}).get("money"), after.get("money")
        delta = cash_after - cash_before if type(cash_before) in (int, float) and type(cash_after) in (int, float) else None
        record = {"id": evidence_id, "step": event.get("step"), "action": action,
                  "arguments": bounded(event.get("arguments", {})), "result": bounded(result),
                  "before": before, "after": after, "observed_cash_delta": delta,
                  "crossed_day": bool(before and before["date"] != after["date"])}
        # Attribute a cross-day action to its starting day, retaining the next-day outcome.
        owner = self.days[-2] if record["crossed_day"] and len(self.days) > 1 else day
        owner["events"].append(record)
        owner["actions"][action] += 1
        owner["failures"] += int(result.get("ok") is False or result.get("return_value") is False)
        return evidence_id

    def context(self, review=False):
        days = []
        for day in self.days[-2:]:
            current = day is self.days[-1]
            count = 12 if review else (3 if current else 0)
            events = day["events"][-count:] if count else []
            receipts = sum(max(0, e['observed_cash_delta'] or 0) for e in day['events'])
            spending = sum(max(0, -(e['observed_cash_delta'] or 0)) for e in day['events'])
            elapsed = 0
            for event in day['events']:
                before, after = event.get('before') or {}, event['after']
                a, b = before.get('time'), after.get('time')
                if before.get('date') == after.get('date') and type(a) is int and type(b) is int:
                    elapsed += max(0, b//100*60+b%100 - (a//100*60+a%100))
            cash_events = [{'id':e['id'], 'action':e['action'], 'observed_cash_delta':e['observed_cash_delta']}
                           for e in day['events'] if e['observed_cash_delta']][-6:]
            facts = {'cash_increases':receipts, 'cash_decreases':spending,
                     'net_observed_cash_change':receipts-spending,
                     'same_day_observed_game_minutes':elapsed,
                     'cash_event_refs':cash_events,
                     'meaning':'Bookkeeping of observed deltas, including settlement; not per-activity profit. Time has ten-minute resolution and excludes cross-date intervals.'}
            days.append({**{key: value for key, value in day.items() if key != "events"},
                         'facts':facts,
                         "events": events, "omitted_events": len(day["events"]) - len(events)})
        result = {"notice": "Observed deltas are not causal profit estimates. Cross-day cash may be shipping settlement. "
                            "This is a partial bounded record; missing evidence is not evidence of no activity.",
                  "days": days}
        # A hard character bound keeps even large inventories/results from growing prompts unboundedly.
        while len(json.dumps(result, ensure_ascii=False)) > 18000 and any(day["events"] for day in days):
            largest = max(days, key=lambda day: len(day["events"]))
            largest["events"] = largest["events"][1:]
            largest["omitted_events"] += 1
        return copy.deepcopy(result)

    def export(self):
        return copy.deepcopy(self.days)
