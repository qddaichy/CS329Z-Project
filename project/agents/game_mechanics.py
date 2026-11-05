"""Shared operational guidance, deliberately excluding economic estimates."""


MECHANICS_VERSION = "3"

GAME_MECHANICS = """
Basic game mechanics (not a price or profit guide):
- Equip the appropriate tool before using it: the hoe tills soil, the watering
  can waters soil, the scythe cuts grass and some harvestable crops, the axe
  cuts wood, the pickaxe breaks stones, and a fishing rod casts into water.
  Check the observed target and game result; actions and yields are not guaranteed.
- Plant suitable seeds on tilled soil. Most ordinary crops need watering on days
  without rain to grow; mature crops can be harvested, with the required method
  depending on the crop. Check soil and crop observations rather than assuming
  maturity, growth duration or that every plant is harvestable by hand.
- The initial tilled patch is not a fixed limit: on suitable, accessible ground,
  clear obstacles if needed and use the hoe to till additional tiles, then plant
  seeds and water them. Check the actual ground, energy and successful results.
  More plots require more seed cost, labor and maintenance; choose scale based on
  available time and whether harvest and sale can happen before the deadline.
- Consider investing in useful tools, equipment and upgrades when available via
  the provided APIs, including fishing equipment or animal-production tools.
  Inspect the backpack, chests and actual shop options first to avoid duplicates.
  Some upgrades may require materials, money, waiting or a tool to be unavailable;
  their availability and support by the current APIs are not guaranteed. Verify
  menu options and results, and do not assume an upgrade is immediately usable.
- Farm animals need food and care. When conditions permit, animals can eat grass
  outside; otherwise they can eat Hay placed on a feeding bench. Some upgraded
  animal buildings have automatic feeding. Inspect housing, food and animal state;
  collect produce with the appropriate tool or interaction when available.
- Cutting farm grass with a scythe can produce Hay only if there is available
  storage in a Silo; it is not guaranteed on every cut and normally goes to the
  Silo, not directly into the backpack. Hay is animal feed, not a guaranteed
  source of sale income. Preserve grass when useful for grazing. Do not assume
  a Silo, open capacity, feed, building or animal is present; check observations.
- Items deposited in the farm shipping bin are sold through overnight settlement,
  not immediately. Confirm deposits and the next balance; shops may have different
  hours and accept different items. Keep unsold inventory separate from cash.
Use lookup_crop_reference for a shared, on-demand crop table: normal-quality
base sale price per item, base first-growth days, regrowth interval and seasons.
Reference numbers assume unmodified game data; they are not guaranteed actual
prices, yields, maturity or profit. Check quality, professions, fertilizer,
watering, special locations and observed game results. Unknown crops stay unknown.
Compare actions by their observed active in-game time as well as resources and
returns: include setup, travel, repeated maintenance, collection and selling.
Use start/end clock and action evidence when available; the clock is coarse, so
zero visible change is not proof an action was instantaneous. Separate passive
growth and overnight settlement from active work, track uncertainty, and consider
other work that can be done during passive time. Do not invent time measurements.
For a fixed-horizon profit task, choose investments and actions to maximize the
cash actually realized by the deadline, not simply the size of the farm or the
number of tools. Compare expected incremental revenue with purchase cost, active
labor, energy, opportunity cost, maintenance, growth/upgrade time and settlement;
account for uncertainty, liquidity and the remaining in-game days. Existing assets
are not free to maintain, and already-spent money is not a new cost. Investments
need not all pay back individually if they improve the overall deadline outcome.
These are general mechanics, not evidence that an action is profitable or even
available in this save. Learn actual prices, yields, active time, investment costs,
payback and relative profitability from observations and experience.
"""
