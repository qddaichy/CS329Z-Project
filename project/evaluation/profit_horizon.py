"""Score cash return at a fixed date boundary, including final shipping settlement."""

import copy


def absolute_day(state):
    clock = state.get("gamestate") or {}
    year, season, day = (clock.get(key) for key in ("year", "season", "dayofmonth"))
    seasons = {"spring": 0, "summer": 1, "fall": 2, "winter": 3}
    if (type(year) is not int or year < 1 or type(day) is not int or not 1 <= day <= 28
            or not isinstance(season, str) or season.lower() not in seasons):
        raise RuntimeError("A valid game date is required for the profit horizon.")
    return (year - 1) * 112 + seasons[season.lower()] * 28 + day - 1


class ProfitHorizonEvaluator:
    requires_full_horizon = True

    def __init__(self, days):
        if type(days) is not int or days < 1:
            raise ValueError("Profit horizon must be a positive number of game days.")
        self.days = days
        self.initial_day = self.last_day = self.initial_balance = None
        self.daily_balances = []
        self.final_result = None

    def update(self, state):
        if self.final_result is not None:
            return copy.deepcopy(self.final_result)
        day = absolute_day(state)
        cash = state.get("player", {}).get("money")
        if type(cash) is not int or cash < 0:
            raise RuntimeError("A valid cash balance is required for the profit horizon.")
        if self.last_day is not None and day < self.last_day:
            raise RuntimeError("Game date moved backwards during the profit experiment.")
        if self.initial_day is None:
            self.initial_day, self.initial_balance = day, cash
        clock = state["gamestate"]
        if day != self.last_day:
            self.daily_balances.append({"date": [clock["year"], clock["season"], clock["dayofmonth"]],
                                        "first_observed_balance": cash, "last_observed_balance": cash})
        self.daily_balances[-1]["last_observed_balance"] = cash
        self.last_day = day
        elapsed = day - self.initial_day
        result = {"kind": "profit_horizon", "completed": elapsed >= self.days,
                  "initial_balance": self.initial_balance, "current_balance": cash,
                  "net_gain": cash - self.initial_balance,
                  "elapsed_game_days": elapsed, "required_game_days": self.days,
                  "remaining_game_days": max(0, self.days - elapsed),
                  "daily_balances": copy.deepcopy(self.daily_balances),
                  "completion_meaning": "Reached deadline; cash return is the score, not proof of optimality."}
        if result["completed"]:
            result["terminal_inventory"] = copy.deepcopy(state.get("player", {}).get("inventory", []))
            self.final_result = copy.deepcopy(result)
        return result
