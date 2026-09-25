"""
Пасьянс «Паук» на Pygame.

Правила (классические):
  - 104 карты (две колоды), 10 колонок: в первых четырёх по 6 карт,
    в остальных по 5, открыта только верхняя. Остальные 50 карт — в запасе,
    это 5 раздач по одной карте в каждую колонку.
  - Карту можно положить на карту на единицу старше (любой масти) или
    в пустую колонку. Группу карт можно переносить, только если она
    одной масти и идёт по убыванию подряд.
  - Собранная в колонке последовательность одной масти от короля до туза
    уходит в «дом». Цель — собрать все 8 таких последовательностей.
  - Раздавать из запаса можно, только если нет пустых колонок.
  Сложность: 1 масть (пики), 2 масти или 4 масти.

Управление упрощено под «ввод одной кнопкой» (моргание):
  - выбранная колонка — select() / move_selection();
  - action(): если в руке ничего нет — взять из выбранной колонки её верхнюю
    переносимую группу; если что-то уже взято — положить в выбранную колонку
    (переносится ровно та часть группы, которая туда подходит). action() на
    той же колонке — отменить;
  - deal() — раздача из запаса, undo() — отмена хода.

Игра ничего не знает про камеру: откуда пришли команды — не её забота.
"""

import copy
import random
from dataclasses import dataclass

import pygame

# ---------- раскладка ----------

COLUMNS = 10
CARD_W, CARD_H = 88, 124
COL_STEP = 106
LEFT = 26
TABLE_TOP = 138
GAP_DOWN = 14           # сдвиг между закрытыми картами
GAP_UP = 30             # сдвиг между открытыми картами

SUIT_SYMBOLS = ["♠", "♥", "♦", "♣"]
RED_SUITS = {1, 2}
RANK_NAMES = {1: "A", 11: "J", 12: "Q", 13: "K"}

START_SCORE = 500
MESSAGE_TIME = 2.2

# ---------- цвета ----------

FELT_TOP = (22, 110, 64)
FELT_BOTTOM = (12, 70, 40)
TEXT_COLOR = (240, 244, 240)
MUTED_TEXT = (170, 205, 180)
CURSOR_COLOR = (255, 215, 60)
DROP_COLOR = (90, 230, 120)
HELD_COLOR = (80, 200, 255)


@dataclass
class Card:
    rank: int            # 1 (туз) .. 13 (король)
    suit: int            # 0 ♠, 1 ♥, 2 ♦, 3 ♣
    face_up: bool = False


def column_x(i):
    return LEFT + i * COL_STEP


class SpiderGame:
    def __init__(self, screen, width, height):
        self.screen = screen
        self.width = width
        self.height = height

        self.font_small = pygame.font.SysFont("arial", 16)
        self.font = pygame.font.SysFont("arial", 22, bold=True)
        self.font_big = pygame.font.SysFont("arial", 52, bold=True)
        self.card_font = pygame.font.SysFont("arial", 21, bold=True)
        self.card_suit_big = pygame.font.SysFont("segoeuisymbol,arial", 44)

        self.background = self._render_background()
        self._face_cache = {}
        self.back = self._render_back()
        self.suits = 1
        self.now = 0.0
        self.reset()

    # ---------- партия ----------

    def reset(self, suits=None):
        if suits is not None:
            self.suits = suits
        suit_set = {1: [0], 2: [0, 1], 4: [0, 1, 2, 3]}[self.suits]
        deck = [Card(rank, suit) for suit in suit_set for _ in range(8 // len(suit_set))
                for rank in range(1, 14)]
        random.shuffle(deck)

        self.columns = [[] for _ in range(COLUMNS)]
        for i in range(54):
            self.columns[i % COLUMNS].append(deck.pop())
        for col in self.columns:
            col[-1].face_up = True
        self.stock = deck                # 50 карт = 5 раздач
        self.completed = []              # масти собранных последовательностей
        self.score = START_SCORE
        self.moves = 0
        self.history = []

        self.selected = 0
        self.held_from = None            # колонка, из которой взяты карты
        self.held_count = 0
        self.flash = {}                  # колонка → (время, цвет) — короткая подсветка
        self.message = None
        self.message_until = 0.0
        self.won = False

    @property
    def deals_left(self):
        return len(self.stock) // COLUMNS

    def movable_run(self, col):
        """Сколько верхних карт колонки можно перенести разом (одна масть, по убыванию подряд)."""
        cards = self.columns[col]
        if not cards:
            return 0
        n = 1
        while n < len(cards):
            upper, lower = cards[-n], cards[-n - 1]
            if not lower.face_up or lower.suit != upper.suit or lower.rank != upper.rank + 1:
                break
            n += 1
        return n

    def fitting_count(self, target):
        """Сколько карт из взятой группы встанет в колонку target (0 — никак)."""
        if self.held_from is None or target == self.held_from:
            return 0
        run = self.columns[self.held_from][-self.held_count:]
        dest = self.columns[target]
        if not dest:
            return len(run)
        need = dest[-1].rank - 1
        for i, card in enumerate(run):
            if card.rank == need:
                return len(run) - i
        return 0

    # ---------- команды ----------

    def select(self, col):
        self.selected = max(0, min(COLUMNS - 1, col))

    def move_selection(self, step):
        self.select(self.selected + step)

    def action(self):
        """Моргание: взять группу из выбранной колонки или положить взятую."""
        if self.won:
            return
        if self.held_from is None:
            n = self.movable_run(self.selected)
            if n == 0:
                self._say("Колонка пустая — брать нечего")
                return
            self.held_from, self.held_count = self.selected, n
        elif self.selected == self.held_from:
            self.held_from = None                     # передумали
        else:
            self._move(self.held_from, self.selected)

    def cancel(self):
        self.held_from = None

    def _move(self, src, dst):
        n = self.fitting_count(dst)
        self.held_from = None
        if n == 0:
            self._say("Сюда эти карты не встанут")
            self.flash[dst] = (self.now, (240, 80, 80))
            return
        self._save()
        cards = self.columns[src][-n:]
        del self.columns[src][-n:]
        self.columns[dst].extend(cards)
        self._open_top(src)
        self.score -= 1
        self.moves += 1
        self.flash[dst] = (self.now, DROP_COLOR)
        self._check_complete(dst)

    def deal(self):
        """Долгое закрытие глаз: раздать по карте в каждую колонку."""
        if self.won:
            self.reset()
            return
        self.held_from = None
        if not self.stock:
            self._say("Запас пуст — раздавать больше нечего")
            return
        if any(not col for col in self.columns):
            self._say("Нельзя раздать, пока есть пустая колонка")
            return
        self._save()
        for i, col in enumerate(self.columns):
            card = self.stock.pop()
            card.face_up = True
            col.append(card)
            self.flash[i] = (self.now, HELD_COLOR)
        self.moves += 1
        for i in range(COLUMNS):
            self._check_complete(i)

    def undo(self):
        if not self.history:
            self._say("Отменять нечего")
            return
        self.columns, self.stock, self.completed, self.score, self.moves = self.history.pop()
        self.held_from = None
        self.score -= 1       # как в классическом «Пауке»: отмена не бесплатна

    def _save(self):
        self.history.append(copy.deepcopy((self.columns, self.stock, self.completed, self.score, self.moves)))
        del self.history[:-200]

    def _open_top(self, col):
        if self.columns[col] and not self.columns[col][-1].face_up:
            self.columns[col][-1].face_up = True

    def _check_complete(self, col):
        cards = self.columns[col]
        if len(cards) < 13:
            return
        top = cards[-13:]
        if all(c.face_up and c.suit == top[0].suit and c.rank == 13 - i for i, c in enumerate(top)):
            del cards[-13:]
            self.completed.append(top[0].suit)
            self.score += 100
            self._open_top(col)
            self._say("Масть собрана! +100")
            if len(self.completed) == 8:
                self.won = True

    def _say(self, text):
        self.message = text
        self.message_until = self.now + MESSAGE_TIME

    def update(self, dt):
        self.now += dt
        if self.message and self.now > self.message_until:
            self.message = None
        self.flash = {k: v for k, v in self.flash.items() if self.now - v[0] < 0.5}

    # ---------- отрисовка ----------

    def column_gaps(self, col):
        """Сдвиги между картами; если колонка не влезает по высоте — сжимаем."""
        cards = self.columns[col]
        downs = sum(1 for c in cards[:-1] if not c.face_up)
        ups = max(0, len(cards) - 1 - downs)
        room = self.height - 72 - TABLE_TOP - CARD_H
        need = downs * GAP_DOWN + ups * GAP_UP
        k = min(1.0, room / need) if need else 1.0
        return GAP_DOWN * k, GAP_UP * k

    def card_positions(self, col):
        gap_down, gap_up = self.column_gaps(col)
        y = TABLE_TOP
        positions = []
        for card in self.columns[col]:
            positions.append(y)
            y += gap_up if card.face_up else gap_down
        return positions

    def column_bottom(self, col):
        ys = self.card_positions(col)
        return (ys[-1] if ys else TABLE_TOP) + CARD_H

    def draw(self):
        self.screen.blit(self.background, (0, 0))
        self._draw_top_bar()

        drop_targets = set()
        if self.held_from is not None:
            drop_targets = {i for i in range(COLUMNS) if self.fitting_count(i) > 0}

        for i in range(COLUMNS):
            x = column_x(i)
            # подсветка под колонкой: курсор, возможные места для хода, вспышка после хода
            rect = pygame.Rect(x - 6, TABLE_TOP - 8, CARD_W + 12, self.column_bottom(i) - TABLE_TOP + 16)
            if i in drop_targets:
                pygame.draw.rect(self.screen, DROP_COLOR, rect, 3, border_radius=12)
            if i in self.flash:
                t0, color = self.flash[i]
                k = 1 - (self.now - t0) / 0.5
                glow = pygame.Surface(rect.size, pygame.SRCALPHA)
                glow.fill((*color, int(90 * k)))
                self.screen.blit(glow, rect.topleft)

            if not self.columns[i]:
                pygame.draw.rect(self.screen, (255, 255, 255), (x, TABLE_TOP, CARD_W, CARD_H), 2, border_radius=8)

            held_start = len(self.columns[i]) - self.held_count if i == self.held_from else None
            for j, (card, y) in enumerate(zip(self.columns[i], self.card_positions(i))):
                lift = -10 if held_start is not None and j >= held_start else 0
                self._draw_card(card, x, y + lift)
                if lift:
                    pygame.draw.rect(self.screen, HELD_COLOR, (x - 2, y + lift - 2, CARD_W + 4, CARD_H + 4),
                                     3, border_radius=9)

        # призрак: куда лягут карты, если моргнуть сейчас
        n = self.fitting_count(self.selected)
        if n:
            cards = self.columns[self.held_from][-n:]
            ys = self.card_positions(self.selected)
            y = (ys[-1] + GAP_UP) if ys else TABLE_TOP
            gap = self.column_gaps(self.selected)[1]
            for k, card in enumerate(cards):
                ghost = self._face(card).copy()
                ghost.set_alpha(140)
                self.screen.blit(ghost, (column_x(self.selected), y + k * gap))

        # курсор выбранной колонки
        x = column_x(self.selected)
        rect = pygame.Rect(x - 8, TABLE_TOP - 10, CARD_W + 16, self.column_bottom(self.selected) - TABLE_TOP + 20)
        pygame.draw.rect(self.screen, CURSOR_COLOR, rect, 4, border_radius=14)
        cx = x + CARD_W // 2
        pygame.draw.polygon(self.screen, CURSOR_COLOR, [(cx, TABLE_TOP - 14), (cx - 12, TABLE_TOP - 30),
                                                        (cx + 12, TABLE_TOP - 30)])

        if self.message:
            txt = self.font.render(self.message, True, (255, 230, 120))
            box = txt.get_rect(center=(self.width // 2, self.height - 120)).inflate(28, 14)
            pygame.draw.rect(self.screen, (0, 0, 0), box, border_radius=10)
            self.screen.blit(txt, txt.get_rect(center=box.center))

        if self.won:
            overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            self.screen.blit(overlay, (0, 0))
            for text, font, dy in (("ПОБЕДА!", self.font_big, -50),
                                   (f"Очки: {self.score}   Ходов: {self.moves}", self.font, 20),
                                   ("Закройте глаза на секунду или нажмите N — новая партия", self.font_small, 60)):
                txt = font.render(text, True, TEXT_COLOR)
                self.screen.blit(txt, txt.get_rect(center=(self.width // 2, self.height // 2 + dy)))

    def _draw_top_bar(self):
        # дом: 8 мест под собранные масти
        for i in range(8):
            rect = pygame.Rect(LEFT + i * 44, 22, 38, 54)
            if i < len(self.completed):
                suit = self.completed[i]
                pygame.draw.rect(self.screen, (252, 252, 248), rect, border_radius=5)
                color = (200, 30, 40) if suit in RED_SUITS else (25, 25, 30)
                txt = self.card_font.render("K" + SUIT_SYMBOLS[suit], True, color)
                self.screen.blit(txt, txt.get_rect(center=rect.center))
            else:
                pygame.draw.rect(self.screen, (170, 215, 185), rect, 1, border_radius=5)

        suits = {1: "1 масть", 2: "2 масти", 4: "4 масти"}[self.suits]
        info = f"Очки: {self.score}    Ходов: {self.moves}    {suits}"
        txt = self.font.render(info, True, TEXT_COLOR)
        self.screen.blit(txt, txt.get_rect(midtop=(self.width // 2 + 40, 26)))

        # запас: столько рубашек, сколько осталось раздач
        small_back = pygame.transform.smoothscale(self.back, (38, 54))
        right = self.width - LEFT - 38
        for i in range(self.deals_left):
            self.screen.blit(small_back, (right - i * 14, 14))
        label = self.font_small.render(f"раздач: {self.deals_left}", True, MUTED_TEXT)
        self.screen.blit(label, label.get_rect(topright=(self.width - LEFT, 74)))

    def _draw_card(self, card, x, y):
        self.screen.blit(self._face(card) if card.face_up else self.back, (x, y))

    def _face(self, card):
        key = (card.rank, card.suit)
        if key not in self._face_cache:
            surf = pygame.Surface((CARD_W, CARD_H), pygame.SRCALPHA)
            pygame.draw.rect(surf, (252, 252, 248), (0, 0, CARD_W, CARD_H), border_radius=8)
            pygame.draw.rect(surf, (120, 120, 120), (0, 0, CARD_W, CARD_H), 1, border_radius=8)
            color = (200, 30, 40) if card.suit in RED_SUITS else (25, 25, 30)
            label = RANK_NAMES.get(card.rank, str(card.rank)) + SUIT_SYMBOLS[card.suit]
            surf.blit(self.card_font.render(label, True, color), (6, 3))
            corner = self.card_font.render(label, True, color)
            surf.blit(corner, corner.get_rect(topright=(CARD_W - 6, 3)))
            big = self.card_suit_big.render(SUIT_SYMBOLS[card.suit], True, color)
            surf.blit(big, big.get_rect(center=(CARD_W // 2, CARD_H // 2 + 12)))
            self._face_cache[key] = surf
        return self._face_cache[key]

    def _render_back(self):
        surf = pygame.Surface((CARD_W, CARD_H), pygame.SRCALPHA)
        pygame.draw.rect(surf, (35, 70, 150), (0, 0, CARD_W, CARD_H), border_radius=8)
        pygame.draw.rect(surf, (240, 240, 240), (0, 0, CARD_W, CARD_H), 2, border_radius=8)
        inner = pygame.Rect(7, 7, CARD_W - 14, CARD_H - 14)
        pygame.draw.rect(surf, (60, 100, 185), inner, border_radius=5)
        surf.set_clip(inner)
        for k in range(-CARD_H, CARD_W, 10):
            pygame.draw.line(surf, (45, 82, 165), (inner.left + k, inner.top),
                             (inner.left + k + inner.height, inner.bottom), 2)
        surf.set_clip(None)
        pygame.draw.rect(surf, (230, 230, 240), inner, 1, border_radius=5)
        return surf

    def _render_background(self):
        surf = pygame.Surface((self.width, self.height))
        for y in range(self.height):
            k = y / self.height
            color = [int(a + (b - a) * k) for a, b in zip(FELT_TOP, FELT_BOTTOM)]
            pygame.draw.line(surf, color, (0, y), (self.width, y))
        return surf
