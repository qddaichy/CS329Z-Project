"""Count verified, distinct changes to soil and crops in observed locations."""


class FarmingEvaluator:
    """Count transitions, never pre-existing state or a model's success claim."""

    def __init__(self, kind, target=None, count=1):
        if kind not in {"till", "sow", "water", "fertilize"} or count < 1:
            raise ValueError("Unsupported farming evaluator or invalid count.")
        if kind in {"sow", "fertilize"} and not target:
            raise ValueError(f"{kind} requires a target item name.")
        self.kind, self.target, self.count = kind, target, count
        self.previous = None
        self.completed_tiles = set()

    def update(self, state):
        farming = state.get("farming")
        clock = state.get("gamestate")
        if not isinstance(farming, dict) or not farming.get("available") or not isinstance(farming.get("soils"), list):
            raise RuntimeError("Farming observations unavailable; update and restart StateOnlyMod.")
        if not isinstance(clock, dict) or any(clock.get(k) is None for k in ("year", "season", "dayofmonth")):
            raise RuntimeError("Game date unavailable; update and restart StateOnlyMod.")
        location = farming.get("location")
        if not location or location != state["player"].get("location"):
            raise RuntimeError("Farming observation has a missing or mismatched location.")
        soils = {}
        for soil in farming["soils"]:
            point = soil.get("position")
            if not isinstance(point, list) or len(point) != 2 or any(type(x) is not int for x in point):
                raise RuntimeError("Farming observation has an invalid soil position.")
            point = tuple(point)
            if point in soils or type(soil.get("watered")) is not bool or "crop" not in soil:
                raise RuntimeError("Farming observation has duplicate or incomplete soil data.")
            soils[point] = soil
        day = (clock["year"], clock["season"], clock["dayofmonth"])
        if self.previous is not None and self.previous[0] == location:
            _, previous_day, before = self.previous
            for point, current in soils.items():
                old = before.get(point)
                crop = current["crop"]
                old_crop = old["crop"] if old else None
                changed = False
                if self.kind == "till":
                    changed = old is None
                elif self.kind == "sow":
                    changed = (old_crop is None and isinstance(crop, dict)
                               and str(crop.get("seed_name") or "").casefold() == self.target.casefold())
                elif self.kind == "water":
                    changed = (previous_day == day and old is not None and isinstance(old_crop, dict)
                               and isinstance(crop, dict) and old_crop.get("seed_id")
                               and old_crop.get("seed_id") == crop.get("seed_id")
                               and not old["watered"] and current["watered"])
                elif self.kind == "fertilize":
                    old_id = old.get("fertilizer_id") if old else None
                    changed = (current.get("fertilizer_id") != old_id
                               and str(current.get("fertilizer_name") or "").casefold() == self.target.casefold())
                if changed:
                    self.completed_tiles.add((location, *point))
        self.previous = (location, day, soils)
        quantity = len(self.completed_tiles)
        return {"completed": quantity >= self.count, "quantity": quantity,
                "required": self.count, "kind": self.kind, "target": self.target}
