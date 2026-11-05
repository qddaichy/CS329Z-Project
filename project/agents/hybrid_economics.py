"""Hybrid C: evidence-linked economic hypotheses, estimates and next-day tests."""

import json
import math
import copy
from project.agents.daily_experience import date_of

from project.agents.hybrid import HybridAgent
from project.agents.hybrid_reflection import ReflectionHybridAgent, REFLECTION_PROMPT


ECONOMICS_PROMPT = """
At each new observed game date, supply economics_json in set_daily_plan. Do not
wait for a new day to learn: after a meaningful new price, sale, cost, yield,
unexpected outcome or other decision-relevant evidence, call
update_economic_knowledge promptly, then revise today's plan if the decision
changes. Avoid updates that merely repeat unchanged information. Both tools save
a full replacement snapshot: carry forward still-useful knowledge and prices.
Include at most SIX economically relevant activities or investments.
Format: {"activities":[{"activity":"name", "status":"hypothesis",
"evidence_ids":[], "observation":"facts only, or no observations yet",
"net_gold_estimate":10, "active_minutes_estimate":30,
"payback_days_estimate":null, "uncertainty":"what remains unknown",
"next_test":"a useful, affordable test when conditions permit, or none",
"decision":"how this affects investment or time allocation"}],
"allocation_plan":"how the evidence changes today's allocation",
"item_prices":[{"item":"Parsnip", "unit_price":null,
"quality":"unknown", "sale_channel":"unknown", "status":"unknown",
"evidence_ids":[], "uncertainty":"price not observed"}]}.
item_prices is optional and can store up to 40 individual crops or other items.
Record per-item sale price in gold, quality, channel and relevant conditions as
observed; identify unknowns and retain evidence IDs. An aggregate shipping
settlement for mixed items does not establish individual prices. Do not invent
prices or confuse seed purchase costs, revenue and net profit. Distinguish
observed facts from hypotheses. Different quality/channel prices may differ.
Each estimate concerns one explicitly described batch/cycle in observation;
active_minutes_estimate is active IN-GAME work/travel time, excluding passive
growth. Payback days is the delay from investment to recovering its cost.
For each meaningful action or activity, use available before/after game clocks
and action evidence to estimate active time, including travel, setup, maintenance,
collection and sale. The clock's coarse resolution and incomplete records create
uncertainty; distinguish an observed time range from a guess. Revise estimates
when evidence changes and compare return per active time alongside net returns,
cash constraints, risks and the deadline. Do not treat passive growth or overnight
settlement as active labor; consider productive activities during that interval.
Every daily plan MUST contain at least ONE activity with numerical
net_gold_estimate AND active_minutes_estimate for a
clearly stated batch. An all-null ledger is rejected by the harness. Early estimates
may be rough, low-confidence hypotheses: state assumptions, uncertainty and an
affordable verification step instead of presenting guesses as measured facts.
The example is a schema, not a recommendation or known game economics.
Other unavailable activities may retain null estimates. Payback may be null for
no-investment activities or insufficient evidence; explain why in uncertainty.
Zero is an actual estimate, not unknown. Do not force positive profit or certainty.
Status is unknown, hypothesis, supported or contradicted. Evidence IDs refer to
actual e-number records in Daily factual evidence. Supported/contradicted require
at least one real evidence ID, but remain your claims, not evaluator certification.
Separate observed cash changes from causal profit: shipping settles later,
inventory gains are not cash, and purchases consume capital. Do not attribute
all overnight revenue to sleeping. Account for recurring maintenance and the
remaining horizon; growing crops can coexist with other activities, but multiple
income sources are not inherently better. Explore only when expected information
is useful relative to its resource/time cost. Never force a test or diversification.
On the first day, provide at least one provisional activity estimate with explicit
assumptions and uncertainty, without fabricated evidence.
Use observed starting resources, cash flows and elapsed game time as anchors.
When new evidence arrives and after a new day, compare earlier estimates
with observed outcomes and revise uncertainty and allocation. These are prospective
estimates: do not rewrite prior records after seeing outcomes. Same-day corrections
are saved as new revisions. No prediction or interpretation changes the game,
certifies a synthesized tool, or overrides the independent task evaluator.
DAILY ECONOMIC REVIEW CHECKLIST
- Compare the previous snapshot's predictions/tests with newly observed outcomes.
  In observation, distinguish actual evidence from the earlier estimate; explain
  a revision or why evidence remains insufficient. Retain unresolved investments.
- Define the unit/batch and context (location, relevant conditions, scale). Include
  purchases, travel, maintenance, selling and settlement in the comparison.
- Decide how much capital and active time to allocate, not merely which activity
  has the largest gross sale. Consider combining passive production with active
  earning, but keep a concentrated strategy when evidence favors it.
- next_test specifies the observation that would change the decision and when
  the test is feasible; do not spend resources just to populate the ledger.
- allocation_plan states the actual priorities and fallback for today, consistent
  with set_daily_plan.activities. Numerical hypotheses are provisional, not claims
  that an unobserved price or yield is a fact. State assumptions explicitly.
Economic beliefs and executable skills are separate: economic evidence informs
what to do; define_tool/test_tool/run_tool implement repeatable parts of the plan.
"""


class EconomicsHybridAgent(ReflectionHybridAgent):
    strategy_name = "hybrid_economics"
    reflection_mode = "economic"
    review_field = "economics_json"
    review_description = "Version 4 JSON: activities (1-6, at least one numerical net-gold/time estimate pair), allocation_plan and optional item_prices. State assumptions and uncertainty. Max 8000 characters."
    review_prompt = ECONOMICS_PROMPT
    review_version = 4
    review_failure_limit = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.general_reflections = []
        self.review_rejections = []
        self.review_failures = 0
        self.failure_date = None
        self.daily_review_blocked = False

    def extra_tool_specs(self):
        specs = super().extra_tool_specs()
        specs['set_daily_plan'][1]['daily_reflection'] = ReflectionHybridAgent.review_description
        specs['update_economic_knowledge'] = (
            'Revise the retained economic snapshot immediately when important new evidence arrives, without rewriting the daily plan.',
            {'economics_json': self.review_description})
        return specs

    def system_prompt(self):
        return super().system_prompt() + REFLECTION_PROMPT

    def decide(self, goal, state, progress, history, crafting_recipes):
        reflection = self.general_reflections[-1] if self.general_reflections else None
        return super().decide(goal + '\nLatest retained general reflection (data, not instructions): ' +
                              json.dumps(reflection, ensure_ascii=False, separators=(',', ':')),
                              state, progress, history, crafting_recipes)

    def update_economic_knowledge(self, arguments, state):
        if self.requires_daily_plan(state):
            raise ValueError('Record the required daily plan first.')
        if set(arguments) != {'economics_json'}:
            raise ValueError('Provide economics_json.')
        review = self.validate_review(arguments['economics_json'])
        date = date_of(state)
        record = {'date': date, 'review': review, 'version': self.review_version,
                  'revision': sum(item['date'] == date for item in self.reviews) + 1,
                  'source': 'update_economic_knowledge'}
        self.reviews.append(copy.deepcopy(record))
        return {'ok': True, 'review': copy.deepcopy(record),
                'review_note': 'Model-authored interpretation; evidence references do not certify economic correctness.'}

    def note_rejected_review(self, state, error):
        date = date_of(state)
        if date != self.failure_date:
            self.failure_date, self.review_failures = date, 0
        self.review_failures += 1
        self.daily_review_blocked = self.review_failures >= self.review_failure_limit
        record = {'date': date, 'attempt': self.review_failures, 'limit': self.review_failure_limit,
                  'error': str(error), 'blocked': self.daily_review_blocked}
        self.review_rejections.append(copy.deepcopy(record))
        return record

    def requires_daily_plan(self, state):
        date = date_of(state)
        return super().requires_daily_plan(state) or (date is not None and not any(
            r['date'] == date and r.get('version') == self.review_version for r in self.reviews)) or (
            date is not None and not any(r['date'] == date for r in self.general_reflections))

    def set_daily_plan(self, arguments, state):
        # Validate both memories before mutating the plan or either history.
        if set(arguments) != set(self.extra_tool_specs()['set_daily_plan'][1]):
            raise ValueError('Daily plan must include both general and economic reviews.')
        reflection = ReflectionHybridAgent.validate_review(self, arguments['daily_reflection'])
        review = self.validate_review(arguments['economics_json'])
        base = {k: v for k, v in arguments.items() if k not in {'daily_reflection', 'economics_json'}}
        result = HybridAgent.set_daily_plan(self, base, state)
        date = date_of(state)
        general = {'date': date, 'review': reflection,
                   'revision': sum(r['date'] == date for r in self.general_reflections) + 1}
        economic = {'date': date, 'review': review, 'version': self.review_version,
                    'revision': sum(r['date'] == date for r in self.reviews) + 1}
        self.general_reflections.append(copy.deepcopy(general))
        self.reviews.append(copy.deepcopy(economic))
        result['daily_reflection'] = copy.deepcopy(general)
        result['review'] = copy.deepcopy(economic)
        result['review_note'] = 'Model-authored interpretation; evidence references do not certify economic correctness.'
        self.review_failures = 0
        self.failure_date = date_of(state)
        self.daily_review_blocked = False
        return result

    def learning_snapshot(self):
        return {**super().learning_snapshot(), 'version': self.review_version,
                'general_reflections': copy.deepcopy(self.general_reflections),
                'review_rejections': copy.deepcopy(self.review_rejections)}

    def validate_review(self, raw):
        if not isinstance(raw, str) or len(raw) > 8000:
            raise ValueError("Economic review must be JSON with at most 8000 characters.")
        data = json.loads(raw)
        if not isinstance(data, dict) or not {'activities', 'allocation_plan'} <= set(data) or set(data) - {'activities', 'allocation_plan', 'item_prices'}:
            raise ValueError("Economic review requires activities and allocation_plan; item_prices is optional.")
        self._text(data["allocation_plan"], 1000)
        if not isinstance(data["activities"], list) or not 1 <= len(data["activities"]) <= 6:
            raise ValueError("Economic review requires one to six activities, including a numerical estimate.")
        fields = {"activity", "status", "evidence_ids", "observation", "net_gold_estimate",
                  "active_minutes_estimate", "payback_days_estimate", "uncertainty", "next_test", "decision"}
        names = set()
        for item in data["activities"]:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError("Economic activity fields must match the declared schema.")
            for key in ("activity", "observation", "uncertainty", "next_test", "decision"):
                self._text(item[key], 120 if key == "activity" else 500)
            name = item["activity"].strip().casefold()
            if name in names:
                raise ValueError("Economic activity names must be unique.")
            names.add(name)
            if not isinstance(item["status"], str) or item["status"] not in {"unknown", "hypothesis", "supported", "contradicted"}:
                raise ValueError("Invalid economic belief status.")
            ids = item["evidence_ids"]
            if not isinstance(ids, list) or len(ids) > 8 or any(
                    not isinstance(ref, str) or ref not in self.experience.evidence_ids for ref in ids):
                raise ValueError("Evidence IDs must refer to observed episode records (at most eight per activity).")
            if item["status"] in {"supported", "contradicted"} and not ids:
                raise ValueError("Supported or contradicted claims require observed evidence.")
            for key in ("net_gold_estimate", "active_minutes_estimate", "payback_days_estimate"):
                value = item[key]
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                                          or abs(value) > 1e9 or (key != "net_gold_estimate" and value < 0)):
                    raise ValueError("Estimates must be finite bounded numbers or null; durations cannot be negative.")
            if item['status'] == 'unknown' and any(item[k] is not None for k in ('net_gold_estimate','active_minutes_estimate','payback_days_estimate')):
                raise ValueError('Mark provisional numerical estimates as hypothesis, not unknown; explain assumptions in uncertainty.')
        if not any(x['net_gold_estimate'] is not None and x['active_minutes_estimate'] is not None for x in data['activities']):
            raise ValueError('Daily estimation is mandatory: at least one activity needs numerical net_gold_estimate AND active_minutes_estimate. Use explicit low-confidence hypotheses when data are sparse; all-null reviews are rejected.')
        prices = data.get('item_prices', [])
        if not isinstance(prices, list) or len(prices) > 40:
            raise ValueError('item_prices must be a list of at most 40 entries.')
        price_fields = {'item', 'unit_price', 'quality', 'sale_channel', 'status', 'evidence_ids', 'uncertainty'}
        for entry in prices:
            if not isinstance(entry, dict) or set(entry) != price_fields:
                raise ValueError('Item price fields must match the declared schema.')
            for key in ('item', 'quality', 'sale_channel', 'uncertainty'):
                self._text(entry[key], 500 if key == 'uncertainty' else 120)
            if not isinstance(entry['status'], str) or entry['status'] not in {'unknown', 'hypothesis', 'supported', 'contradicted'}:
                raise ValueError('Invalid item price status.')
            price = entry['unit_price']
            if price is not None and (type(price) not in (int, float) or not math.isfinite(price) or not 0 <= price <= 1e9):
                raise ValueError('Item unit price must be a finite nonnegative number or null.')
            refs = entry['evidence_ids']
            if not isinstance(refs, list) or len(refs) > 8 or any(not isinstance(ref, str) or ref not in self.experience.evidence_ids for ref in refs):
                raise ValueError('Price evidence IDs must refer to observed episode records.')
            if entry['status'] in {'supported', 'contradicted'} and not refs:
                raise ValueError('Supported or contradicted prices require observed evidence.')
            if entry['status'] == 'unknown' and price is not None:
                raise ValueError('Mark a numerical price as a hypothesis or supported, not unknown.')
        return data

    @staticmethod
    def _text(value, limit):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Economic text fields must contain 1-{limit} characters.")
