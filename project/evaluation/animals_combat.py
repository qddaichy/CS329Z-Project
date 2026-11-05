"""Evaluators using observable animal transitions and game kill statistics."""


class PetEvaluator:
    def __init__(self, count=1):
        if type(count) is not int or count < 1:
            raise ValueError("Animal count must be positive.")
        self.count, self.previous, self.completed_ids = count, None, set()

    def update(self, state):
        entities, clock = state.get("entities"), state.get("gamestate")
        if not isinstance(entities, dict) or not entities.get("available") or not isinstance(entities.get("animals"), list):
            raise RuntimeError("Animal observations unavailable; update StateOnlyMod.")
        if not isinstance(clock, dict) or any(clock.get(k) is None for k in ("year", "season", "dayofmonth")):
            raise RuntimeError("Game date unavailable.")
        animals = {}
        for animal in entities["animals"]:
            identifier = animal.get("id")
            if not identifier or identifier in animals or type(animal.get("petted_today")) is not bool:
                raise RuntimeError("Incomplete or duplicate animal observation.")
            animals[identifier] = animal["petted_today"]
        day = tuple(clock[k] for k in ("year", "season", "dayofmonth"))
        location = entities.get("location")
        if self.previous and self.previous[0] == location and self.previous[1] == day:
            old = self.previous[2]
            self.completed_ids.update(identifier for identifier, value in animals.items()
                                      if identifier in old and not old[identifier] and value)
        self.previous = (location, day, animals)
        quantity = len(self.completed_ids)
        return {"kind": "pet", "completed": quantity >= self.count, "quantity": quantity, "required": self.count}


class CombatEvaluator:
    def __init__(self, name, count=1):
        if not name or type(count) is not int or count < 1:
            raise ValueError("Monster name and positive count required.")
        self.name, self.count, self.initial = name, count, None

    def update(self, state):
        combat = state.get("combat")
        if not isinstance(combat, dict) or not combat.get("available") or not isinstance(combat.get("monsters_killed"), list):
            raise RuntimeError("Kill statistics unavailable; update StateOnlyMod.")
        rows = combat["monsters_killed"]
        if any(not isinstance(row.get("name"), str) or type(row.get("count")) is not int or row["count"] < 0 for row in rows):
            raise RuntimeError("Invalid monster kill statistics.")
        value = sum(row["count"] for row in rows if row["name"].casefold() == self.name.casefold())
        if self.initial is None:
            self.initial = value
        quantity = max(0, value - self.initial)
        return {"kind": "kill", "target": self.name, "completed": quantity >= self.count,
                "quantity": quantity, "required": self.count}
