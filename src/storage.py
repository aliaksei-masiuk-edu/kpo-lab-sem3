import json
import math
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from config import (
    CONDITION_RULES, DIFFICULTIES, FOODS, FORMAT_VERSION, MAX_ANIMALS,
    MAX_FOODS, MAX_INCOME, MAX_INITIAL_COUNT, MAX_INVENTORY, MAX_NAME_LENGTH,
    MAX_PRICE, MAX_SPECIES,
    SAVE_INTERVAL, default_settings,
)
from game import Animal, Player

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAX_FILE_SIZE = 1024 * 1024


class PersistenceError(ValueError):
    pass


class SaveCooldownError(PersistenceError):
    def __init__(self, seconds):
        self.seconds = seconds
        minutes, seconds = divmod(seconds, 60)
        super().__init__(f"Сохранять можно раз в 30 минут. Осталось: {minutes} мин. {seconds} сек.")


def check_fields(data, fields):
    if not isinstance(data, dict) or set(data) != set(fields):
        raise PersistenceError("Неверный набор полей в файле.")


def check_integer(value, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise PersistenceError("Числовое значение вне допустимого диапазона.")


def check_name(name):
    if (not isinstance(name, str) or not name.strip() or name != name.strip()
            or len(name) > MAX_NAME_LENGTH or not name.isprintable()):
        raise PersistenceError(f"Название должно содержать 1–{MAX_NAME_LENGTH} печатных символов.")


def check_timestamp(value):
    if type(value) not in (int, float) or not 0 <= value <= 10 ** 12:
        raise PersistenceError("Неверное время сохранения.")


def check_species(species, foods):
    if not isinstance(species, dict) or not 2 <= len(species) <= MAX_SPECIES:
        raise PersistenceError(f"Число видов должно быть от 2 до {MAX_SPECIES}.")
    names = set()
    for name, profile in species.items():
        check_name(name)
        if name.casefold() in names:
            raise PersistenceError("Названия видов не должны повторяться.")
        names.add(name.casefold())
        check_fields(profile, ("food", "price", "product", "income"))
        check_name(profile["food"])
        if profile["food"] not in foods:
            raise PersistenceError("Для вида не определён подходящий корм.")
        check_name(profile["product"])
        check_integer(profile["price"], 1, MAX_PRICE)
        check_integer(profile["income"], 1, MAX_INCOME)


def check_foods(foods):
    if not isinstance(foods, dict) or not 1 <= len(foods) <= MAX_FOODS:
        raise PersistenceError(f"Число кормов должно быть от 1 до {MAX_FOODS}.")
    names = set()
    for name, food in foods.items():
        check_name(name)
        if name.casefold() in names:
            raise PersistenceError("Названия кормов не должны повторяться.")
        names.add(name.casefold())
        check_fields(food, ("price", "satiety"))
        check_integer(food["price"], 1, MAX_PRICE)
        check_integer(food["satiety"], 1, 100)


def validate_settings(settings):
    check_fields(settings, ("difficulty", "animal_counts", "species", "foods"))
    if not isinstance(settings["difficulty"], str) or settings["difficulty"] not in DIFFICULTIES:
        raise PersistenceError("Неизвестная сложность.")
    check_foods(settings["foods"])
    check_species(settings["species"], settings["foods"])
    counts = settings["animal_counts"]
    if not isinstance(counts, dict) or set(counts) != set(settings["species"]):
        raise PersistenceError("Количество должно быть задано для каждого вида.")
    for count in counts.values():
        check_integer(count, 0, MAX_INITIAL_COUNT)
    if sum(counts.values()) > MAX_ANIMALS:
        raise PersistenceError(f"Начальное число животных не должно превышать {MAX_ANIMALS}.")


def add_species(settings, name, count, food, price, product, income):
    check_name(name)
    if name.casefold() in {item.casefold() for item in settings["species"]}:
        raise PersistenceError("Вид с таким названием уже существует.")
    result = deepcopy(settings)
    result["species"][name] = {
        "food": food, "price": price, "product": product, "income": income,
    }
    result["animal_counts"][name] = count
    validate_settings(result)
    return result


def add_food(settings, name, price, satiety):
    check_name(name)
    if name.casefold() in {item.casefold() for item in settings["foods"]}:
        raise PersistenceError("Корм с таким названием уже существует.")
    result = deepcopy(settings)
    result["foods"][name] = {"price": price, "satiety": satiety}
    validate_settings(result)
    return result


def validate_rules(rules):
    check_fields(rules, ("difficulty", "actions", "money", "hunger", "foods", "species", "condition"))
    if not isinstance(rules["difficulty"], str) or rules["difficulty"] not in DIFFICULTIES:
        raise PersistenceError("Неизвестная сложность сохранённой игры.")
    check_integer(rules["actions"], 1, 100)
    check_integer(rules["money"], 0)
    check_integer(rules["hunger"], 1, 100)
    foods = rules["foods"]
    check_foods(foods)
    check_species(rules["species"], foods)
    condition = rules["condition"]
    check_fields(condition, CONDITION_RULES)
    if type(condition["maximum"]) is not int or condition["maximum"] != 100:
        raise PersistenceError("Шкала состояния должна быть от 0 до 100.")
    for field in ("initial_satiety", "initial_happiness", "production_satiety",
                  "production_happiness", "bonus_happiness"):
        check_integer(condition[field], 1, condition["maximum"])
    for field in ("care_gain", "happiness_loss"):
        check_integer(condition[field], 1, 100)
    check_integer(condition["starvation_days"], 1, 30)
    check_integer(condition["bonus_multiplier"], 1, 10)
    check_integer(condition["starting_food"], 0, MAX_INVENTORY)
    if condition["bonus_happiness"] < condition["production_happiness"]:
        raise PersistenceError("Порог бонуса ниже порога производства.")


def player_from_data(data):
    check_fields(data, ("day", "money", "actions", "animals", "inventory", "rules", "last_saved_at"))
    validate_rules(data["rules"])
    check_integer(data["day"], 1)
    check_integer(data["money"], 0)
    check_integer(data["actions"], 0, data["rules"]["actions"])
    check_timestamp(data["last_saved_at"])
    inventory = data["inventory"]
    if not isinstance(inventory, dict) or set(inventory) != set(data["rules"]["foods"]):
        raise PersistenceError("Неверный склад кормов.")
    for count in inventory.values():
        check_integer(count, 0, MAX_INVENTORY)
    if not isinstance(data["animals"], list) or len(data["animals"]) > MAX_ANIMALS:
        raise PersistenceError("Неверный список животных.")
    animals = []
    condition = data["rules"]["condition"]
    for animal in data["animals"]:
        check_fields(animal, ("species", "happiness", "satiety", "starvation"))
        if not isinstance(animal["species"], str) or animal["species"] not in data["rules"]["species"]:
            raise PersistenceError("Неизвестный вид в сохранении.")
        check_integer(animal["happiness"], 0, condition["maximum"])
        check_integer(animal["satiety"], 0, condition["maximum"])
        check_integer(animal["starvation"], 0, condition["starvation_days"] - 1)
        if animal["satiety"] > 0 and animal["starvation"] != 0:
            raise PersistenceError("Сытое животное не может голодать несколько дней.")
        if animal["satiety"] == 0 and animal["starvation"] == 0:
            raise PersistenceError("Для голодного животного не указан срок голодания.")
        animals.append(Animal(**animal))
    return Player(data["day"], data["money"], data["actions"], animals,
                  inventory, data["rules"], data["last_saved_at"])


class Storage():
    def __init__(self, directory=DATA_DIR):
        self.__directory = Path(directory)

    def get_directory(self):
        return self.__directory

    def __read(self, filename):
        path = self.__directory / filename
        try:
            with path.open("r", encoding="utf-8") as file:
                content = file.read(MAX_FILE_SIZE + 1)
        except FileNotFoundError as error:
            raise PersistenceError(f"Файл {filename} не найден.") from error
        except (OSError, UnicodeError) as error:
            raise PersistenceError(f"Не удалось прочитать {filename}: {error}") from error
        if len(content) > MAX_FILE_SIZE:
            raise PersistenceError(f"Файл {filename} слишком большой.")
        try:
            data = json.loads(content)
        except (ValueError, RecursionError) as error:
            raise PersistenceError(f"Неверный JSON в {filename}.") from error
        check_fields(data, ("format_version", "data"))
        if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
            raise PersistenceError("Неподдерживаемая версия формата файла.")
        return data["data"]

    def __write(self, filename, data):
        temporary = None
        try:
            self.__directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.__directory,
                                             prefix=filename + ".", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({"format_version": FORMAT_VERSION, "data": data}, file,
                          ensure_ascii=False, indent=4, allow_nan=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.__directory / filename)
        except OSError as error:
            raise PersistenceError(f"Не удалось записать {filename}: {error}") from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    raise PersistenceError(f"Не удалось удалить временный файл: {error}") from error

    def load_settings(self):
        # Файл создаётся только если его нет; повреждённый файл не перезаписывается.
        try:
            (self.__directory / "settings.json").stat()
        except FileNotFoundError:
            settings = default_settings()
            self.save_settings(settings)
            return settings
        except OSError as error:
            raise PersistenceError(f"Не удалось проверить файл настроек: {error}") from error
        settings = self.__read("settings.json")
        # В старых настройках не было кормов — подставляем корма по умолчанию.
        if isinstance(settings, dict) and set(settings) == {"difficulty", "animal_counts", "species"}:
            settings["foods"] = deepcopy(FOODS)
        validate_settings(settings)
        return settings

    def save_settings(self, settings):
        validate_settings(settings)
        self.__write("settings.json", settings)

    def load_game(self):
        return player_from_data(self.__read("save.json"))

    def save_game(self, player, now=None):
        timestamp = time.time() if now is None else now
        check_timestamp(timestamp)
        previous = player.get_last_saved_at()
        try:
            (self.__directory / "save.json").stat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise PersistenceError(f"Не удалось проверить сохранение: {error}") from error
        else:
            disk_timestamp = self.load_game().get_last_saved_at()
            previous = disk_timestamp if previous is None else max(previous, disk_timestamp)
        if previous is not None and timestamp < previous + SAVE_INTERVAL:
            raise SaveCooldownError(math.ceil(previous + SAVE_INTERVAL - timestamp))
        data = player.to_data()
        data["last_saved_at"] = timestamp
        player_from_data(data)
        self.__write("save.json", data)
        player.set_last_saved_at(timestamp)
