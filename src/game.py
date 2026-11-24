from copy import deepcopy

from config import MAX_ANIMALS, MAX_INVENTORY, MAX_QUANTITY, make_rules


class GameError(ValueError):
    pass


def check_quantity(quantity):
    if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
        raise GameError(f"Количество должно быть от 1 до {MAX_QUANTITY}.")


class Food():
    def __init__(self, name, price, satiety):
        self.__name = name
        self.__price = price
        self.__satiety = satiety

    def get_name(self):
        return self.__name

    def get_price(self):
        return self.__price

    def get_satiety(self):
        return self.__satiety


class Animal():
    def __init__(self, species, happiness, satiety, starvation=0):
        self.__species = species
        self.__happiness = happiness
        self.__satiety = satiety
        self.__starvation = starvation

    def get_species(self):
        return self.__species

    def get_happiness(self):
        return self.__happiness

    def get_satiety(self):
        return self.__satiety

    def get_starvation(self):
        return self.__starvation

    def feed(self, food, maximum):
        self.__satiety = min(maximum, self.__satiety + food.get_satiety())
        self.__starvation = 0

    def care(self, gain, maximum):
        self.__happiness = min(maximum, self.__happiness + gain)

    def finish_day(self, hunger, condition):
        self.__satiety = max(0, self.__satiety - hunger)
        self.__happiness = max(0, self.__happiness - condition["happiness_loss"])
        self.__starvation = self.__starvation + 1 if self.__satiety == 0 else 0
        return self.__starvation < condition["starvation_days"]

    def to_data(self):
        return {
            "species": self.__species,
            "happiness": self.__happiness,
            "satiety": self.__satiety,
            "starvation": self.__starvation,
        }


class Player():
    def __init__(self, day, money, actions, animals, inventory, rules, last_saved_at=None):
        self.__day = day
        self.__money = money
        self.__actions = actions
        self.__animals = list(animals)
        self.__inventory = dict(inventory)
        self.__rules = deepcopy(rules)
        self.__last_saved_at = last_saved_at

    @classmethod
    def new_game(cls, settings):
        rules = make_rules(settings)
        condition = rules["condition"]
        animals = [
            Animal(name, condition["initial_happiness"], condition["initial_satiety"])
            for name, count in settings["animal_counts"].items()
            for _ in range(count)
        ]
        inventory = {name: condition["starting_food"] for name in rules["foods"]}
        return cls(1, rules["money"], rules["actions"], animals, inventory, rules)

    def get_day(self):
        return self.__day

    def get_money(self):
        return self.__money

    def get_actions(self):
        return self.__actions

    def get_animals(self):
        return list(self.__animals)

    def get_inventory(self):
        return dict(self.__inventory)

    def get_rules(self):
        return deepcopy(self.__rules)

    def get_last_saved_at(self):
        return self.__last_saved_at

    def set_last_saved_at(self, timestamp):
        self.__last_saved_at = timestamp

    def __check_action(self):
        if self.__actions == 0:
            raise GameError("Действия закончились. Выберите «Завершить день» (6).")

    def __get_animal(self, index):
        if type(index) is not int or not 0 <= index < len(self.__animals):
            raise GameError("Такого животного нет.")
        return self.__animals[index]

    def buy_animals(self, species, quantity):
        check_quantity(quantity)
        self.__check_action()
        if species not in self.__rules["species"]:
            raise GameError("Неизвестный вид животного.")
        if len(self.__animals) + quantity > MAX_ANIMALS:
            raise GameError(f"На ферме может быть не более {MAX_ANIMALS} животных.")
        cost = self.__rules["species"][species]["price"] * quantity
        if cost > self.__money:
            raise GameError("Недостаточно денег.")
        condition = self.__rules["condition"]
        animals = [Animal(species, condition["initial_happiness"], condition["initial_satiety"])
                   for _ in range(quantity)]
        self.__animals.extend(animals)
        self.__money -= cost
        self.__actions -= 1

    def buy_food(self, name, quantity):
        check_quantity(quantity)
        self.__check_action()
        if name not in self.__rules["foods"]:
            raise GameError("Неизвестный корм.")
        if self.__inventory[name] + quantity > MAX_INVENTORY:
            raise GameError(f"Лимит запаса корма: {MAX_INVENTORY}.")
        cost = self.__rules["foods"][name]["price"] * quantity
        if cost > self.__money:
            raise GameError("Недостаточно денег.")
        self.__inventory[name] += quantity
        self.__money -= cost
        self.__actions -= 1

    def feed(self, index, food_name):
        self.__check_action()
        animal = self.__get_animal(index)
        if food_name != self.__rules["species"][animal.get_species()]["food"]:
            raise GameError("Этот корм не подходит животному.")
        if self.__inventory[food_name] == 0:
            raise GameError("Подходящий корм закончился.")
        maximum = self.__rules["condition"]["maximum"]
        if animal.get_satiety() == maximum:
            raise GameError("Животное уже сыто.")
        food = Food(food_name, **self.__rules["foods"][food_name])
        animal.feed(food, maximum)
        self.__inventory[food_name] -= 1
        self.__actions -= 1

    def care(self, index):
        self.__check_action()
        animal = self.__get_animal(index)
        condition = self.__rules["condition"]
        if animal.get_happiness() == condition["maximum"]:
            raise GameError("Животное уже полностью счастливо.")
        animal.care(condition["care_gain"], condition["maximum"])
        self.__actions -= 1

    def finish_day(self):
        condition = self.__rules["condition"]
        survivors = []
        deaths = []
        products = {}
        income = 0
        for animal in self.__animals:
            if not animal.finish_day(self.__rules["hunger"], condition):
                deaths.append(animal.get_species())
                continue
            survivors.append(animal)
            if (animal.get_satiety() >= condition["production_satiety"]
                    and animal.get_happiness() >= condition["production_happiness"]):
                species = self.__rules["species"][animal.get_species()]
                amount = (condition["bonus_multiplier"]
                          if animal.get_happiness() >= condition["bonus_happiness"] else 1)
                products[species["product"]] = products.get(species["product"], 0) + amount
                income += species["income"] * amount
        self.__animals = survivors
        self.__money += income
        self.__actions = self.__rules["actions"]
        self.__day += 1
        return {"income": income, "products": products, "deaths": deaths}

    def to_data(self):
        return {
            "day": self.__day,
            "money": self.__money,
            "actions": self.__actions,
            "animals": [animal.to_data() for animal in self.__animals],
            "inventory": dict(self.__inventory),
            "rules": deepcopy(self.__rules),
            "last_saved_at": self.__last_saved_at,
        }
