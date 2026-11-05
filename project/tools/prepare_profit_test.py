"""Prepare a disposable 500-gold fixture; administrative actions never reach the agent."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import copy
import hashlib
import xml.etree.ElementTree as ET

from dotenv import dotenv_values
import psutil

from project.paths import DATA_ROOT, ENV_FILE, REPO_ROOT


def add_fixture_chickens(save_path):
    """Add two deterministic adult chickens to a disposable farming-save copy."""
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
    tree = ET.parse(save_path)
    root = tree.getroot()
    # xsd appears in attribute *values* (e.g. xsi:type="xsd:int"), which
    # ElementTree cannot detect and would otherwise omit from the root element.
    root.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
    coops = [b for b in root.findall('.//buildings/Building') if b.findtext('buildingType') == 'Deluxe Coop']
    if len(coops) != 1:
        raise ValueError("Expected exactly one Deluxe Coop in the farming fixture.")
    coop = coops[0]
    indoor = coop.find('indoors')
    tables = [indoor.find('animals'), indoor.find('Animals/SerializableDictionaryOfInt64FarmAnimal')]
    residents = indoor.find('animalsThatLiveHere')
    if any(table is None for table in tables) or residents is None:
        raise ValueError("Coop animal dictionaries or resident list are unavailable.")
    existing_ids = {int(a.findtext('myID')) for a in root.findall('.//FarmAnimal') if a.findtext('myID')}
    ids = [1694954662719916835, 1694954662719916836]
    if existing_ids.intersection(ids):
        raise ValueError("Fixture animal IDs are already present.")
    occupancy = int(coop.findtext('currentOccupants'))
    capacity = int(coop.findtext('maxOccupants'))
    if occupancy + len(ids) > capacity:
        raise ValueError("Coop does not have room for the fixture chickens.")
    for table in tables:
        originals = [entry for entry in table.findall('item')
                     if entry.findtext('value/FarmAnimal/type') == 'White Chicken']
        if len(originals) != 1:
            raise ValueError("Expected one original chicken in each coop animal dictionary.")
        for index, animal_id in enumerate(ids, start=2):
            entry = copy.deepcopy(originals[0])
            animal = entry.find('value/FarmAnimal')
            entry.find('key/long').text = str(animal_id)
            animal.find('myID').text = str(animal_id)
            animal.find('name').text = f'Chicken_{index}'
            animal.find('displayName').text = f'Chicken_{index}'
            animal.find('Position/X').text = str(576 + (index - 1) * 64)
            table.append(entry)
    for animal_id in ids:
        ET.SubElement(residents, 'long').text = str(animal_id)
    coop.find('currentOccupants').text = str(occupancy + len(ids))
    tree.write(save_path, encoding='utf-8', xml_declaration=True)
    return ids


def optimize_fixture(save_path):
    """Remove pig and rabbit and move existing animal tools into the backpack."""
    ET.register_namespace('xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    ET.register_namespace('xsd', 'http://www.w3.org/2001/XMLSchema')
    tree = ET.parse(save_path)
    root = tree.getroot()
    root.set('xmlns:xsd', 'http://www.w3.org/2001/XMLSchema')
    barns = [b for b in root.findall('.//buildings/Building') if b.findtext('buildingType') == 'Deluxe Barn']
    if len(barns) != 1:
        raise ValueError('Expected exactly one Deluxe Barn in the fixture.')
    barn = barns[0]
    indoor = barn.find('indoors')
    tables = [indoor.find('animals'), indoor.find('Animals/SerializableDictionaryOfInt64FarmAnimal')]
    residents = indoor.find('animalsThatLiveHere')
    if residents is None or any(table is None for table in tables):
        raise ValueError('Barn animal dictionaries or resident list are unavailable.')
    pigs = [[e for e in table.findall('item') if e.findtext('value/FarmAnimal/type') == 'Pig'] for table in tables]
    if any(len(matches) != 1 for matches in pigs):
        raise ValueError('Expected one pig in each barn animal dictionary.')
    pig_ids = [matches[0].findtext('key/long') for matches in pigs]
    if pig_ids[0] is None or pig_ids[0] != pig_ids[1]:
        raise ValueError('Barn pig dictionaries disagree.')
    resident = [entry for entry in residents.findall('long') if entry.text == pig_ids[0]]
    if len(resident) != 1:
        raise ValueError('Pig residency is inconsistent.')
    for table, matches in zip(tables, pigs):
        table.remove(matches[0])
    residents.remove(resident[0])
    barn.find('currentOccupants').text = str(int(barn.findtext('currentOccupants')) - 1)

    coops = [b for b in root.findall('.//buildings/Building') if b.findtext('buildingType') == 'Deluxe Coop']
    if len(coops) != 1:
        raise ValueError('Expected exactly one Deluxe Coop in the fixture.')
    coop = coops[0]
    inside = coop.find('indoors')
    coop_tables = [inside.find('animals'), inside.find('Animals/SerializableDictionaryOfInt64FarmAnimal')]
    coop_residents = inside.find('animalsThatLiveHere')
    if coop_residents is None or any(table is None for table in coop_tables):
        raise ValueError('Coop animal dictionaries or resident list are unavailable.')
    rabbits = [[entry for entry in table.findall('item') if entry.findtext('value/FarmAnimal/type') == 'Rabbit']
               for table in coop_tables]
    if any(len(matches) != 1 for matches in rabbits):
        raise ValueError('Expected one rabbit in each coop animal dictionary.')
    rabbit_ids = [matches[0].findtext('key/long') for matches in rabbits]
    if rabbit_ids[0] is None or rabbit_ids[0] != rabbit_ids[1]:
        raise ValueError('Coop rabbit dictionaries disagree.')
    rabbit_resident = [entry for entry in coop_residents.findall('long') if entry.text == rabbit_ids[0]]
    if len(rabbit_resident) != 1:
        raise ValueError('Rabbit residency is inconsistent.')
    for table, matches in zip(coop_tables, rabbits):
        table.remove(matches[0])
    coop_residents.remove(rabbit_resident[0])
    coop.find('currentOccupants').text = str(int(coop.findtext('currentOccupants')) - 1)

    inventory = root.find('player/items')
    if inventory is None:
        raise ValueError('Player inventory unavailable.')
    nil = '{http://www.w3.org/2001/XMLSchema-instance}nil'
    moved = []
    for name in ('Milk Pail', 'Shears'):
        matches = [(items, item) for items in root.findall('.//objects/item/value/Object/items')
                   for item in items.findall('Item') if item.findtext('name') == name]
        empty = [i for i, item in enumerate(inventory) if item.get(nil) == 'true']
        if len(matches) != 1 or not empty:
            raise ValueError(f'Expected one stored {name} and an empty inventory slot.')
        container, item = matches[0]
        container.remove(item)
        slot = empty[0]
        inventory.remove(inventory[slot])
        inventory.insert(slot, item)
        moved.append({'name': name, 'slot': slot})
    tree.write(save_path, encoding='utf-8', xml_declaration=True)
    return {'removed_pig_id': pig_ids[0], 'removed_rabbit_id': rabbit_ids[0], 'moved_tools': moved}


def listening():
    try:
        with socket.create_connection(("127.0.0.1", 10783), timeout=1):
            return True
    except OSError:
        return False


def main():
    output = REPO_ROOT / ".tools" / "profit-test" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    app = Path(dotenv_values(ENV_FILE)["STARDEW_APP_PATH"]).resolve()
    if not app.is_file() or app.name.lower() != "stardewmoddingapi.exe":
        raise RuntimeError("Expected the configured SMAPI executable.")
    if not listening():
        if any("stardew" in (p.info["name"] or "").lower() for p in psutil.process_iter(["name"])):
            raise RuntimeError("A game process is running without an API; inspect it before launching another.")
        with (output / "smapi.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(app), "--port-id", "10783"], cwd=app.parent,
                                       stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        print(f"Launched SMAPI PID {process.pid}; waiting for API.", flush=True)
        deadline = time.monotonic() + 90
        while not listening():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"SMAPI did not become ready. Inspect {output / 'smapi.log'}")
            time.sleep(.5)
    from project.game.actions import ActionProxy
    from project.agents.react import GameTools
    proxy = ActionProxy(10783)
    source = DATA_ROOT / "saves/save_farming/StarDojo_396780002"
    name = "StarDojo_396780002_ProfitFarm_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destination = Path(os.environ["APPDATA"]) / "StardewValley/Saves" / name
    shutil.copytree(source, destination)
    (destination / source.name).rename(destination / name)
    fixture_save = destination / name
    added_chickens = add_fixture_chickens(fixture_save)
    fixture_changes = optimize_fixture(fixture_save)
    fixture_sha256 = hashlib.sha256(fixture_save.read_bytes()).hexdigest()
    print("Loading disposable fixture:", name, flush=True)
    proxy.resume_game()
    reply = proxy._post_message("load_game_record%" + name)
    time.sleep(3)
    proxy._post_message("set_money%500")
    proxy.pause_game()
    state = GameTools(proxy, radius=8).observe()
    player = state.get('player', {})
    names = [item.get('name') for item in player.get('inventory', [])]
    if player.get('money') != 500 or any(name not in names for name in ('Milk Pail', 'Shears')):
        raise RuntimeError('Could not verify fixture starting cash and animal tools.')
    report = {"test_save": str(destination), "source": str(source), "load_reply": reply,
              "fixture_version": 3, "fixture_sha256_before_load": fixture_sha256, "added_chicken_ids": added_chickens,
              "fixture_changes": fixture_changes,
              "initial_state": state, "setup_commands": ["load_game_record", "set_money%500"],
              "note": "Fixture setup only; these administrative commands are not agent tools."}
    (output / "fixture.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"fixture_report": str(output / "fixture.json"), "clock": state.get("gamestate"),
                      "player": state.get("player"), "clock_rules": state.get("clock_rules"),
                      "fishing_rules": state.get("fishing_rules")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
