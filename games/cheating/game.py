"""
Симулятор списывания на Pygame.

Вы — ученик на контрольной. Пока смотрите на экран, видно класс, учителя у
доски и свою парту с листом задания (вопрос и поле ответа). Опустили взгляд
вниз — «камера» уезжает вниз: учителя больше не видно, зато видна шпаргалка
на коленях. Цель — ответить на все вопросы по шпаргалке и не попасться.

Учитель:
  - пишет на доске (спиной к классу) — безопасно; слышен стук мела;
  - поворачивается к классу — это видно, только если смотреть на экран;
    глядя вниз, заметите лишь, что мел перестал стучать;
  - смотрит на класс: если в это время смотреть вниз, растёт подозрение;
    100% — «Что это у тебя там?!»: замечание. Три замечания — выгнали;
  - с 3-го уровня иногда оглядывается через плечо, не переставая писать:
    мел стучит, поэтому это видно, только если поднять взгляд, — одной
    тишины мела ждать мало, на класс надо иногда поглядывать.
С уровнями учитель пишет короче, поворачивается быстрее, чаще оглядывается,
делает обманные полуповороты и замечает быстрее.

Игра ничего не знает про камеру: main.py каждый кадр сообщает, смотрит ли
игрок вниз, — set_looking_down(bool). Ответы печатаются с клавиатуры.
"""

import math
import random

import numpy as np
import pygame

try:
    from .tasks import SUBJECTS, make_test      # импорт как games.cheating.game (версия «рукой»)
except ImportError:
    from tasks import SUBJECTS, make_test       # запуск games/cheating/main.py

SHIFT = 430                 # на сколько «опускается камера», когда смотрят вниз
BOARD = pygame.Rect(330, 50, 620, 230)
SHEET = pygame.Rect(330, 452, 620, 330)         # лист с заданием (координаты «холста»)
CHEAT = pygame.Rect(215, 846, 850, 340)         # шпаргалка на коленях (два столбца)
TEST_TIME = 240.0
TASKS_PER_TEST = 5
MAX_STRIKES = 3
CAUGHT_TIME = 2.2

WORK, CAUGHT, RESULT = "work", "caught", "result"
AT_BOARD, TURNING, WATCHING, TURNING_BACK, FAKE, GLANCE = (
    "board", "turning", "watching", "turning_back", "fake", "glance")
GLANCE_TIME = 0.9

WALL = (226, 214, 186)
BOARD_GREEN = (36, 72, 52)
WOOD = (168, 116, 70)
WOOD_DARK = (120, 80, 46)
PAPER = (250, 250, 244)
INK = (40, 50, 110)
TEXT = (40, 40, 48)
HUD_TEXT = (240, 242, 248)
RED = (220, 60, 60)
GREEN = (60, 170, 90)

PHRASES = ["Работаем молча!", "Смотрим в свою тетрадь.", "Осталось мало времени!", "Я всё вижу…",
           "Не разговариваем!", "Кто закончил — сдаём."]


def teacher_params(level):
    """Как ведёт себя учитель на уровне level (с 1)."""
    return {
        "board": (max(2.2, 6.5 - 0.6 * level), max(3.5, 9.5 - 0.7 * level)),   # сек у доски
        "turn": max(0.45, 0.85 - 0.06 * level),     # поворот к классу; не быстрее реакции человека
        "watch": (2.0, 3.5 + 0.3 * level),                                     # смотрит на класс
        "fake": min(0.4, 0.07 * (level - 1)),                                  # доля обманных поворотов
        "glance": max(0.0, min(0.2, 0.035 * (level - 2))),                     # оглядываний в секунду у доски
        "notice": min(150, 70 + 12 * level),                                   # %/с подозрения
    }


def _tone(samples):
    """numpy-массив сэмплов → pygame.Sound (или None, если звука нет)."""
    try:
        return pygame.sndarray.make_sound(np.ascontiguousarray((samples * 32767).astype(np.int16)))
    except (pygame.error, ValueError):
        return None


class CheatingGame:
    def __init__(self, screen, width, height, reserved=None):
        self.screen = screen
        self.width = width
        self.height = height
        self.reserved = reserved
        self.font_small = pygame.font.SysFont("arial", 16)
        self.font = pygame.font.SysFont("arial", 22)
        self.font_bold = pygame.font.SysFont("arial", 24, bold=True)
        self.font_big = pygame.font.SysFont("arial", 46, bold=True)
        self.font_hand = pygame.font.SysFont("segoeprint,comicsansms,arial", 19, bold=True)
        self.font_cheat = pygame.font.SysFont("segoeprint,comicsansms,arial", 16, bold=True)
        self.font_chalk = pygame.font.SysFont("segoeprint,comicsansms,arial", 24)
        self.font_symbol = pygame.font.SysFont("segoeuisymbol,arial", 24, bold=True)
        self.classroom = self._render_classroom()
        self.classmates = self._render_classmates()
        self.desk = self._render_desk()
        self._init_sound()
        self.level = 1
        self.best_grade = None
        self.new_test()

    # ---------- контрольная ----------

    def new_test(self, level=None):
        if level is not None:
            self.level = level
        self.params = teacher_params(self.level)
        self.tasks, self.sheet = make_test(random.Random(), TASKS_PER_TEST)
        self.cheat_surface = self._render_cheat()
        self.current = 0
        self.typed = ""
        self.time_left = TEST_TIME
        self.suspicion = 0.0
        self.strikes = 0
        self.phase = WORK
        self.phase_since = 0.0
        self.now = 0.0
        self.down = False
        self.view = 0.0                 # 0 — смотрим на класс, 1 — на шпаргалку (плавно)
        self.expelled = False
        self.bubble = None              # (текст, до какого времени) — реплика учителя
        self.last_tick = -10.0
        self.teacher = {"state": AT_BOARD, "timer": random.uniform(*self.params["board"]),
                        "turn": 0.0, "x": 640.0, "target_x": 640.0, "arm": 0.0}
        self.board_marks = []           # «мел» на доске — появляется, пока учитель пишет

    @property
    def task(self):
        return self.tasks[self.current]

    @property
    def answered(self):
        return sum(1 for t in self.tasks if t.given)

    def set_looking_down(self, down):
        self.down = bool(down) and self.phase == WORK

    # ---------- ввод ответа ----------

    def type_text(self, text):
        if self.phase == WORK and len(self.typed) < 24:
            self.typed += text

    def backspace(self):
        if self.phase == WORK:
            self.typed = self.typed[:-1]

    def submit(self):
        if self.phase != WORK or not self.typed.strip():
            return
        self.task.given = self.typed.strip()
        self.typed = ""
        self._next_task()

    def skip(self):
        if self.phase == WORK:
            self.typed = ""
            self._next_task(skip=True)

    def _next_task(self, skip=False):
        open_tasks = [i for i, t in enumerate(self.tasks) if not t.given]
        if not open_tasks:
            self._finish()
            return
        later = [i for i in open_tasks if i > self.current]
        self.current = later[0] if later else open_tasks[0]

    def _finish(self, expelled=False):
        self.phase = RESULT
        self.phase_since = self.now
        self.expelled = expelled
        self.down = False
        self.bubble = None
        if expelled:
            self.grade = 2
        else:
            correct = sum(1 for t in self.tasks if t.given and t.check(t.given))
            self.grade = {5: 5, 4: 4, 3: 3}.get(correct, 2)
        self.best_grade = self.grade if self.best_grade is None else max(self.best_grade, self.grade)

    def continue_after_result(self):
        if self.phase == RESULT:
            self.new_test(self.level + 1 if self.grade >= 4 and not self.expelled else self.level)

    # ---------- логика ----------

    def update(self, dt):
        self.now += dt
        target = 1.0 if self.down else 0.0
        self.view += (target - self.view) * min(1.0, dt * 10)
        if self.phase == RESULT:
            return
        if self.phase == CAUGHT:
            if self.now - self.phase_since > CAUGHT_TIME:
                if self.strikes >= MAX_STRIKES:
                    self._finish(expelled=True)
                else:
                    self.phase = WORK
                    self.teacher.update(state=AT_BOARD, timer=random.uniform(*self.params["board"]))
            self._update_teacher(dt)
            return

        self.time_left -= dt
        if self.time_left <= 0:
            self.time_left = 0
            self._finish()
            return
        self._update_teacher(dt)
        self._update_suspicion(dt)

    def _update_teacher(self, dt):
        t = self.teacher
        p = self.params
        t["timer"] -= dt
        state = t["state"]
        if state == AT_BOARD:
            t["turn"] = max(0.0, t["turn"] - dt * 4)
            t["x"] += (t["target_x"] - t["x"]) * min(1.0, dt * 2)
            t["arm"] = 0.5 + 0.5 * math.sin(self.now * 9)
            if self.now - self.last_tick > random.uniform(0.14, 0.4):   # стук мела
                self.last_tick = self.now
                self.board_marks.append((t["x"] + random.uniform(-60, 90), random.uniform(90, 250),
                                         random.randint(4, 14), random.randint(-3, 3)))
                self.board_marks = self.board_marks[-160:]
                self._play(self.snd_chalk)
            if t["timer"] <= 0:
                if random.random() < p["fake"]:
                    t.update(state=FAKE, timer=0.7)
                else:
                    t.update(state=TURNING, timer=p["turn"])
            elif random.random() < p["glance"] * dt:
                # оглядывается через плечо, продолжая писать; потом дописывает, сколько оставалось
                t.update(state=GLANCE, board_left=t["timer"], timer=GLANCE_TIME)
        elif state == GLANCE:
            k = 1 - t["timer"] / GLANCE_TIME
            t["turn"] = 0.75 * min(1.0, 4 * k, 4 * (1 - k))
            if self.now - self.last_tick > random.uniform(0.3, 0.6):          # мел стучит и дальше
                self.last_tick = self.now
                self._play(self.snd_chalk)
            if t["timer"] <= 0:
                t.update(state=AT_BOARD, timer=t["board_left"], turn=0.0)
        elif state == FAKE:                     # обманный полуповорот — и обратно к доске
            t["turn"] = 0.45 * math.sin(math.pi * min(1.0, 1 - t["timer"] / 0.7))
            if t["timer"] <= 0:
                t.update(state=AT_BOARD, timer=random.uniform(*p["board"]), turn=0.0)
        elif state == TURNING:
            t["turn"] = min(1.0, 1 - t["timer"] / p["turn"])
            if t["timer"] <= 0:
                t.update(state=WATCHING, timer=random.uniform(*p["watch"]), turn=1.0)
                if random.random() < 0.5 and self.phase == WORK:
                    self.bubble = (random.choice(PHRASES), self.now + 1.8)
        elif state == WATCHING:
            if t["timer"] <= 0 and self.phase == WORK:
                t.update(state=TURNING_BACK, timer=0.4)
        elif state == TURNING_BACK:
            t["turn"] = max(0.0, t["timer"] / 0.4)
            if t["timer"] <= 0:
                t.update(state=AT_BOARD, timer=random.uniform(*p["board"]), turn=0.0,
                         target_x=random.uniform(430, 850))

    def _update_suspicion(self, dt):
        t = self.teacher
        watching = t["state"] == WATCHING or (t["state"] in (TURNING, GLANCE) and t["turn"] > 0.55)
        if watching and self.down:
            rate = self.params["notice"] * {WATCHING: 1.0, TURNING: 0.5, GLANCE: 0.7}[t["state"]]
            self.suspicion += rate * dt
        else:
            self.suspicion -= (6 if watching else 10) * dt
        self.suspicion = max(0.0, min(100.0, self.suspicion))
        if self.suspicion >= 100:
            self._caught()

    def _caught(self):
        self.strikes += 1
        self.phase = CAUGHT
        self.phase_since = self.now
        self.down = False
        self.suspicion = 35.0
        self.teacher.update(state=WATCHING, timer=CAUGHT_TIME, turn=1.0)
        last = self.strikes >= MAX_STRIKES
        self.bubble = ("Всё, сдавай работу! Родителей в школу!" if last else "Что это там у тебя?! Замечание!",
                       self.now + CAUGHT_TIME)
        self._play(self.snd_caught)

    # ---------- звук ----------

    def _init_sound(self):
        self.snd_chalk = self.snd_caught = None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(22050, -16, 1)
            rate = pygame.mixer.get_init()[0]
            n = int(rate * 0.035)
            noise = np.random.uniform(-1, 1, n) * np.exp(-np.linspace(0, 7, n)) * 0.35
            self.snd_chalk = _tone(np.diff(noise, prepend=0.0))             # «щелчок» мела
            n = int(rate * 0.45)
            tt = np.linspace(0, 0.45, n, endpoint=False)
            buzz = np.sign(np.sin(2 * np.pi * 120 * tt)) * np.exp(-tt * 4) * 0.25
            self.snd_caught = _tone(buzz)
            channels = pygame.mixer.get_init()[2]
            if channels == 2:                    # микшер оказался стерео — нужен двухканальный массив
                self.snd_chalk = _tone(np.column_stack([np.diff(noise, prepend=0.0)] * 2))
                self.snd_caught = _tone(np.column_stack([buzz] * 2))
        except (pygame.error, ImportError, TypeError):
            pass                                  # без звука — будут только надписи

    @staticmethod
    def _play(sound):
        if sound is not None:
            try:
                sound.play()
            except pygame.error:
                pass

    # ---------- отрисовка ----------

    def draw(self):
        s = self.screen
        oy = -int(self.view * SHIFT)
        s.fill((30, 26, 24))
        if oy > -SHIFT + 2:
            s.blit(self.classroom, (0, oy))
            for x, y, dx, dy in self.board_marks:
                pygame.draw.line(s, (220, 225, 215), (x, y + oy), (x + dx, y + oy + dy), 2)
            self._draw_clock(oy)
            self._draw_teacher(oy)
            s.blit(self.classmates, (0, oy))
        s.blit(self.desk, (0, SHIFT + oy))
        self._draw_sheet(oy)
        self._draw_cheat(oy)
        self._draw_hud()
        if self.phase == CAUGHT:
            k = 1 - (self.now - self.phase_since) / CAUGHT_TIME
            flash = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            flash.fill((200, 20, 20, int(90 * max(0.0, k))))
            s.blit(flash, (0, 0))
        if self.phase == RESULT:
            self._draw_result()

    def _render_classroom(self):
        surf = pygame.Surface((self.width, SHIFT))
        for y in range(SHIFT):
            k = y / SHIFT
            surf.fill([int(c * (1 - 0.12 * k)) for c in WALL], (0, y, self.width, 1))
        pygame.draw.rect(surf, (120, 86, 52), BOARD.inflate(24, 24), border_radius=6)
        pygame.draw.rect(surf, BOARD_GREEN, BOARD)
        pygame.draw.rect(surf, (140, 104, 64), (BOARD.left - 12, BOARD.bottom + 10, BOARD.width + 24, 10))
        for i, line in enumerate(("Контрольная работа", "Вариант 1", "Время: 4 минуты")):
            txt = self.font_chalk.render(line, True, (225, 228, 220))
            surf.blit(txt, (BOARD.left + 30, BOARD.top + 18 + i * 38))
        # окно слева
        win = pygame.Rect(40, 60, 190, 220)
        pygame.draw.rect(surf, (180, 215, 240), win)
        pygame.draw.rect(surf, (250, 250, 250), win, 10)
        pygame.draw.line(surf, (250, 250, 250), (win.centerx, win.top), (win.centerx, win.bottom), 6)
        pygame.draw.line(surf, (250, 250, 250), (win.left, win.centery), (win.right, win.centery), 6)
        # портрет справа
        portrait = pygame.Rect(1080, 80, 110, 140)
        pygame.draw.rect(surf, (90, 70, 50), portrait, border_radius=4)
        pygame.draw.rect(surf, (200, 190, 170), portrait.inflate(-16, -16))
        pygame.draw.circle(surf, (120, 100, 90), (portrait.centerx, portrait.centery - 12), 22)
        pygame.draw.rect(surf, (80, 70, 70), (portrait.centerx - 30, portrait.centery + 12, 60, 40), border_radius=10)
        return surf

    def _render_classmates(self):
        """Затылки одноклассников на переднем плане — учитель виден между ними."""
        surf = pygame.Surface((self.width, SHIFT), pygame.SRCALPHA)
        rng = random.Random(3)
        for row_y, r, xs in ((350, 30, (150, 330, 950, 1130)), (412, 40, (60, 290, 1000, 1230))):
            for x in xs:
                shirt = rng.choice([(70, 80, 110), (110, 60, 60), (60, 100, 80), (90, 90, 90)])
                hair = rng.choice([(50, 35, 25), (90, 60, 30), (30, 30, 30), (150, 110, 60)])
                pygame.draw.ellipse(surf, shirt, (x - r * 2.2, row_y + r * 0.6, r * 4.4, r * 3))
                pygame.draw.circle(surf, (230, 190, 160), (x, row_y), r)
                pygame.draw.circle(surf, hair, (x, row_y - 4), r)
        return surf

    def _render_desk(self):
        surf = pygame.Surface((self.width, self.height))       # от края класса (y = SHIFT) до низа колен
        surf.fill(WOOD)
        rng = random.Random(5)
        for _ in range(40):                                   # текстура дерева
            y = rng.uniform(0, 370)
            pygame.draw.line(surf, WOOD_DARK, (0, y), (self.width, y + rng.uniform(-8, 8)), 1)
        pygame.draw.rect(surf, (90, 58, 32), (0, 370, self.width, 34))            # край парты
        pygame.draw.rect(surf, (44, 52, 84), (0, 404, self.width, surf.get_height() - 404))   # колени, брюки
        for x in range(0, self.width, 140):
            pygame.draw.arc(surf, (36, 42, 70), (x, 460, 180, 260), 0.3, 2.6, 3)
        # пенал и линейка
        pygame.draw.rect(surf, (60, 90, 150), (1000, 60, 200, 60), border_radius=16)
        pygame.draw.rect(surf, (240, 220, 120), (980, 250, 250, 22))
        for i in range(0, 250, 12):
            pygame.draw.line(surf, (80, 70, 40), (980 + i, 250), (980 + i, 258 if i % 60 else 264), 1)
        return surf

    def _render_cheat(self):
        """Шпаргалка: мятый листок, мелкий почерк, по предметам."""
        w, h = CHEAT.size
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (246, 244, 226), (0, 0, w, h), border_radius=6)
        rng = random.Random(len(self.tasks[0].question))
        for _ in range(6):                                    # «помятости»
            x1, y1, x2, y2 = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(0, w), rng.uniform(0, h)
            pygame.draw.line(surf, (225, 222, 200), (x1, y1), (x2, y2), 1)
        step = 19
        for y in range(32, h, step):
            pygame.draw.line(surf, (200, 214, 232), (8, y), (w - 8, y), 1)
        pygame.draw.line(surf, (230, 200, 200), (w // 2, 10), (w // 2, h - 10), 1)
        # предметы раскладываем в два столбца, чтобы всё поместилось на коленях
        blocks = sorted(self.sheet.items(), key=lambda kv: -len(kv[1]))
        columns = [[], []]
        heights = [0, 0]
        for subject, lines in blocks:
            col = 0 if heights[0] <= heights[1] else 1
            columns[col].append((subject, lines))
            heights[col] += len(lines) + 1
        for col, items in enumerate(columns):
            x, y = 14 + col * (w // 2), 10
            for subject, lines in items:
                surf.blit(self.font_cheat.render(SUBJECTS[subject] + ":", True, (170, 40, 40)), (x, y))
                y += step
                for line in lines:
                    surf.blit(self.font_cheat.render(line, True, INK), (x + 8, y))
                    y += step
                y += 4
        return pygame.transform.rotate(surf, -1.5)

    def _draw_clock(self, oy):
        cx, cy = 640, 26 + oy
        pygame.draw.circle(self.screen, (250, 250, 250), (cx, cy), 20)
        pygame.draw.circle(self.screen, (60, 60, 60), (cx, cy), 20, 2)
        k = 1 - self.time_left / TEST_TIME
        a = -math.pi / 2 + 2 * math.pi * k
        pygame.draw.line(self.screen, (200, 40, 40), (cx, cy), (cx + math.cos(a) * 15, cy + math.sin(a) * 15), 2)

    def _draw_teacher(self, oy):
        s = self.screen
        t = self.teacher
        x, k = int(t["x"]), t["turn"]
        angry = self.phase == CAUGHT
        jacket = (150, 60, 70) if angry else (96, 74, 120)
        pygame.draw.rect(s, jacket, (x - 58, 196 + oy, 116, 210), border_radius=30)          # туловище
        if t["state"] == AT_BOARD:                                                         # рука с мелом у доски
            hand = (x + 78 + t["arm"] * 20, 130 + oy + t["arm"] * 30)
            pygame.draw.line(s, jacket, (x + 42, 214 + oy), hand, 18)
            pygame.draw.circle(s, (236, 200, 170), hand, 10)
        else:
            pygame.draw.line(s, jacket, (x + 48, 216 + oy), (x + 60, 320 + oy), 18)
            pygame.draw.line(s, jacket, (x - 48, 216 + oy), (x - 60, 320 + oy), 18)
        head_w = 0.62 + 0.38 * abs(2 * k - 1)                                              # «поворот» головы
        head = pygame.Rect(0, 0, int(76 * head_w), 80)
        head.center = (x, 160 + oy)
        hair = (74, 50, 36)
        if k < 0.5:                                                                       # затылок
            pygame.draw.ellipse(s, (236, 200, 170), head)
            pygame.draw.ellipse(s, hair, head.inflate(4, 6))
            pygame.draw.circle(s, hair, (x, head.top + 4), 18)                            # пучок
        else:                                                                             # лицо к классу
            pygame.draw.ellipse(s, (236, 200, 170), head)
            pygame.draw.ellipse(s, hair, (head.left - 2, head.top - 6, head.width + 4, 34))
            eye_y = head.centery - 4
            look = 0 if self.phase == CAUGHT else math.sin(self.now * 1.3) * 4             # водит глазами по классу
            for dx in (-14, 14):
                ex = x + int(dx * head_w)
                pygame.draw.circle(s, (255, 255, 255), (ex, eye_y), 8)
                pygame.draw.circle(s, (40, 40, 40), (ex + look, eye_y + 1), 4)
                pygame.draw.circle(s, (60, 60, 60), (ex, eye_y), 11, 2)                   # очки
            brow = 6 if angry or self.suspicion > 60 else 0
            pygame.draw.line(s, hair, (x - 24, eye_y - 14), (x - 6, eye_y - 14 + brow), 3)
            pygame.draw.line(s, hair, (x + 24, eye_y - 14), (x + 6, eye_y - 14 + brow), 3)
            mouth = pygame.Rect(x - 12, head.bottom - 22, 24, 10)
            pygame.draw.arc(s, (150, 60, 60), mouth, 0.2 if angry else math.pi + 0.3, math.pi - 0.2 if angry else -0.3, 3)
        if self.bubble and self.now < self.bubble[1]:
            self._draw_bubble(self.bubble[0], (x + 60, 70 + oy), angry)

    def _draw_bubble(self, text, anchor, angry):
        txt = self.font_bold.render(text, True, (180, 20, 20) if angry else TEXT)
        box = txt.get_rect(bottomleft=anchor).inflate(28, 18)
        box.right = min(box.right, self.width - 310)            # не заезжать под панель со временем
        pygame.draw.rect(self.screen, (255, 255, 255), box, border_radius=14)
        pygame.draw.rect(self.screen, (60, 60, 60), box, 2, border_radius=14)
        pygame.draw.polygon(self.screen, (255, 255, 255), [(box.left + 20, box.bottom - 2), (box.left + 36, box.bottom - 2),
                                                           (box.left + 10, box.bottom + 16)])
        self.screen.blit(txt, txt.get_rect(center=box.center))

    def _draw_sheet(self, oy):
        s = self.screen
        r = SHEET.move(0, oy)
        pygame.draw.rect(s, (110, 72, 40), r.move(6, 8), border_radius=4)          # тень листа на парте
        pygame.draw.rect(s, PAPER, r, border_radius=4)
        for y in range(r.top + 60, r.bottom - 10, 26):
            pygame.draw.line(s, (214, 226, 240), (r.left + 16, y), (r.right - 16, y), 1)
        pygame.draw.line(s, (240, 170, 170), (r.left + 50, r.top + 8), (r.left + 50, r.bottom - 8), 2)
        head = f"Контрольная работа  ·  задание {self.current + 1} из {len(self.tasks)}  ·  {SUBJECTS[self.task.subject]}"
        s.blit(self.font_small.render(head, True, (110, 110, 120)), (r.left + 62, r.top + 16))
        y = r.top + 52
        for line in self._wrap(self.task.question, self.font_bold, r.width - 90):
            s.blit(self.font_bold.render(line, True, TEXT), (r.left + 62, y))
            y += 32
        box = pygame.Rect(r.left + 62, r.bottom - 118, r.width - 110, 52)
        pygame.draw.rect(s, (255, 255, 255), box, border_radius=8)
        pygame.draw.rect(s, INK, box, 2, border_radius=8)
        caret = "|" if int(self.now * 2) % 2 and self.phase == WORK else ""
        s.blit(self.font_hand.render("Ответ: " + self.typed + caret, True, INK), (box.left + 12, box.top + 12))
        hint = "Печатайте ответ · Enter — записать · Tab — пропустить"
        s.blit(self.font_small.render(hint, True, (120, 120, 130)), (r.left + 62, r.bottom - 56))
        for i, t in enumerate(self.tasks):                      # отмеченные задания
            cell = pygame.Rect(r.left + 62 + i * 34, r.bottom - 32, 26, 22)
            color = (150, 200, 160) if t.given else (225, 225, 230)
            pygame.draw.rect(s, color, cell, border_radius=4)
            if i == self.current:
                pygame.draw.rect(s, INK, cell, 2, border_radius=4)
            s.blit(self.font_small.render(str(i + 1), True, TEXT), (cell.x + 8, cell.y + 2))

    def _draw_cheat(self, oy):
        s = self.screen
        if CHEAT.top + oy > self.height:
            return
        self._draw_cheat_sheet(oy)
        if self.view > 0.6:
            ticking = self.now - self.last_tick < 0.7 and self.teacher["state"] in (AT_BOARD, GLANCE)
            sound = "[ стук мела: учитель пишет на доске ]" if ticking else "[ тишина… мел не стучит ]"
            color = (200, 220, 200) if ticking else (255, 190, 120)
            txt = self.font.render(sound, True, color)
            box = txt.get_rect(midtop=(self.width // 2, SHEET.bottom + oy + 14)).inflate(20, 8)
            pygame.draw.rect(s, (20, 20, 26), box, border_radius=8)
            s.blit(txt, txt.get_rect(center=box.center))

    def _draw_cheat_sheet(self, oy):
        """Шпаргалка на коленях. В версии «рукой» (games/cheating_hand) её держит рука."""
        self.screen.blit(self.cheat_surface, (CHEAT.left, CHEAT.top + oy))

    def _draw_hud(self):
        s = self.screen
        panel = pygame.Rect(self.width - 300, 12, 288, 142)
        bg = pygame.Surface(panel.size, pygame.SRCALPHA)
        bg.fill((20, 22, 30, 200))
        s.blit(bg, panel)
        x, y = panel.left + 14, panel.top + 10
        minutes, seconds = divmod(int(math.ceil(self.time_left)), 60)
        s.blit(self.font_bold.render(f"Время {minutes}:{seconds:02d}", True, HUD_TEXT), (x, y))
        s.blit(self.font_small.render(f"Уровень {self.level}", True, (170, 176, 196)), (panel.right - 90, y + 4))
        y += 34
        s.blit(self.font_small.render("Замечания:", True, HUD_TEXT), (x, y))
        for i in range(MAX_STRIKES):
            pygame.draw.circle(s, RED if i < self.strikes else (80, 84, 100), (x + 110 + i * 24, y + 10), 8)
        y += 30
        s.blit(self.font_small.render("Учитель подозревает:", True, HUD_TEXT), (x, y))
        y += 22
        bar = pygame.Rect(x, y, panel.width - 28, 16)
        pygame.draw.rect(s, (60, 64, 80), bar, border_radius=5)
        k = self.suspicion / 100
        color = (int(90 + 165 * k), int(200 - 150 * k), 80)
        pygame.draw.rect(s, color, (bar.x, bar.y, int(bar.width * k), bar.height), border_radius=5)

    def _draw_result(self):
        s = self.screen
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((10, 12, 18, 225))
        s.blit(overlay, (0, 0))
        title = "Выгнали с контрольной!" if self.expelled else "Контрольная сдана"
        txt = self.font_big.render(title, True, RED if self.expelled else HUD_TEXT)
        s.blit(txt, txt.get_rect(center=(self.width // 2, 70)))
        grade_color = {5: GREEN, 4: (140, 200, 90), 3: (230, 190, 70)}.get(self.grade, RED)
        grade = self.font_big.render(f"Оценка: {self.grade}", True, grade_color)
        s.blit(grade, grade.get_rect(center=(self.width // 2, 130)))
        y = 190
        for i, t in enumerate(self.tasks):
            ok = bool(t.given) and t.check(t.given)
            mark = "✓" if ok else "✗"
            row = f"{i + 1}. {t.question}"
            s.blit(self.font_symbol.render(mark, True, GREEN if ok else RED), (150, y))
            s.blit(self.font.render(self._clip(row, 700), True, HUD_TEXT), (185, y))
            given = t.given or "— нет ответа —"
            s.blit(self.font_small.render(f"ваш ответ: {given}   ·   верно: {t.answer}", True, (170, 176, 196)),
                   (185, y + 28))
            y += 64
        info = f"Замечаний: {self.strikes} из {MAX_STRIKES}"
        s.blit(self.font.render(info, True, HUD_TEXT), (185, y + 6))
        nxt = self.level + 1 if self.grade >= 4 and not self.expelled else self.level
        hint = f"Enter — следующая контрольная (уровень {nxt}: учитель {'внимательнее' if nxt > self.level else 'тот же'})"
        txt = self.font.render(hint, True, (255, 205, 80))
        s.blit(txt, txt.get_rect(center=(self.width // 2, self.height - 60)))

    def _clip(self, text, width):
        if self.font.size(text)[0] <= width:
            return text
        while self.font.size(text + "…")[0] > width and len(text) > 4:
            text = text[:-1]
        return text + "…"

    @staticmethod
    def _wrap(text, font, width):
        lines, line = [], ""
        for word in text.split():
            candidate = f"{line} {word}".strip()
            if font.size(candidate)[0] <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines
