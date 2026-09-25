"""
Игра «Дартс» на Pygame.

Как и BlockBlastGame, игра ничего не знает про камеру или мышь: она получает
события on_press / on_drag / on_release с координатами (x, y) в пикселях окна.
Отличие одно — в on_release дополнительно передаётся ThrowMotion: скорость
руки в момент разжатия пальцев. Из неё считаются сила и направление броска.

Ход одного дротика:
  1. ПРИЦЕЛ — щипок + движение руки двигают перекрестие по мишени,
              разжатие пальцев фиксирует точку (x, y).
  2. БРОСОК — снова щипок («взяли дротик»), резкое движение руки вперёд/вверх
              и разжатие пальцев в конце движения — дротик летит в прицел.
              Слабый бросок уходит ниже прицела, слишком сильный — выше,
              боковое движение руки уводит дротик в сторону.
"""

import math
import random
from dataclasses import dataclass

import pygame

# ---------- геометрия ----------

BOARD_AREA = 760                 # левая квадратная часть окна отведена под мишень
BOARD_CENTER = (BOARD_AREA // 2, BOARD_AREA // 2)
R = 265                          # внешний радиус кольца удвоения, px

# Радиусы колец относительно R — по стандартным размерам мишени (мм / 170)
R_BULL = 6.35 / 170
R_OUTER_BULL = 15.9 / 170
R_TRIPLE_IN = 99 / 170
R_TRIPLE_OUT = 107 / 170
R_DOUBLE_IN = 162 / 170
R_NUMBERS = 1.1
R_FRAME = 1.2

# Номера секторов по часовой стрелке, начиная с верхнего
SECTORS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

# ---------- правила ----------

ROUNDS = 5
DARTS_PER_ROUND = 3

# ---------- физика броска (подбираются под свою камеру и манеру броска) ----------

FWD_WEIGHT = 0.6        # вклад движения «к камере» (рост руки в кадре, 1/с) в силу броска
MIN_POWER = 0.4         # слабее — считаем, что броска не было, дротик остаётся в руке
IDEAL_POWER = 1.5       # сила, при которой дротик летит точно в прицел по вертикали
POWER_TOLERANCE = 0.25  # допустимое отклонение силы (доля от идеала) без промаха
VERT_GAIN = 150         # px смещения вниз/вверх на единицу «лишнего» отклонения силы
SIDE_TOLERANCE = 0.1    # допустимая доля бокового движения руки
SIDE_GAIN = 120         # px смещения вбок на единицу «лишнего» бокового движения
SCATTER = 6             # px, случайный разброс — рука никогда не бывает идеальной

# ---------- анимация / тайминги ----------

FLIGHT_TIME = 0.35
ROUND_PAUSE = 2.2
REAIM_HOLD = 1.5        # столько секунд держать щипок неподвижно, чтобы перенацелиться
REAIM_MOVE = 30         # px — движение больше этого сбрасывает таймер перенацеливания
POPUP_TIME = 1.2

# ---------- цвета ----------

BG_COLOR = (24, 27, 34)
WALL_COLOR = (38, 44, 56)
PANEL_COLOR = (32, 36, 46)
TEXT_COLOR = (230, 232, 238)
MUTED_TEXT = (150, 156, 170)
FRAME_COLOR = (20, 20, 22)
SINGLE_DARK = (30, 30, 30)
SINGLE_LIGHT = (236, 222, 186)
RING_RED = (208, 42, 48)
RING_GREEN = (22, 138, 70)
WIRE = (190, 190, 196)
DART_COLORS = [(235, 72, 72), (70, 160, 235), (245, 200, 60)]

AIM, THROW, FLIGHT, ROUND_END, GAME_OVER = "aim", "throw", "flight", "round_end", "game_over"


@dataclass
class ThrowMotion:
    """Скорость руки в момент броска. x, y — доли кадра в секунду, y растёт вниз.
    v_fwd — относительный рост руки в кадре в секунду (> 0 — рука движется к камере)."""
    vx: float = 0.0
    vy: float = 0.0
    v_fwd: float = 0.0

    @property
    def power(self):
        return max(0.0, -self.vy) + FWD_WEIGHT * max(0.0, self.v_fwd)


def _soft(value, tolerance):
    """Мёртвая зона: отклонения меньше tolerance не влияют, остальное — линейно."""
    return math.copysign(max(0.0, abs(value) - tolerance), value)


def score_at(x, y):
    """Очки за попадание в точку (x, y) окна: (очки, подпись)."""
    dx, dy = x - BOARD_CENTER[0], y - BOARD_CENTER[1]
    r = math.hypot(dx, dy) / R
    if r <= R_BULL:
        return 50, "BULL"
    if r <= R_OUTER_BULL:
        return 25, "25"
    if r > 1.0:
        return 0, "мимо"
    angle = math.degrees(math.atan2(-dy, dx))           # 0° — вправо, против часовой
    base = SECTORS[int(((90 - angle + 9) % 360) // 18)]
    if R_TRIPLE_IN <= r <= R_TRIPLE_OUT:
        return base * 3, f"T{base}"
    if r >= R_DOUBLE_IN:
        return base * 2, f"D{base}"
    return base, str(base)


class DartsGame:
    def __init__(self, screen, width, height):
        self.screen = screen
        self.width = width
        self.height = height

        self.font_small = pygame.font.SysFont("arial", 17)
        self.font = pygame.font.SysFont("arial", 22, bold=True)
        self.font_big = pygame.font.SysFont("arial", 44, bold=True)
        self.font_popup = pygame.font.SysFont("arial", 30, bold=True)

        self.board = self._render_board()
        self.best = 0
        self.reset()

    def reset(self):
        self.phase = AIM
        self.now = 0.0
        self.round = 1
        self.total = 0
        self.round_throws = []      # [(подпись, очки)] текущего раунда
        self.darts = []             # воткнутые дротики: [(x, y, цвет)]

        self.aim = None             # зафиксированная (или двигаемая) точка прицела
        self.aiming = False         # щипок в фазе прицеливания
        self.holding = False        # дротик «в руке» в фазе броска
        self.hold_anchor = None
        self.hold_since = 0.0

        self.flight = None
        self.phase_until = 0.0
        self.last_power = None
        self.popups = []            # всплывающие надписи и круги попаданий
        self.message = None
        self.message_until = 0.0

    # ---------- ввод (общий для мыши и жестов) ----------

    def on_press(self, pos):
        if self.phase == GAME_OVER:
            self.reset()
        elif self.phase == AIM:
            self.aiming = True
            self.aim = self._clamp_to_board(pos)
        elif self.phase == THROW:
            self.holding = True
            self.hold_anchor = pos
            self.hold_since = self.now

    def on_drag(self, pos):
        if self.phase == AIM and self.aiming:
            self.aim = self._clamp_to_board(pos)
        elif self.phase == THROW and self.holding:
            if math.dist(pos, self.hold_anchor) > REAIM_MOVE:
                self.hold_anchor = pos
                self.hold_since = self.now

    def on_release(self, pos, motion):
        if self.phase == AIM and self.aiming:
            self.aiming = False
            self.aim = self._clamp_to_board(pos)
            self.phase = THROW
        elif self.phase == THROW and self.holding:
            self.holding = False
            power = motion.power
            self.last_power = power
            if power < MIN_POWER:
                self._say("Слишком слабо — дротик остался в руке. Резче!")
                return
            self._launch(motion)

    def reaim(self):
        """Вернуться к шагу прицеливания (клавиша A)."""
        if self.phase == THROW:
            self.holding = False
            self.phase = AIM

    def _clamp_to_board(self, pos):
        return (min(max(pos[0], 0), BOARD_AREA - 1), min(max(pos[1], 0), self.height - 1))

    # ---------- логика ----------

    def _launch(self, motion):
        power = motion.power
        # по вертикали: недобор силы — ниже прицела, перебор — выше
        err = max(-1.0, min(1.5, (power - IDEAL_POWER) / IDEAL_POWER))
        dy = -VERT_GAIN * _soft(err, POWER_TOLERANCE)
        # по горизонтали: доля бокового движения руки относительно силы броска
        side = max(-1.5, min(1.5, motion.vx / power))
        dx = SIDE_GAIN * _soft(side, SIDE_TOLERANCE)

        land = (self.aim[0] + dx + random.gauss(0, SCATTER),
                self.aim[1] + dy + random.gauss(0, SCATTER))
        land = (min(max(land[0], 8), BOARD_AREA - 8), min(max(land[1], 8), self.height - 8))

        start = (BOARD_AREA * 0.62, self.height + 90)
        self.flight = {"start": start, "end": land, "t0": self.now,
                       "color": DART_COLORS[len(self.round_throws) % len(DART_COLORS)]}
        self.phase = FLIGHT

    def _land(self):
        x, y = self.flight["end"]
        points, label = score_at(x, y)
        self.darts.append((x, y, self.flight["color"]))
        self.round_throws.append((label, points))
        self.total += points
        self.flight = None

        self.popups.append({"kind": "ring", "pos": (x, y), "t0": self.now})
        text = f"{label} +{points}" if points else "Мимо!"
        color = (255, 220, 90) if points >= 40 else (TEXT_COLOR if points else (240, 110, 110))
        self.popups.append({"kind": "text", "pos": (x, y - 28), "t0": self.now,
                            "text": text, "color": color})

        if len(self.round_throws) >= DARTS_PER_ROUND:
            self.phase = ROUND_END
            self.phase_until = self.now + ROUND_PAUSE
        else:
            self.phase = AIM

    def _next_round(self):
        if self.round >= ROUNDS:
            self.best = max(self.best, self.total)
            self.phase = GAME_OVER
            return
        self.round += 1
        self.round_throws = []
        self.darts = []
        self.aim = None
        self.phase = AIM

    def _say(self, text, seconds=2.0):
        self.message = text
        self.message_until = self.now + seconds

    def update(self, dt):
        self.now += dt

        if self.phase == THROW and self.holding and self.now - self.hold_since > REAIM_HOLD:
            # долго держим щипок без движения — берём перекрестие заново
            self.holding = False
            self.aiming = True
            self.aim = self._clamp_to_board(self.hold_anchor)
            self.phase = AIM
            self._say("Перенацеливание: ведите руку и разожмите пальцы")

        if self.phase == FLIGHT and self.now - self.flight["t0"] >= FLIGHT_TIME:
            self._land()
        elif self.phase == ROUND_END and self.now >= self.phase_until:
            self._next_round()

        self.popups = [p for p in self.popups if self.now - p["t0"] < POPUP_TIME]
        if self.message and self.now > self.message_until:
            self.message = None

    # ---------- отрисовка ----------

    def draw(self, cursor_pos, pinching, live_motion=None):
        self.screen.fill(BG_COLOR)
        pygame.draw.rect(self.screen, WALL_COLOR, (0, 0, BOARD_AREA, self.height))
        pygame.draw.circle(self.screen, (18, 20, 26), (BOARD_CENTER[0] + 6, BOARD_CENTER[1] + 10),
                           int(R * R_FRAME) + 4)
        self.screen.blit(self.board, self.board.get_rect(center=BOARD_CENTER))

        for x, y, color in self.darts:
            self._draw_dart((x, y), 1.25, color)
        self._draw_crosshair()
        self._draw_popups()
        self._draw_flight()

        self._draw_panel(live_motion)
        if self.phase == ROUND_END:
            self._draw_round_end()
        elif self.phase == GAME_OVER:
            self._draw_game_over()

        if self.message:
            txt = self.font.render(self.message, True, (255, 200, 90))
            box = txt.get_rect(midtop=(BOARD_AREA // 2, 14)).inflate(24, 12)
            pygame.draw.rect(self.screen, (0, 0, 0), box, border_radius=8)
            self.screen.blit(txt, txt.get_rect(center=box.center))

        # курсор руки: красный — пальцы сжаты
        color = (255, 60, 60) if pinching else (240, 240, 240)
        pygame.draw.circle(self.screen, color, cursor_pos, 10, 3)
        if pinching:
            pygame.draw.circle(self.screen, color, cursor_pos, 4)

    def _render_board(self):
        """Рисует мишень один раз в отдельную поверхность (с 2x-суперсэмплингом для гладких краёв)."""
        ss = 2
        half = int(R * R_FRAME) + 2
        size = half * 2 * ss
        c = size / 2
        rr = R * ss
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(surf, FRAME_COLOR, (c, c), R * R_FRAME * ss)

        def polar(r, deg):
            a = math.radians(deg)
            return c + math.cos(a) * r * rr, c - math.sin(a) * r * rr

        def segment(r_in, r_out, a0, a1, color):
            steps = 8
            outer = [polar(r_out, a0 + (a1 - a0) * k / steps) for k in range(steps + 1)]
            inner = [polar(r_in, a1 - (a1 - a0) * k / steps) for k in range(steps + 1)]
            pygame.draw.polygon(surf, color, outer + inner)

        for i in range(len(SECTORS)):
            mid = 90 - i * 18
            a0, a1 = mid - 9, mid + 9
            single = SINGLE_DARK if i % 2 == 0 else SINGLE_LIGHT
            ring = RING_RED if i % 2 == 0 else RING_GREEN
            segment(R_OUTER_BULL, R_TRIPLE_IN, a0, a1, single)
            segment(R_TRIPLE_IN, R_TRIPLE_OUT, a0, a1, ring)
            segment(R_TRIPLE_OUT, R_DOUBLE_IN, a0, a1, single)
            segment(R_DOUBLE_IN, 1.0, a0, a1, ring)

        for i in range(len(SECTORS)):
            edge = 90 - i * 18 - 9
            pygame.draw.line(surf, WIRE, polar(R_OUTER_BULL, edge), polar(1.0, edge), ss)
        pygame.draw.circle(surf, RING_GREEN, (c, c), R_OUTER_BULL * rr)
        pygame.draw.circle(surf, RING_RED, (c, c), R_BULL * rr)
        for r in (R_BULL, R_OUTER_BULL, R_TRIPLE_IN, R_TRIPLE_OUT, R_DOUBLE_IN, 1.0):
            pygame.draw.circle(surf, WIRE, (c, c), r * rr, ss)

        font = pygame.font.SysFont("arial", 26 * ss, bold=True)
        for i, number in enumerate(SECTORS):
            txt = font.render(str(number), True, (235, 235, 235))
            surf.blit(txt, txt.get_rect(center=polar(R_NUMBERS, 90 - i * 18)))

        return pygame.transform.smoothscale(surf, (half * 2, half * 2))

    def _draw_dart(self, tip, scale, color):
        """Дротик, воткнутый в точку tip; хвост смотрит вниз-вправо, к игроку."""
        d = (0.32, 0.95)
        n = (-d[1], d[0])

        def p(k, w=0.0):
            return (tip[0] + (d[0] * k + n[0] * w) * scale,
                    tip[1] + (d[1] * k + n[1] * w) * scale)

        w = max(1, round(scale))
        pygame.draw.line(self.screen, (205, 205, 215), p(0), p(8), 2 * w)                 # игла
        pygame.draw.polygon(self.screen, (95, 98, 110), [p(8, -2.2), p(22, -2.8), p(22, 2.8), p(8, 2.2)])
        pygame.draw.line(self.screen, (30, 30, 34), p(22), p(34), 2 * w)                  # хвостовик
        flight = [p(30), p(38, -8), p(47, -7), p(44), p(47, 7), p(38, 8)]                 # оперение
        pygame.draw.polygon(self.screen, color, flight)
        pygame.draw.polygon(self.screen, (20, 20, 20), flight, w)

    def _draw_crosshair(self):
        if self.aim is None or self.phase not in (AIM, THROW, FLIGHT):
            return
        x, y = int(self.aim[0]), int(self.aim[1])
        moving = self.phase == AIM and self.aiming
        if self.phase == AIM and not self.aiming:
            color = (150, 150, 150)       # старый прицел: пора выбрать новый
        elif moving:
            color = (255, 230, 80)
        else:
            color = (80, 255, 140)
        pygame.draw.circle(self.screen, color, (x, y), 16, 2)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            pygame.draw.line(self.screen, color, (x + dx * 8, y + dy * 8), (x + dx * 24, y + dy * 24), 2)
        pygame.draw.circle(self.screen, color, (x, y), 2)

    def _draw_flight(self):
        if not self.flight:
            return
        t = min(1.0, (self.now - self.flight["t0"]) / FLIGHT_TIME)
        ease = 1 - (1 - t) ** 2
        (sx, sy), (ex, ey) = self.flight["start"], self.flight["end"]
        x = sx + (ex - sx) * ease
        y = sy + (ey - sy) * ease - 90 * math.sin(math.pi * t)   # небольшая дуга
        self._draw_dart((x, y), 3.2 - 1.95 * ease, self.flight["color"])

    def _draw_popups(self):
        for p in self.popups:
            age = (self.now - p["t0"]) / POPUP_TIME
            if p["kind"] == "ring":
                if age < 0.3:
                    k = age / 0.3
                    pygame.draw.circle(self.screen, (255, 255, 255), p["pos"], int(4 + 22 * k), 2)
            else:
                txt = self.font_popup.render(p["text"], True, p["color"])
                txt.set_alpha(int(255 * (1 - age)))
                x, y = p["pos"]
                self.screen.blit(txt, txt.get_rect(center=(x, y - 30 * age)))

    def _draw_panel(self, live_motion):
        x0 = BOARD_AREA
        pygame.draw.rect(self.screen, PANEL_COLOR, (x0, 0, self.width - x0, self.height))
        x = x0 + 24
        y = 24

        self.screen.blit(self.font_big.render("ДАРТС", True, TEXT_COLOR), (x, y))
        y += 64
        info = [
            f"Раунд: {min(self.round, ROUNDS)} / {ROUNDS}",
            f"Дротик: {min(len(self.round_throws) + 1, DARTS_PER_ROUND)} / {DARTS_PER_ROUND}",
            f"Счёт: {self.total}",
            f"Рекорд: {self.best}",
        ]
        for line in info:
            self.screen.blit(self.font.render(line, True, TEXT_COLOR), (x, y))
            y += 32

        y += 8
        self.screen.blit(self.font_small.render("Этот раунд:", True, MUTED_TEXT), (x, y))
        y += 26
        for i in range(DARTS_PER_ROUND):
            pygame.draw.circle(self.screen, DART_COLORS[i], (x + 8, y + 11), 7)
            if i < len(self.round_throws):
                label, points = self.round_throws[i]
                text = f"{label}  —  {points}"
            else:
                text = "…"
            self.screen.blit(self.font.render(text, True, TEXT_COLOR), (x + 24, y))
            y += 30

        y += 16
        y = self._draw_power_meter(x, y, self.width - x0 - 48, live_motion)

        y += 22
        for line in self._wrap(self._hint(), self.font_small, self.width - x0 - 48):
            self.screen.blit(self.font_small.render(line, True, TEXT_COLOR), (x, y))
            y += 22

    def _draw_power_meter(self, x, y, w, live_motion):
        self.screen.blit(self.font_small.render("Сила броска:", True, MUTED_TEXT), (x, y))
        y += 26
        h = 20
        top = IDEAL_POWER * 2.2

        def px(power):
            return x + int(w * min(power, top) / top)

        pygame.draw.rect(self.screen, (55, 60, 74), (x, y, w, h), border_radius=5)
        pygame.draw.rect(self.screen, (80, 60, 60), (x, y, px(MIN_POWER) - x, h), border_radius=5)
        lo, hi = IDEAL_POWER * (1 - POWER_TOLERANCE), IDEAL_POWER * (1 + POWER_TOLERANCE)
        pygame.draw.rect(self.screen, (40, 120, 70), (px(lo), y, px(hi) - px(lo), h))

        if live_motion is not None and self.phase == THROW and self.holding:
            pygame.draw.rect(self.screen, (240, 200, 70), (x, y + 6, px(live_motion.power) - x, h - 12),
                             border_radius=3)
        if self.last_power is not None:
            mx = px(self.last_power)
            pygame.draw.polygon(self.screen, (255, 255, 255), [(mx, y + h + 2), (mx - 6, y + h + 12), (mx + 6, y + h + 12)])
        y += h + 16
        self.screen.blit(self.font_small.render("слабо", True, MUTED_TEXT), (x, y))
        strong = self.font_small.render("сильно", True, MUTED_TEXT)
        self.screen.blit(strong, (x + w - strong.get_width(), y))
        return y + 20

    def _hint(self):
        if self.phase == AIM:
            if self.aiming:
                return "Ведите руку к цели и разожмите пальцы, чтобы зафиксировать прицел."
            return ("Шаг 1 — прицел. Сожмите большой и указательный пальцы (щипок) и ведите руку: "
                    "перекрестие двигается по мишени. Разожмите пальцы — прицел зафиксирован.")
        if self.phase == THROW:
            if self.holding:
                return "Бросайте! Резко двиньте руку вперёд (к камере) и вверх и разожмите пальцы в конце движения."
            return ("Шаг 2 — бросок. Сожмите пальцы — дротик в руке. Сделайте резкое движение "
                    "вперёд/вверх и разожмите пальцы в конце, как при настоящем броске. "
                    f"Держите щипок неподвижно {REAIM_HOLD:.1f} с — перенацелиться.")
        if self.phase == GAME_OVER:
            return "Игра окончена. Сожмите пальцы или нажмите R, чтобы сыграть ещё."
        return ""

    def _wrap(self, text, font, width):
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

    def _overlay_box(self, lines):
        box = pygame.Rect(0, 0, 460, 60 + 50 * len(lines))
        box.center = BOARD_CENTER
        overlay = pygame.Surface(box.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        self.screen.blit(overlay, box.topleft)
        y = box.top + 30
        for text, font in lines:
            txt = font.render(text, True, (255, 255, 255))
            self.screen.blit(txt, txt.get_rect(midtop=(box.centerx, y)))
            y += 50

    def _draw_round_end(self):
        round_points = sum(p for _, p in self.round_throws)
        self._overlay_box([(f"Раунд {self.round}: +{round_points}", self.font_big),
                           (f"Всего: {self.total}", self.font)])

    def _draw_game_over(self):
        self._overlay_box([("ИГРА ОКОНЧЕНА", self.font_big),
                           (f"Итог: {self.total}   Рекорд: {self.best}", self.font),
                           ("Щипок или R — заново", self.font)])
