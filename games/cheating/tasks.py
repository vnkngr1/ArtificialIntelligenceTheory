"""
Задания контрольной и шпаргалка к ним.

Каждая контрольная собирается случайно из разных предметов. Шпаргалка
содержит всё, что нужно для ответа, но вперемешку с лишними строками —
нужное придётся поискать глазами, пока учитель не смотрит.
"""

import random
from dataclasses import dataclass, field

HISTORY = [
    ("Ледовое побоище", 1242), ("Куликовская битва", 1380), ("Крещение Руси", 988),
    ("Основание Петербурга", 1703), ("Полтавская битва", 1709), ("Бородинское сражение", 1812),
    ("Отмена крепостного права", 1861), ("Полёт Гагарина в космос", 1961),
    ("Открытие Америки Колумбом", 1492), ("Взятие Бастилии", 1789),
    ("Начало Первой мировой войны", 1914), ("Изобретение телефона Беллом", 1876),
]
CHEMISTRY = [
    ("Серная кислота", "H2SO4"), ("Азотная кислота", "HNO3"), ("Аммиак", "NH3"), ("Метан", "CH4"),
    ("Глюкоза", "C6H12O6"), ("Этанол", "C2H5OH"), ("Питьевая сода", "NaHCO3"), ("Озон", "O3"),
    ("Соляная кислота", "HCl"), ("Негашёная известь", "CaO"), ("Мел", "CaCO3"), ("Поваренная соль", "NaCl"),
]
CAPITALS = [
    ("Австралия", "Канберра"), ("Канада", "Оттава"), ("Бразилия", "Бразилиа"), ("Турция", "Анкара"),
    ("Швейцария", "Берн"), ("Новая Зеландия", "Веллингтон"), ("Казахстан", "Астана"), ("Нигерия", "Абуджа"),
    ("Марокко", "Рабат"), ("Пакистан", "Исламабад"), ("Вьетнам", "Ханой"), ("Чили", "Сантьяго"),
]

SUBJECTS = {"history": "История", "chemistry": "Химия", "capitals": "География", "formulas": "Формулы"}


@dataclass
class Task:
    subject: str                # ключ из SUBJECTS
    question: str
    answer: str                 # правильный ответ — для таблицы результатов
    numeric: bool = False
    cheat: list = field(default_factory=list)      # строки шпаргалки с ответом / подсказкой
    given: str = ""             # что ответил игрок

    def check(self, text):
        if self.numeric:
            try:
                value = float(normalize(text))
            except ValueError:
                return False
            target = float(self.answer)
            return abs(value - target) <= max(0.01, abs(target) * 0.01)
        return normalize(text) == normalize(self.answer)


def normalize(text):
    return text.strip().lower().replace("ё", "е").replace(" ", "").replace(",", ".")


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}".rstrip("0").rstrip(".")


# ---------- генераторы по предметам: (задание, лишние строки шпаргалки того же предмета) ----------

def history(rng):
    event, year = rng.choice(HISTORY)
    extra = rng.sample([h for h in HISTORY if h[0] != event], 3)
    return (Task("history", f"В каком году: {event.lower()}?", str(year), cheat=[f"{event} — {year}"]),
            [f"{e} — {y}" for e, y in extra])


def chemistry(rng):
    name, formula = rng.choice(CHEMISTRY)
    extra = rng.sample([c for c in CHEMISTRY if c[0] != name], 3)
    return (Task("chemistry", f"Запишите химическую формулу вещества «{name.lower()}».", formula,
                 cheat=[f"{name} — {formula}"]),
            [f"{n} — {f}" for n, f in extra])


def capitals(rng):
    country, capital = rng.choice(CAPITALS)
    extra = rng.sample([c for c in CAPITALS if c[0] != country], 3)
    return (Task("capitals", f"Назовите столицу страны: {country}.", capital, cheat=[f"{country} — {capital}"]),
            [f"{c} — {k}" for c, k in extra])


def _formula_tasks(rng):
    """Задачи на формулы: (строка шпаргалки, текст задачи, ответ)."""
    r = rng.randint(2, 9)
    a, b, h = rng.randint(3, 9), rng.randint(10, 16), rng.randint(2, 8)
    s, t = rng.choice([(120, 2), (180, 3), (240, 4), (150, 2.5)])
    u, res = rng.choice([(12, 4), (220, 11), (36, 9), (24, 6)])
    m, v = rng.choice([(390, 50), (270, 100), (79, 10)])
    kx, ky = rng.choice([(3, 4), (6, 8), (5, 12), (8, 15)])
    return [
        ("Площадь круга: S = π·r², π ≈ 3,14", f"Найдите площадь круга радиусом {r} (π ≈ 3,14).", round(3.14 * r * r, 2)),
        ("Площадь трапеции: S = (a + b) / 2 · h", f"Найдите площадь трапеции с основаниями {a} и {b} и высотой {h}.",
         (a + b) / 2 * h),
        ("Площадь треугольника: S = a · h / 2", f"Найдите площадь треугольника с основанием {b} и высотой {h}.", b * h / 2),
        ("Скорость: v = s / t", f"Поезд прошёл {s} км за {_fmt(t)} ч. Найдите его скорость (км/ч).", s / t),
        ("Закон Ома: I = U / R", f"Напряжение {u} В, сопротивление {res} Ом. Найдите силу тока (А).", u / res),
        ("Плотность: ρ = m / V", f"Масса тела {m} г, объём {v} см³. Найдите плотность (г/см³).", m / v),
        ("Гипотенуза: c = √(a² + b²)", f"Катеты прямоугольного треугольника {kx} и {ky}. Найдите гипотенузу.",
         (kx * kx + ky * ky) ** 0.5),
    ]


def formulas(rng):
    options = _formula_tasks(rng)
    line, question, value = rng.choice(options)
    extra = rng.sample([o[0] for o in options if o[0] != line], 3)
    return Task("formulas", question, _fmt(round(value, 2)), numeric=True, cheat=[line]), extra


GENERATORS = [history, chemistry, capitals, formulas]


def make_test(rng=None, count=5):
    """Контрольная: count заданий разных предметов и шпаргалка — {предмет: [строки]}."""
    rng = rng or random.Random()
    gens = GENERATORS[:]
    rng.shuffle(gens)
    tasks, sheet, used = [], {}, set()
    while len(tasks) < count:
        gen = gens[len(tasks) % len(gens)]
        task, extra = gen(rng)
        if task.question in used:
            continue
        used.add(task.question)
        tasks.append(task)
        lines = sheet.setdefault(task.subject, [])
        for line in task.cheat + extra:
            if line not in lines:
                lines.append(line)
    for lines in sheet.values():
        rng.shuffle(lines)
    return tasks, sheet
