import time
from copy import deepcopy

from config import (
    ABOUT, AUTHOR, DIFFICULTIES, EXIT, LOAD_GAME, MAX_FOODS, MAX_INCOME,
    MAX_INITIAL_COUNT, MAX_PRICE, MAX_QUANTITY, MAX_SPECIES, SAVE_INTERVAL, SETTINGS,
    START_GAME, VERSION, default_settings,
)
from game import GameError, Player
from storage import (
    PersistenceError, SaveCooldownError, Storage, add_food, add_species, check_name,
)


def read_integer(prompt, minimum, maximum, allow_cancel=False):
    while True:
        value = input(prompt).strip()
        if allow_cancel and not value:
            return None
        try:
            number = int(value)
        except ValueError:
            print("Введите целое число.")
            continue
        if minimum <= number <= maximum:
            return number
        print(f"Введите число от {minimum} до {maximum}.")


def show_menu(title, options):
    print(f"\n--- {title} ---")
    for number, label in options.items():
        print(f"{number}. {label}")
    while True:
        choice = read_integer("Ваш выбор: ", min(options), max(options))
        if choice in options:
            return choice
        print("Такого пункта меню нет.")


def choose_item(title, items):
    options = {index: item for index, item in enumerate(items, 1)}
    options[0] = "Назад"
    choice = show_menu(title, options)
    return None if choice == 0 else choice - 1


def choose_animal(player):
    animals = player.get_animals()
    if not animals:
        print("На ферме нет животных. Можно купить животных или вернуться в меню.")
        return None
    return choose_item("Выберите животное", [animal_label(animal) for animal in animals])


def animal_label(animal):
    return (f"{animal.get_species()}: счастье {animal.get_happiness()}/100, "
            f"сытость {animal.get_satiety()}/100, дней голодания {animal.get_starvation()}")


def confirm(message):
    return show_menu(message, {1: "Да", 0: "Нет"}) == 1


class FarmApplication():
    def __init__(self, storage=None, clock=time.time):
        self.__storage = Storage() if storage is None else storage
        self.__clock = clock
        self.__settings = default_settings()
        self.__running = False
        self.__autosave_from = 0

    def run(self):
        try:
            try:
                self.__settings = self.__storage.load_settings()
            except PersistenceError as error:
                print(f"Ошибка настроек: {error}")
                print("Используются настройки по умолчанию. Файл пока не изменён.")
            self.__running = True
            handlers = {
                START_GAME: self.start_game,
                LOAD_GAME: self.load_game,
                SETTINGS: self.settings,
                ABOUT: self.about,
                EXIT: self.exit,
            }
            while self.__running:
                choice = show_menu("Весёлая ферма", {
                    START_GAME: "Начать игру",
                    LOAD_GAME: "Загрузить игру",
                    SETTINGS: "Настройки",
                    ABOUT: "О программе",
                    EXIT: "Выход",
                })
                handlers[choice]()
        except (EOFError, KeyboardInterrupt):
            print("\nВвод завершён. Несохранённые изменения не записаны.")
        print("До свидания!")

    def start_game(self):
        print("Начинается новая игра.")
        self.__play(Player.new_game(self.__settings))

    def load_game(self):
        try:
            player = self.__storage.load_game()
        except PersistenceError as error:
            print(f"Не удалось загрузить игру: {error}")
            return
        print("Игра загружена.")
        self.__play(player)

    def settings(self):
        while True:
            names = list(self.__settings["animal_counts"])
            options = {1: f"Сложность: {self.__settings['difficulty']}"}
            for index, name in enumerate(names, 2):
                options[index] = f"Вид — {name}: {self.__settings['animal_counts'][name]} в начале"
            add_option = len(names) + 2
            options[add_option] = "Добавить вид животного"
            foods_option = add_option + 1
            options[foods_option] = "Корма: параметры и добавление"
            options[0] = "Назад"
            choice = show_menu("Настройки новых игр", options)
            if choice == 0:
                return
            try:
                if choice == 1:
                    self.__difficulty_settings()
                elif choice == add_option:
                    self.__species_settings()
                elif choice == foods_option:
                    self.__food_settings()
                else:
                    self.__edit_species_settings(names[choice - 2])
            except PersistenceError as error:
                print(f"Настройки не изменены: {error}")

    def __save_settings(self, settings):
        self.__storage.save_settings(settings)
        self.__settings = settings
        print("Настройки сохранены.")

    def __difficulty_settings(self):
        names = list(DIFFICULTIES)
        labels = [f"{name}: {rules['actions']} действий, {rules['money']} монет, "
                  f"потеря сытости {rules['hunger']}/день" for name, rules in DIFFICULTIES.items()]
        index = choose_item("Уровень сложности", labels)
        if index is not None:
            settings = deepcopy(self.__settings)
            settings["difficulty"] = names[index]
            self.__save_settings(settings)

    def __choose_food(self):
        foods = self.__settings["foods"]
        names = list(foods)
        index = choose_item("Подходящий корм", [
            f"{name}: цена {foods[name]['price']}, сытость +{foods[name]['satiety']}"
            for name in names
        ])
        return None if index is None else names[index]

    def __edit_species_settings(self, name):
        while True:
            profile = self.__settings["species"][name]
            choice = show_menu(f"Параметры вида — {name}", {
                1: f"Начальное количество — {name}: {self.__settings['animal_counts'][name]}",
                2: f"Подходящий корм: {profile['food']}",
                3: f"Цена покупки: {profile['price']}",
                4: f"Продукт: {profile['product']}",
                5: f"Доход за единицу продукта: {profile['income']}",
                0: "Назад",
            })
            if choice == 0:
                return
            print("Пустая строка — отмена изменения.")
            settings = deepcopy(self.__settings)
            if choice == 1:
                value = read_integer(f"Количество (0–{MAX_INITIAL_COUNT}): ",
                                     0, MAX_INITIAL_COUNT, allow_cancel=True)
                if value is None:
                    continue
                settings["animal_counts"][name] = value
            else:
                field = {2: "food", 3: "price", 4: "product", 5: "income"}[choice]
                if field == "food":
                    value = self.__choose_food()
                elif field == "product":
                    value = input("Название продукта: ").strip() or None
                else:
                    maximum = MAX_PRICE if field == "price" else MAX_INCOME
                    value = read_integer(f"Значение (1–{maximum}): ", 1, maximum, allow_cancel=True)
                if value is None:
                    continue
                settings["species"][name][field] = value
            self.__save_settings(settings)

    def __species_settings(self):
        if len(self.__settings["species"]) >= MAX_SPECIES:
            raise PersistenceError(f"Можно добавить не более {MAX_SPECIES} видов.")
        print("Пустая строка в любом поле или 0 при выборе корма — отмена добавления.")
        name = input("Название нового вида (пустая строка — отмена): ").strip()
        if not name:
            return
        check_name(name)
        if name.casefold() in {item.casefold() for item in self.__settings["species"]}:
            raise PersistenceError("Вид с таким названием уже существует.")
        food = self.__choose_food()
        if food is None:
            return
        price = read_integer(f"Цена покупки (1–{MAX_PRICE}): ", 1, MAX_PRICE, allow_cancel=True)
        if price is None:
            return
        product = input("Название продукта (пустая строка — отмена): ").strip()
        if not product:
            return
        check_name(product)
        income = read_integer(f"Доход за единицу продукта (1–{MAX_INCOME}): ",
                              1, MAX_INCOME, allow_cancel=True)
        if income is None:
            return
        count = read_integer(f"Начальное количество (0–{MAX_INITIAL_COUNT}): ",
                             0, MAX_INITIAL_COUNT, allow_cancel=True)
        if count is None:
            return
        settings = add_species(self.__settings, name, count, food, price, product, income)
        self.__save_settings(settings)

    def __food_settings(self):
        while True:
            names = list(self.__settings["foods"])
            options = {
                index: f"{name}: цена {food['price']}, сытость +{food['satiety']}"
                for index, (name, food) in enumerate(self.__settings["foods"].items(), 1)
            }
            add_option = len(names) + 1
            options[add_option] = "Добавить корм"
            options[0] = "Назад"
            choice = show_menu("Корма для новых игр", options)
            if choice == 0:
                return
            if choice == add_option:
                self.__add_food_settings()
            else:
                self.__edit_food_settings(names[choice - 1])

    def __add_food_settings(self):
        if len(self.__settings["foods"]) >= MAX_FOODS:
            raise PersistenceError(f"Можно добавить не более {MAX_FOODS} кормов.")
        print("Пустая строка в любом поле — отмена добавления.")
        name = input("Название нового корма: ").strip()
        if not name:
            return
        check_name(name)
        if name.casefold() in {item.casefold() for item in self.__settings["foods"]}:
            raise PersistenceError("Корм с таким названием уже существует.")
        price = read_integer(f"Цена порции (1–{MAX_PRICE}): ", 1, MAX_PRICE, allow_cancel=True)
        if price is None:
            return
        satiety = read_integer("Прибавка сытости (1–100): ", 1, 100, allow_cancel=True)
        if satiety is None:
            return
        self.__save_settings(add_food(self.__settings, name, price, satiety))

    def __edit_food_settings(self, name):
        while True:
            food = self.__settings["foods"][name]
            choice = show_menu(f"Параметры корма — {name}", {
                1: f"Цена порции: {food['price']}",
                2: f"Прибавка сытости: {food['satiety']}",
                0: "Назад",
            })
            if choice == 0:
                return
            field = "price" if choice == 1 else "satiety"
            maximum = MAX_PRICE if field == "price" else 100
            value = read_integer(f"Значение (1–{maximum}, пустая строка — отмена): ",
                                 1, maximum, allow_cancel=True)
            if value is not None:
                settings = deepcopy(self.__settings)
                settings["foods"][name][field] = value
                self.__save_settings(settings)

    def about(self):
        print(f"\nВесёлая ферма\nВерсия {VERSION} — выполнены лабораторные работы 1–5.")
        print(f"Автор: {AUTHOR}")
        input("Нажмите Enter для возврата в главное меню...")

    def exit(self):
        self.__running = False

    def __autosave(self, player):
        # Автосохранение раз в 30 минут игры; отсчёт от начала сеанса или последнего сохранения.
        now = self.__clock()
        last = player.get_last_saved_at()
        start = self.__autosave_from if last is None else max(self.__autosave_from, last)
        if now - start < SAVE_INTERVAL:
            return
        try:
            self.__storage.save_game(player, now=now)
            print("Автосохранение: прогресс записан.")
        except SaveCooldownError:
            self.__autosave_from = now
        except PersistenceError as error:
            self.__autosave_from = now
            print(f"Автосохранение не выполнено: {error}")

    def __play(self, player):
        self.__autosave_from = self.__clock()
        self.__inspect(player)
        while True:
            print(f"\nДень {player.get_day()} | Деньги: {player.get_money()} | Действия: {player.get_actions()}")
            if player.get_actions() == 0:
                print("Действия закончились.")
            choice = show_menu("Ферма", {
                1: "Купить животных (1 действие за покупку)",
                2: "Покормить животное (1 действие и 1 порция)",
                3: "Позаботиться о животном (1 действие)",
                4: "Купить корм (1 действие за покупку)",
                5: "Осмотреть ферму (бесплатно)",
                6: "Завершить день (бесплатно)",
                7: "Сохранить игру (бесплатно)",
                8: "Вернуться в главное меню",
            })
            try:
                if choice == 1:
                    self.__buy_animals(player)
                elif choice == 2:
                    self.__feed(player)
                elif choice == 3:
                    index = choose_animal(player)
                    if index is not None:
                        player.care(index)
                        print("Уход выполнен: счастье повышено.")
                elif choice == 4:
                    self.__buy_food(player)
                elif choice == 5:
                    self.__inspect(player)
                elif choice == 6:
                    if confirm("Завершить день? Оставшиеся действия сгорят, животные проголодаются."):
                        report = player.finish_day()
                        print(f"Получено монет: {report['income']}. Продукция: {report['products'] or 'нет'}.")
                        for name in report["deaths"]:
                            print(f"От длительного голодания погибло животное: {name}.")
                        print(f"Начался день {player.get_day()}.")
                elif choice == 7:
                    self.__storage.save_game(player)
                    print("Игра сохранена. Следующее сохранение доступно через 30 минут.")
                elif choice == 8:
                    if confirm("Закрыть ферму? Несохранённые изменения будут потеряны."):
                        return
            except (GameError, PersistenceError) as error:
                print(f"Операция отклонена: {error}")
            self.__autosave(player)

    def __inspect(self, player):
        rules = player.get_rules()
        print(f"\nФерма | День {player.get_day()} | Сложность: {rules['difficulty']}")
        print(f"Деньги: {player.get_money()} | Действия: {player.get_actions()}/{rules['actions']}")
        print(f"За день: сытость -{rules['hunger']}, счастье -{rules['condition']['happiness_loss']}.")
        print("Животные:")
        for index, animal in enumerate(player.get_animals(), 1):
            print(f"  {index}. {animal_label(animal)}")
        if not player.get_animals():
            print("  Животных нет. Купите новых; при нехватке денег начните новую игру из меню.")
        print("Корм на складе:")
        for name, count in player.get_inventory().items():
            food = rules["foods"][name]
            print(f"  {name}: {count} порций, цена {food['price']}, сытость +{food['satiety']}.")
        print("Виды и продукция (доход за единицу):")
        for name, species in rules["species"].items():
            print(f"  {name}: цена {species['price']}, корм {species['food']}, "
                  f"{species['product']} — {species['income']} монет.")
        condition = rules["condition"]
        print(f"Уход: счастье +{condition['care_gain']}. Производство после дневного обновления: "
              f"сытость от {condition['production_satiety']}, счастье от {condition['production_happiness']}.")
        print(f"При счастье от {condition['bonus_happiness']} доход и продукция "
              f"умножаются на {condition['bonus_multiplier']}. "
              f"Гибель после {condition['starvation_days']} дней с нулевой сытостью.")
        if player.get_money() == 0:
            print("Денег нет: используйте запасы, уход и дневной доход. Можно вернуться в меню и начать заново.")

    def __buy_animals(self, player):
        species = player.get_rules()["species"]
        names = list(species)
        index = choose_item("Купить животных", [f"{name} — {species[name]['price']} монет" for name in names])
        if index is None:
            return
        quantity = read_integer(f"Количество (1–{MAX_QUANTITY}, 0 — отмена): ", 0, MAX_QUANTITY)
        if quantity:
            player.buy_animals(names[index], quantity)
            print("Животные куплены.")

    def __buy_food(self, player):
        foods = player.get_rules()["foods"]
        names = list(foods)
        index = choose_item("Купить корм", [f"{name} — {foods[name]['price']} монет/порция" for name in names])
        if index is None:
            return
        quantity = read_integer(f"Количество (1–{MAX_QUANTITY}, 0 — отмена): ", 0, MAX_QUANTITY)
        if quantity:
            player.buy_food(names[index], quantity)
            print("Корм куплен.")

    def __feed(self, player):
        index = choose_animal(player)
        if index is None:
            return
        species = player.get_animals()[index].get_species()
        food = player.get_rules()["species"][species]["food"]
        choice = show_menu(f"Нужен корм: {food}. На складе: {player.get_inventory()[food]}",
                           {1: "Дать одну порцию", 0: "Назад"})
        if choice == 1:
            player.feed(index, food)
            print("Животное покормлено.")
