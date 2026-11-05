"""Validate the prepared farm fixture without touching game saves."""

import tempfile
import unittest
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from project.paths import DATA_ROOT
from project.tools.prepare_profit_test import add_fixture_chickens, optimize_fixture


class ProfitFixtureChecks(unittest.TestCase):
    def test_pig_removed_and_existing_tools_moved_to_backpack(self):
        source = DATA_ROOT / 'saves/save_farming/StarDojo_396780002/StarDojo_396780002'
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'save'
            shutil.copyfile(source, copied)
            add_fixture_chickens(copied)
            changes = optimize_fixture(copied)
            root = ET.parse(copied).getroot()
            barn = next(b for b in root.findall('.//buildings/Building') if b.findtext('buildingType') == 'Deluxe Barn')
            residents = {x.text for x in barn.findall('indoors/animalsThatLiveHere/long')}
            self.assertNotIn(changes['removed_pig_id'], residents)
            self.assertEqual(int(barn.findtext('currentOccupants')), len(residents))
            for table in (barn.find('indoors/animals'), barn.find('indoors/Animals/SerializableDictionaryOfInt64FarmAnimal')):
                self.assertNotIn('Pig', [x.findtext('value/FarmAnimal/type') for x in table.findall('item')])
                self.assertEqual({x.findtext('key/long') for x in table.findall('item')}, residents)
            coop = next(b for b in root.findall('.//buildings/Building') if b.findtext('buildingType') == 'Deluxe Coop')
            coop_residents = {x.text for x in coop.findall('indoors/animalsThatLiveHere/long')}
            self.assertNotIn(changes['removed_rabbit_id'], coop_residents)
            self.assertEqual(int(coop.findtext('currentOccupants')), len(coop_residents))
            for table in (coop.find('indoors/animals'), coop.find('indoors/Animals/SerializableDictionaryOfInt64FarmAnimal')):
                self.assertNotIn('Rabbit', [x.findtext('value/FarmAnimal/type') for x in table.findall('item')])
                self.assertEqual({x.findtext('key/long') for x in table.findall('item')}, coop_residents)
            inventory = root.find('player/items')
            for tool in changes['moved_tools']:
                self.assertEqual(inventory[tool['slot']].findtext('name'), tool['name'])
                self.assertFalse(any(item.findtext('name') == tool['name'] for item in root.findall('.//objects/item/value/Object/items/Item')))
            with self.assertRaises(ValueError): optimize_fixture(copied)
        self.assertEqual(source.read_bytes(), original)

    def test_fixture_has_matching_chickens_and_preserves_original(self):
        source = DATA_ROOT / 'saves/save_farming/StarDojo_396780002/StarDojo_396780002'
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'save'
            shutil.copyfile(source, copied)
            ids = add_fixture_chickens(copied)
            self.assertIn(b'xmlns:xsd="http://www.w3.org/2001/XMLSchema"', copied.read_bytes())
            root = ET.parse(copied).getroot()
            coop = next(b for b in root.findall('.//buildings/Building') if b.findtext('buildingType') == 'Deluxe Coop')
            tables = [coop.find('indoors/animals'), coop.find('indoors/Animals/SerializableDictionaryOfInt64FarmAnimal')]
            for table in tables:
                chickens = [x for x in table.findall('item') if x.findtext('value/FarmAnimal/type') == 'White Chicken']
                self.assertEqual(len(chickens), 3)
                self.assertEqual({x.findtext('key/long') for x in chickens},
                                 {x.findtext('value/FarmAnimal/myID') for x in chickens})
            residents = {int(e.text) for e in coop.findall('indoors/animalsThatLiveHere/long')}
            self.assertTrue(set(ids).issubset(residents))
            self.assertEqual(int(coop.findtext('currentOccupants')), len(residents))
            soils = [x for x in root.findall('.//terrainFeatures/item/value/*')
                     if x.get('{http://www.w3.org/2001/XMLSchema-instance}type') == 'HoeDirt']
            self.assertTrue(any(x.find('crop') is not None for x in soils))
            with self.assertRaises(ValueError):
                add_fixture_chickens(copied)
        self.assertEqual(source.read_bytes(), original)
