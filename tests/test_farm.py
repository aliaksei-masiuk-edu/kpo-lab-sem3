import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import DIFFICULTIES, MAX_FOODS, SAVE_INTERVAL, default_settings
from game import Animal, Food, GameError, Player
from storage import PersistenceError, SaveCooldownError, Storage, add_food, add_species
from ui import FarmApplication, read_integer


class GameTest(unittest.TestCase):
    def setUp(self):
        self.settings = default_settings()
        self.player = Player.new_game(self.settings)

    def assert_rejected(self, operation):
        before = self.player.to_data()
        with self.assertRaises(GameError):
            operation()
        self.assertEqual(self.player.to_data(), before)

    def test_initialization_uses_selected_counts_and_difficulty(self):
        self.settings["difficulty"] = "Сложная"
        self.settings["animal_counts"] = {"Корова": 2, "Курица": 0}
        player = Player.new_game(self.settings)
        self.assertEqual([animal.get_species() for animal in player.get_animals()], ["Корова"] * 2)
        self.assertEqual((player.get_day(), player.get_money(), player.get_actions()), (1, 120, 6))
        self.assertEqual(player.get_inventory(), {"Сено": 5, "Зерно": 5})
        self.assertEqual(player.get_animals()[0].get_satiety(), 70)

    def test_difficulty_affects_actions_money_and_daily_hunger(self):
        for name, rules in DIFFICULTIES.items():
            with self.subTest(name=name):
                self.settings["difficulty"] = name
                player = Player.new_game(self.settings)
                self.assertEqual(player.get_money(), rules["money"])
                self.assertEqual(player.get_actions(), rules["actions"])
                player.finish_day()
                self.assertEqual(player.get_animals()[0].get_satiety(), 70 - rules["hunger"])

    def test_successful_batch_purchase_costs_one_action(self):
        self.player.buy_animals("Курица", 3)
        self.assertEqual(len(self.player.get_animals()), 6)
        self.assertEqual(self.player.get_money(), 110)
        self.assertEqual(self.player.get_actions(), 7)
        self.player.buy_food("Зерно", 4)
        self.assertEqual(self.player.get_inventory()["Зерно"], 9)
        self.assertEqual(self.player.get_money(), 98)
        self.assertEqual(self.player.get_actions(), 6)

    def test_insufficient_money_is_atomic(self):
        self.assert_rejected(lambda: self.player.buy_animals("Корова", 3))
        self.assert_rejected(lambda: self.player.buy_food("Сено", 100))

    def test_invalid_quantity_and_unknown_species_are_atomic(self):
        for quantity in (-1, 0, 101, True, 1.5):
            self.assert_rejected(lambda: self.player.buy_animals("Корова", quantity))
            self.assert_rejected(lambda: self.player.buy_food("Сено", quantity))
        self.assert_rejected(lambda: self.player.buy_animals("Нет", 1))
        self.assert_rejected(lambda: self.player.buy_food("Нет", 1))

    def test_farm_and_inventory_limits(self):
        data = self.player.to_data()
        self.player = Player(1, 100000, 8, [Animal("Курица", 60, 70) for _ in range(100)],
                             {"Сено": 10000, "Зерно": 5}, data["rules"])
        self.assert_rejected(lambda: self.player.buy_animals("Курица", 1))
        self.assert_rejected(lambda: self.player.buy_food("Сено", 1))

    def test_action_limits_and_reset(self):
        for _ in range(8):
            self.player.buy_food("Сено", 1)
        self.assertEqual(self.player.get_actions(), 0)
        for operation in (lambda: self.player.buy_food("Сено", 1),
                          lambda: self.player.buy_animals("Курица", 1),
                          lambda: self.player.feed(0, "Сено"), lambda: self.player.care(0)):
            self.assert_rejected(operation)
        self.player.finish_day()
        self.assertEqual(self.player.get_actions(), 8)
        self.assertEqual(self.player.get_day(), 2)

    def test_food_compatibility_and_consumption(self):
        self.assert_rejected(lambda: self.player.feed(0, "Зерно"))
        self.assert_rejected(lambda: self.player.feed(1, "Сено"))
        self.player.feed(0, "Сено")
        self.assertEqual(self.player.get_inventory()["Сено"], 4)
        self.assertEqual(self.player.get_animals()[0].get_satiety(), 100)
        self.assertEqual(self.player.get_actions(), 7)
        self.assert_rejected(lambda: self.player.feed(0, "Сено"))
        self.player.feed(1, "Зерно")
        self.assertEqual(self.player.get_inventory()["Зерно"], 4)

    def test_empty_food_stock_is_atomic(self):
        rules = self.player.get_rules()
        self.player = Player(1, 200, 8, [Animal("Корова", 60, 20)], {"Сено": 0, "Зерно": 2}, rules)
        self.assert_rejected(lambda: self.player.feed(0, "Сено"))

    def test_care_and_full_happiness(self):
        self.player.care(0)
        self.assertEqual(self.player.get_animals()[0].get_happiness(), 90)
        self.player.care(0)
        self.assertEqual(self.player.get_animals()[0].get_happiness(), 100)
        self.assert_rejected(lambda: self.player.care(0))
        for index in (-1, 3, True):
            self.assert_rejected(lambda: self.player.care(index))
            self.assert_rejected(lambda: self.player.feed(index, "Сено"))

    def test_daily_order_and_product_income(self):
        report = self.player.finish_day()
        self.assertEqual(report, {"income": 32, "products": {"Молоко": 1, "Яйца": 2}, "deaths": []})
        self.assertEqual(self.player.get_money(), 232)
        self.assertEqual(self.player.get_animals()[0].get_satiety(), 50)
        self.assertEqual(self.player.get_animals()[0].get_happiness(), 50)
        self.assertEqual(self.player.finish_day()["income"], 32)
        self.assertEqual(self.player.finish_day()["income"], 0)

    def test_happiness_bonus(self):
        self.player.care(0)
        report = self.player.finish_day()
        self.assertEqual(report["products"], {"Молоко": 2, "Яйца": 2})
        self.assertEqual(report["income"], 48)

    def test_low_satiety_or_happiness_prevents_production(self):
        rules = self.player.get_rules()
        self.player = Player(1, 0, 8, [Animal("Корова", 100, 30), Animal("Курица", 40, 100)],
                             {"Сено": 0, "Зерно": 0}, rules)
        self.assertEqual(self.player.finish_day()["income"], 0)

    def test_starvation_death_and_dead_animal_removal(self):
        for _ in range(4):
            report = self.player.finish_day()
        self.assertEqual(self.player.get_animals()[0].get_starvation(), 1)
        self.assertEqual(report["income"], 0)
        self.player.finish_day()
        self.assertEqual(self.player.get_animals()[0].get_starvation(), 2)
        report = self.player.finish_day()
        self.assertEqual(report["deaths"], ["Корова", "Курица", "Курица"])
        self.assertEqual(self.player.get_animals(), [])
        self.assertEqual(report["income"], 0)
        self.assert_rejected(lambda: self.player.care(0))
        self.assert_rejected(lambda: self.player.feed(0, "Сено"))
        self.assertEqual(self.player.finish_day()["income"], 0)

    def test_feeding_resets_starvation(self):
        for _ in range(5):
            self.player.finish_day()
        self.player.feed(0, "Сено")
        self.assertEqual(self.player.get_animals()[0].get_starvation(), 0)
        self.player.finish_day()
        self.assertEqual(len(self.player.get_animals()), 1)
        self.assertEqual(self.player.get_animals()[0].get_satiety(), 20)

    def test_custom_species_is_usable(self):
        settings = add_species(self.settings, "Коза", 1, "Сено", 80, "Молоко", 16)
        player = Player.new_game(settings)
        self.assertEqual(player.get_animals()[-1].get_species(), "Коза")
        player.feed(3, "Сено")
        player.care(3)
        player.buy_animals("Коза", 1)
        report = player.finish_day()
        self.assertEqual(report["products"]["Молоко"], 4)
        self.assertEqual(report["income"], 80)

    def test_independent_species_and_food_parameters_drive_gameplay(self):
        settings = add_food(self.settings, "Трава", 7, 12)
        settings = add_species(settings, "Овца", 1, "Трава", 45, "Шерсть", 23)
        player = Player.new_game(settings)
        player.buy_animals("Овца", 1)
        self.assertEqual(player.get_money(), 155)
        player.buy_food("Трава", 2)
        self.assertEqual(player.get_money(), 141)
        self.assertEqual(player.get_inventory()["Трава"], 7)
        before = player.to_data()
        with self.assertRaises(GameError):
            player.feed(3, "Сено")
        self.assertEqual(player.to_data(), before)
        player.feed(3, "Трава")
        self.assertEqual(player.get_animals()[3].get_satiety(), 82)
        self.assertEqual(player.get_inventory()["Трава"], 6)
        player.care(3)
        self.assertEqual(player.finish_day(), {
            "income": 101, "products": {"Молоко": 1, "Яйца": 2, "Шерсть": 3}, "deaths": [],
        })
        settings["foods"]["Трава"]["satiety"] = 100
        settings["species"]["Овца"]["income"] = 1
        self.assertEqual(player.get_rules()["foods"]["Трава"]["satiety"], 12)
        self.assertEqual(player.get_rules()["species"]["Овца"]["income"], 23)

    def test_empty_farm_and_no_money_can_advance(self):
        self.settings["animal_counts"] = {"Корова": 0, "Курица": 0}
        player = Player.new_game(self.settings)
        self.assertEqual(player.get_animals(), [])
        self.assertEqual(player.finish_day()["income"], 0)
        player.buy_animals("Курица", 1)
        self.assertEqual(len(player.get_animals()), 1)

    def test_rules_are_isolated_from_settings_and_getter_changes(self):
        self.settings["species"]["Корова"]["price"] = 1
        rules = self.player.get_rules()
        rules["species"]["Корова"]["price"] = 2
        self.player.get_inventory()["Сено"] = 0
        self.assertEqual(self.player.get_rules()["species"]["Корова"]["price"], 80)
        self.assertEqual(self.player.get_inventory()["Сено"], 5)

    def test_food_class(self):
        food = Food("Сено", 4, 40)
        self.assertEqual((food.get_name(), food.get_price(), food.get_satiety()), ("Сено", 4, 40))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.storage = Storage(self.path)
        self.settings = self.storage.load_settings()
        self.player = Player.new_game(self.settings)

    def write_data(self, filename, data):
        (self.path / filename).write_text(json.dumps({"format_version": 1, "data": data}), encoding="utf-8")

    def test_default_settings_created_and_round_trip(self):
        self.assertEqual(self.settings, default_settings())
        self.assertTrue((self.path / "settings.json").is_file())
        self.settings["difficulty"] = "Лёгкая"
        self.settings["animal_counts"]["Курица"] = 4
        self.storage.save_settings(self.settings)
        self.assertEqual(Storage(self.path).load_settings(), self.settings)
        self.assertIsInstance(self.settings["difficulty"], str)
        self.assertIsInstance(self.settings["animal_counts"], dict)

    def test_custom_species_settings_and_game_round_trip(self):
        settings = add_species(self.settings, "Коза", 2, "Сено", 80, "Молоко", 16)
        self.storage.save_settings(settings)
        self.assertEqual(self.storage.load_settings(), settings)
        player = Player.new_game(self.storage.load_settings())
        player.feed(3, "Сено")
        self.storage.save_game(player, now=1000)
        loaded = self.storage.load_game()
        self.assertEqual(loaded.to_data(), player.to_data())
        self.assertEqual(loaded.get_animals()[3].get_species(), "Коза")
        loaded.care(3)
        self.assertGreater(loaded.finish_day()["income"], 0)

    def test_custom_food_and_species_survive_reload_and_settings_changes(self):
        settings = add_food(self.settings, "Трава", 7, 12)
        settings = add_species(settings, "Овца", 1, "Трава", 45, "Шерсть", 23)
        self.storage.save_settings(settings)
        self.assertEqual(Storage(self.path).load_settings(), settings)
        player = Player.new_game(self.storage.load_settings())
        player.buy_food("Трава", 2)
        player.feed(3, "Трава")
        self.storage.save_game(player, now=1000)
        settings["foods"]["Трава"] = {"price": 1, "satiety": 100}
        settings["species"]["Овца"]["income"] = 1
        self.storage.save_settings(settings)
        loaded = Storage(self.path).load_game()
        self.assertEqual(loaded.to_data(), player.to_data())
        self.assertEqual(loaded.finish_day(), player.finish_day())
        self.assertEqual(loaded.get_rules()["foods"]["Трава"], {"price": 7, "satiety": 12})

    def test_legacy_settings_gain_default_foods_without_rewriting_file(self):
        legacy = add_species(self.settings, "Коза", 2, "Сено", 80, "Молоко", 16)
        del legacy["foods"]
        self.write_data("settings.json", legacy)
        original = (self.path / "settings.json").read_bytes()
        loaded = self.storage.load_settings()
        self.assertEqual(loaded, {**legacy, "foods": default_settings()["foods"]})
        self.assertEqual((self.path / "settings.json").read_bytes(), original)
        self.storage.save_settings(loaded)
        self.assertEqual(self.storage.load_settings(), loaded)

    def test_invalid_custom_parameters_are_rejected_without_mutation(self):
        original = deepcopy(self.settings)
        for name in ("", "  ", "сено", "ЗЕРНО", "x" * 41, "Трава\n", "\x1b[31m"):
            with self.subTest(name=name), self.assertRaises(PersistenceError):
                add_food(self.settings, name, 3, 30)
        for price, satiety in ((0, 30), (10001, 30), (True, 30), (1.5, 30),
                               (3, 0), (3, 101), (3, True), (3, "30")):
            with self.subTest(price=price, satiety=satiety), self.assertRaises(PersistenceError):
                add_food(self.settings, "Трава", price, satiety)
        profile = {"food": "Сено", "price": 45, "product": "Шерсть", "income": 23}
        for change in ({"food": "Нет"}, {"food": []}, {"price": 0}, {"price": 10001},
                       {"price": True}, {"product": ""}, {"product": "x" * 41},
                       {"income": 0}, {"income": 10001}, {"income": 1.5}):
            with self.subTest(change=change), self.assertRaises(PersistenceError):
                add_species(self.settings, "Овца", 1, **{**profile, **change})
        self.assertEqual(self.settings, original)

    def test_food_limit_and_invalid_food_catalogs(self):
        settings = deepcopy(self.settings)
        for index in range(MAX_FOODS - len(settings["foods"])):
            settings = add_food(settings, f"Корм {index}", 1, 1)
        with self.assertRaises(PersistenceError):
            add_food(settings, "Лишний", 1, 1)
        for foods in (None, {}, {"Сено": {"price": 1, "satiety": 30},
                                 "сено": {"price": 2, "satiety": 40}},
                      {"Сено": {"price": 1, "satiety": 30, "unknown": 1}}):
            with self.subTest(foods=foods), self.assertRaises(PersistenceError):
                self.storage.save_settings({**self.settings, "foods": foods})
        self.assertEqual(self.storage.load_settings(), self.settings)

    def test_bad_custom_species_and_invalid_settings(self):
        for name in ("", "  ", "корова", "КУРИЦА", "a" * 41, "Коза\n", "\x1b[31m"):
            with self.subTest(name=name), self.assertRaises(PersistenceError):
                add_species(self.settings, name, 1, "Сено", 80, "Молоко", 16)
        for count in (-1, 51, True):
            with self.assertRaises(PersistenceError):
                add_species(self.settings, "Коза", count, "Сено", 80, "Молоко", 16)
        with self.assertRaises(PersistenceError):
            add_species(self.settings, "Коза", 1, "нет", 80, "Молоко", 16)
        self.settings["animal_counts"] = {"Корова": 50, "Курица": 50}
        with self.assertRaises(PersistenceError):
            add_species(self.settings, "Коза", 1, "Сено", 80, "Молоко", 16)
        for index in range(18):
            self.settings = add_species(self.settings, f"Вид {index}", 0, "Сено", 80, "Молоко", 16)
        with self.assertRaises(PersistenceError):
            add_species(self.settings, "Лишний", 0, "Сено", 80, "Молоко", 16)

    def test_complete_save_load_preserves_state_without_day_advance(self):
        for _ in range(4):
            self.player.finish_day()
        self.player.feed(0, "Сено")
        self.player.care(0)
        self.player.buy_food("Зерно", 3)
        self.storage.save_game(self.player, now=1234.5)
        loaded = Storage(self.path).load_game()
        self.assertIsInstance(loaded, Player)
        self.assertTrue(all(isinstance(animal, Animal) for animal in loaded.get_animals()))
        self.assertEqual(loaded.to_data(), self.player.to_data())
        self.assertEqual(loaded.get_animals()[1].get_starvation(), 1)
        self.assertEqual(loaded.finish_day(), self.player.finish_day())
        self.assertEqual(loaded.to_data(), self.player.to_data())

    def test_settings_changes_do_not_change_saved_rules(self):
        self.storage.save_game(self.player, now=1000)
        self.settings["difficulty"] = "Сложная"
        self.settings["animal_counts"]["Корова"] = 0
        self.settings["species"]["Корова"]["price"] = 1
        self.storage.save_settings(self.settings)
        loaded = self.storage.load_game()
        self.assertEqual(loaded.get_rules(), self.player.get_rules())
        self.assertEqual(loaded.get_rules()["difficulty"], "Обычная")
        self.assertEqual(len(loaded.get_animals()), 3)

    def test_cooldown_survives_reload_and_new_game(self):
        self.storage.save_game(self.player, now=1000)
        original = (self.path / "save.json").read_bytes()
        loaded = Storage(self.path).load_game()
        loaded.care(0)
        for player in (loaded, Player.new_game(self.settings)):
            before = player.to_data()
            with self.assertRaises(SaveCooldownError) as error:
                Storage(self.path).save_game(player, now=1100.1)
            self.assertEqual(error.exception.seconds, 1700)
            self.assertEqual(player.to_data(), before)
            self.assertEqual((self.path / "save.json").read_bytes(), original)
        self.storage.save_game(loaded, now=1000 + SAVE_INTERVAL)
        self.assertEqual(self.storage.load_game().get_last_saved_at(), 2800)

    def test_boundary_and_clock_rollback(self):
        self.storage.save_game(self.player, now=1000)
        for now, remaining in ((2799, 1), (900, 1900)):
            with self.assertRaises(SaveCooldownError) as error:
                self.storage.save_game(self.player, now=now)
            self.assertEqual(error.exception.seconds, remaining)
        self.storage.save_game(self.player, now=2800)

    def test_newer_disk_save_cannot_be_overwritten_by_older_loaded_game(self):
        self.storage.save_game(self.player, now=1000)
        older = self.storage.load_game()
        self.storage.save_game(self.player, now=2800)
        with self.assertRaises(SaveCooldownError):
            self.storage.save_game(older, now=3000)

    def test_failed_replacement_preserves_file_and_timestamp(self):
        self.storage.save_game(self.player, now=1000)
        original = (self.path / "save.json").read_bytes()
        before = self.player.to_data()
        with patch("storage.os.replace", side_effect=PermissionError("test write denied")):
            with self.assertRaises(PersistenceError):
                self.storage.save_game(self.player, now=2800)
        self.assertEqual(self.player.to_data(), before)
        self.assertEqual((self.path / "save.json").read_bytes(), original)
        self.assertEqual(list(self.path.glob("*.tmp")), [])
        settings_before = (self.path / "settings.json").read_bytes()
        with patch("storage.os.replace", side_effect=PermissionError("test write denied")):
            with self.assertRaises(PersistenceError):
                self.storage.save_settings(self.settings)
        self.assertEqual((self.path / "settings.json").read_bytes(), settings_before)

    def test_missing_save(self):
        with self.assertRaisesRegex(PersistenceError, "не найден"):
            self.storage.load_game()

    def test_failed_temporary_file_cleanup_is_reported(self):
        self.storage.save_game(self.player, now=1000)
        original = (self.path / "save.json").read_bytes()
        with patch("storage.os.replace", side_effect=PermissionError("replace denied")), \
                patch("pathlib.Path.unlink", side_effect=PermissionError("cleanup denied")):
            with self.assertRaisesRegex(PersistenceError, "временный файл"):
                self.storage.save_game(self.player, now=2800)
        self.assertEqual((self.path / "save.json").read_bytes(), original)
        self.assertEqual(self.player.get_last_saved_at(), 1000)

    def test_unreadable_files(self):
        for operation in (self.storage.load_settings, self.storage.load_game):
            with patch("pathlib.Path.open", side_effect=PermissionError("test read denied")):
                with self.assertRaises(PersistenceError):
                    operation()

    def test_bad_json_and_encoding_are_preserved(self):
        for content in (b"{", b"[]", b"\xff", b'{"format_version":2,"data":{}}'):
            for filename, operation in (("settings.json", self.storage.load_settings),
                                        ("save.json", self.storage.load_game)):
                (self.path / filename).write_bytes(content)
                with self.assertRaises(PersistenceError):
                    operation()
                self.assertEqual((self.path / filename).read_bytes(), content)

    def test_malformed_save_fields(self):
        self.storage.save_game(self.player, now=1000)
        original = self.player.to_data()
        mutations = [
            lambda data: data.update(day=0),
            lambda data: data.update(money=-1),
            lambda data: data.update(actions=9),
            lambda data: data.update(last_saved_at=float("nan")),
            lambda data: data.update(last_saved_at=True),
            lambda data: data.update(inventory={}),
            lambda data: data.update(animals={}),
            lambda data: data["animals"][0].update(species="Нет"),
            lambda data: data["animals"][0].update(satiety=101),
            lambda data: data["animals"][0].update(starvation=3),
            lambda data: data["animals"][0].update(starvation=1),
            lambda data: data["rules"].update(hunger=0),
            lambda data: data["rules"].update(difficulty=[]),
            lambda data: data["rules"]["foods"]["Сено"].update(price=-1),
            lambda data: data["rules"]["foods"].update(сено={"price": 4, "satiety": 40}),
            lambda data: data["rules"]["species"]["Корова"].update(food="Нет"),
            lambda data: data["rules"]["condition"].pop("care_gain"),
        ]
        for mutate in mutations:
            data = deepcopy(original)
            mutate(data)
            self.write_data("save.json", data)
            with self.subTest(data=data), self.assertRaises(PersistenceError):
                self.storage.load_game()

    def test_invalid_settings_not_overwritten_on_load(self):
        for change in ({"difficulty": []}, {"animal_counts": {"Корова": True, "Курица": 2}},
                       {"species": {}}, {"unexpected": 1}):
            settings = {**default_settings(), **change}
            self.write_data("settings.json", settings)
            before = (self.path / "settings.json").read_bytes()
            with self.assertRaises(PersistenceError):
                self.storage.load_settings()
            self.assertEqual((self.path / "settings.json").read_bytes(), before)

    def test_invalid_existing_save_blocks_overwrite(self):
        (self.path / "save.json").write_text("broken", encoding="utf-8")
        with self.assertRaises(PersistenceError):
            self.storage.save_game(self.player, now=1000)
        self.assertEqual((self.path / "save.json").read_text(), "broken")
        self.assertIsNone(self.player.get_last_saved_at())

    def test_default_data_path_is_independent_of_working_directory(self):
        from storage import DATA_DIR
        self.assertEqual(DATA_DIR, Path(__file__).resolve().parent.parent / "data")
        self.assertEqual(Storage().get_directory(), DATA_DIR)


class ConsoleTest(unittest.TestCase):
    def run_app(self, answers, directory=None):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch("builtins.input", side_effect=answers), redirect_stdout(output):
                FarmApplication(Storage(temporary if directory is None else directory)).run()
            return output.getvalue()

    def test_bad_input_is_retried(self):
        with patch("builtins.input", side_effect=["abc", "-1", "999", "2"]), redirect_stdout(io.StringIO()):
            self.assertEqual(read_integer("", 0, 5), 2)

    def test_eof_and_ctrl_c_exit_cleanly(self):
        for interruption in (EOFError(), KeyboardInterrupt()):
            output = self.run_app([interruption])
            self.assertIn("Ввод завершён", output)
            self.assertIn("До свидания", output)

    def test_settings_submenus_reopen_and_about(self):
        output = self.run_app(["3", "1", "3", "2", "1", "4", "0", "0", "3", "0", "4", "", "5"])
        self.assertIn("Сложность: Сложная", output)
        self.assertIn("Начальное количество — Корова: 4", output)
        self.assertIn("1.5.0", output)

    def test_create_and_edit_species_and_food_from_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.run_app([
                "3", "5", "3", "Трава", "7", "12", "0",
                "4", "Овца", "3", "45", "Шерсть", "23", "1",
                "4", "1", "2", "2", "2", "3", "60", "4", "Пух", "5", "19", "0",
                "6", "3", "1", "9", "2", "25", "0", "0", "0",
                "1", "8", "1", "5",
            ], directory)
            settings = Storage(directory).load_settings()
            self.assertEqual(settings["species"]["Овца"], {
                "food": "Зерно", "price": 60, "product": "Пух", "income": 19,
            })
            self.assertEqual(settings["animal_counts"]["Овца"], 2)
            self.assertEqual(settings["foods"]["Трава"], {"price": 9, "satiety": 25})
            self.assertIn("Овца: цена 60, корм Зерно, Пух — 19 монет", output)
            self.assertIn("Трава: 5 порций, цена 9, сытость +25", output)

    def test_edit_existing_species_and_food(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_app([
                "3", "2", "2", "2", "3", "71", "4", "Сыр", "5", "21", "0",
                "5", "2", "1", "6", "2", "18", "0", "0", "0", "5",
            ], directory)
            settings = Storage(directory).load_settings()
            self.assertEqual(settings["species"]["Корова"], {
                "food": "Зерно", "price": 71, "product": "Сыр", "income": 21,
            })
            self.assertEqual(settings["foods"]["Зерно"], {"price": 6, "satiety": 18})

    def test_species_and_food_creation_can_be_cancelled_at_every_step(self):
        species_prefixes = [[], ["Коза"], ["Коза", "1"], ["Коза", "1", "45"],
                            ["Коза", "1", "45", "Шерсть"],
                            ["Коза", "1", "45", "Шерсть", "23"]]
        for prefix in species_prefixes:
            cancel = "0" if prefix == ["Коза"] else ""
            with self.subTest(species=prefix), tempfile.TemporaryDirectory() as directory:
                self.run_app(["3", "4", *prefix, cancel, "0", "5"], directory)
                self.assertEqual(Storage(directory).load_settings(), default_settings())
        for prefix in ([], ["Трава"], ["Трава", "7"]):
            with self.subTest(food=prefix), tempfile.TemporaryDirectory() as directory:
                self.run_app(["3", "5", "3", *prefix, "", "0", "0", "5"], directory)
                self.assertEqual(Storage(directory).load_settings(), default_settings())

    def test_invalid_input_is_retried_and_zero_initial_count_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_app([
                "3", "5", "3", "Трава", "abc", "0", "10001", "7", "101", "12", "0",
                "4", "Овца", "3", "45", "Шерсть", "23", "0", "0", "5",
            ], directory)
            settings = Storage(directory).load_settings()
            self.assertEqual(settings["animal_counts"]["Овца"], 0)
            self.assertEqual(settings["foods"]["Трава"], {"price": 7, "satiety": 12})

    def test_failed_settings_write_does_not_apply_custom_food(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory)
            storage.load_settings()
            output = io.StringIO()
            with patch.object(storage, "save_settings", side_effect=PersistenceError("write denied")), \
                    patch("builtins.input", side_effect=[
                        "3", "5", "3", "Трава", "7", "12", "0", "1", "8", "1", "5",
                    ]), redirect_stdout(output):
                FarmApplication(storage).run()
            self.assertIn("Настройки не изменены: write denied", output.getvalue())
            self.assertNotIn("Трава: 5 порций", output.getvalue())
            self.assertEqual(storage.load_settings(), default_settings())

    def test_load_directly_resumes_and_save_does_not_consume_actions(self):
        output = self.run_app(["1", "7", "8", "1", "2", "7", "8", "1", "5"])
        self.assertIn("Игра загружена", output)
        self.assertIn("Осталось:", output)
        self.assertEqual(output.count("Действия: 8/8"), 2)

    def test_destructive_actions_can_be_cancelled(self):
        output = self.run_app(["1", "6", "0", "8", "0", "5", "8", "1", "5"])
        self.assertNotIn("Начался день 2", output)
        self.assertIn("Действия: 8/8", output)

    def test_autosave_every_30_minutes(self):
        times = iter([1000, 1100, 1000 + SAVE_INTERVAL, 1060 + SAVE_INTERVAL])
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch("builtins.input", side_effect=["1", "5", "5", "5", "8", "1", "5"]), \
                    redirect_stdout(output):
                FarmApplication(Storage(directory), clock=lambda: next(times)).run()
            self.assertEqual(output.getvalue().count("Автосохранение: прогресс записан."), 1)
            self.assertEqual(Storage(directory).load_game().get_last_saved_at(), 1000 + SAVE_INTERVAL)

    def test_autosave_respects_recent_manual_save(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory)
            storage.save_game(Player.new_game(default_settings()), now=2000)
            times = iter([1000, 1000 + SAVE_INTERVAL])
            output = io.StringIO()
            with patch("builtins.input", side_effect=["1", "5", "8", "1", "5"]), \
                    redirect_stdout(output):
                FarmApplication(storage, clock=lambda: next(times)).run()
            self.assertNotIn("Автосохранение", output.getvalue())
            self.assertEqual(storage.load_game().get_last_saved_at(), 2000)


if __name__ == "__main__":
    unittest.main()
