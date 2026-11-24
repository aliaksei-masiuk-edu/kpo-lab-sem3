from copy import deepcopy

VERSION = "1.5.0"
AUTHOR = "Масюк Алексей"
FORMAT_VERSION = 1
SAVE_INTERVAL = 30 * 60
MAX_SPECIES = 20
MAX_FOODS = 20
MAX_PRICE = 10000
MAX_INCOME = 10000
MAX_NAME_LENGTH = 40
MAX_INITIAL_COUNT = 50
MAX_ANIMALS = 100
MAX_QUANTITY = 100
MAX_INVENTORY = 10000

START_GAME = 1
LOAD_GAME = 2
SETTINGS = 3
ABOUT = 4
EXIT = 5

DIFFICULTIES = {
    "Лёгкая": {"actions": 10, "money": 300, "hunger": 15},
    "Обычная": {"actions": 8, "money": 200, "hunger": 20},
    "Сложная": {"actions": 6, "money": 120, "hunger": 30},
}
FOODS = {
    "Сено": {"price": 4, "satiety": 40},
    "Зерно": {"price": 3, "satiety": 35},
}
SPECIES = {
    "Корова": {"food": "Сено", "price": 80, "product": "Молоко", "income": 16},
    "Курица": {"food": "Зерно", "price": 30, "product": "Яйца", "income": 8},
}
CONDITION_RULES = {
    "maximum": 100,
    "initial_satiety": 70,
    "initial_happiness": 60,
    "care_gain": 30,
    "happiness_loss": 10,
    "starvation_days": 3,
    "production_satiety": 30,
    "production_happiness": 40,
    "bonus_happiness": 80,
    "bonus_multiplier": 2,
    "starting_food": 5,
}


def default_settings():
    return {
        "difficulty": "Обычная",
        "animal_counts": {"Корова": 1, "Курица": 2},
        "species": deepcopy(SPECIES),
        "foods": deepcopy(FOODS),
    }


def make_rules(settings):
    return {
        "difficulty": settings["difficulty"],
        **deepcopy(DIFFICULTIES[settings["difficulty"]]),
        "foods": deepcopy(settings["foods"]),
        "species": deepcopy(settings["species"]),
        "condition": deepcopy(CONDITION_RULES),
    }
