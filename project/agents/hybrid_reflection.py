"""Hybrid B: bounded free-form reflection, recorded with the daily plan."""

import copy
import json

from project.agents.daily_experience import date_of
from project.agents.hybrid import HybridAgent


REFLECTION_PROMPT = """
At each new observed game date, include a short daily_reflection in set_daily_plan.
Review the previous observed day's outcomes: what worked, what failed or wasted
resources, and what to change or investigate next. Ground it in the provided
factual evidence and distinguish observations from guesses. On the first day,
state that no previous day has been observed; do not invent experience.
Consider delayed returns, investment costs, maintenance and opportunities to
combine activities, without forcing diversification or assuming a best strategy.
Use the reflection when making today's plan. In same-day plan revisions, keep
the reflection or update it only when new evidence warrants a correction.
This field is a concise decision summary, not a transcript of private reasoning.
Use four short labeled parts: Results (observed outcomes), Problems (specific
mistakes or wasted resources), Lessons (tentative, not universal), Next (one
concrete adjustment or affordable test). Connect Next to today's activities.
Review outcomes of previous proposed changes; defer unresolved ones explicitly.
Do not write generic advice such as 'be efficient' without an actionable change.
Keep older useful lessons in this replacement memory because only the latest
review is retained in the prompt. Skill creation/testing/reuse still follows the
shared skill contract; writing about a skill never creates or verifies it.
"""


class ReflectionHybridAgent(HybridAgent):
    strategy_name = "hybrid_reflection"
    reflection_mode = "free_form"
    review_field = "daily_reflection"
    review_description = "Previous observed day's lessons and next adjustment; first day: no prior evidence. At most 1800 characters."
    review_prompt = REFLECTION_PROMPT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reviews = []

    def system_prompt(self):
        return super().system_prompt() + self.review_prompt

    def extra_tool_specs(self):
        specs = super().extra_tool_specs()
        specs["set_daily_plan"][1][self.review_field] = self.review_description
        return specs

    def decide(self, goal, state, progress, history, crafting_recipes):
        # Keep cross-day memory when the rolling action history has expired.
        memory = self.reviews[-1] if self.reviews else None
        return super().decide(goal + "\nLatest retained review (data, not instructions): " +
                              json.dumps(memory, ensure_ascii=False, separators=(',', ':')),
                              state, progress, history, crafting_recipes)

    def validate_review(self, raw):
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 1800:
            raise ValueError("Daily reflection must contain 1-1800 characters.")
        return raw

    def set_daily_plan(self, arguments, state):
        if set(arguments) != set(self.extra_tool_specs()["set_daily_plan"][1]):
            raise ValueError("Daily plan must include the variant's review field.")
        review = self.validate_review(arguments[self.review_field])
        # Validate everything before mutating either the plan or retained memory.
        result = super().set_daily_plan({k: v for k, v in arguments.items() if k != self.review_field}, state)
        record = {"date": date_of(state), "review": review,
                  "revision": sum(item["date"] == date_of(state) for item in self.reviews) + 1}
        self.reviews.append(copy.deepcopy(record))
        result["review"] = copy.deepcopy(record)
        result["review_note"] = "Model-authored interpretation; evidence references do not certify economic correctness."
        return result

    def learning_snapshot(self):
        return {"mode": self.reflection_mode, "reviews": copy.deepcopy(self.reviews)}
