"""Require a verified task and a minimum number of elapsed in-game days."""


class MultiDayEvaluator:
    def __init__(self, evaluator, min_days):
        if evaluator is None or type(min_days) is not int or min_days < 1:
            raise ValueError("A task evaluator and positive day count are required.")
        self.evaluator = evaluator
        self.min_days = min_days
        self.initial_day = None
        self.last_day = None

    def update(self, state):
        clock = state.get("gamestate")
        if not isinstance(clock, dict):
            raise RuntimeError("Game date is unavailable for the multi-day evaluator.")
        year, day, season = clock.get("year"), clock.get("dayofmonth"), clock.get("season")
        seasons = {"spring": 0, "summer": 1, "fall": 2, "winter": 3}
        if (type(year) is not int or year < 1 or type(day) is not int or not 1 <= day <= 28
                or not isinstance(season, str) or season.lower() not in seasons):
            raise RuntimeError("Game date is invalid for the multi-day evaluator.")
        absolute = (year - 1) * 112 + seasons[season.lower()] * 28 + day - 1
        if self.initial_day is None:
            self.initial_day = absolute
        if self.last_day is not None and absolute < self.last_day:
            raise RuntimeError("Game date moved backwards during the episode.")
        self.last_day = absolute
        base = self.evaluator.update(state)
        elapsed = absolute - self.initial_day
        return {**base, "task_completed": base["completed"], "elapsed_game_days": elapsed,
                "required_game_days": self.min_days,
                "completed": base["completed"] and elapsed >= self.min_days}
