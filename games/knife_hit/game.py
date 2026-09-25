"""
Игра в духе Knife Hit на Pygame.

В центре крутится бревно, снизу по одному бросаются ножи. Нож втыкается
в бревно и дальше крутится вместе с ним. Если брошенный нож попадает
в уже торчащий нож — проигрыш. Все ножи уровня воткнуты — бревно
раскалывается, начинается следующий уровень.

Каждый уровень сложнее предыдущего: больше ножей, быстрее и хитрее
вращение (разгоны, остановки, смена направления), больше заранее
воткнутых ножей-препятствий. Каждый 5-й уровень — «босс».
Яблоки на бревне — бонус: нож, воткнутый в яблоко, приносит +1 яблоко.

Игра ничего не знает про камеру: бросок — вызов throw().
"""

import math
import random

import pygame

# ---------- геометрия ----------

LOG_R = 105              # радиус бревна, px
KNIFE_LEN = 112          # длина ножа от острия до конца рукояти
BLADE_LEN = 62
KNIFE_W = 12
EMBED = 20               # насколько нож входит в бревно
KNIFE_OUT = KNIFE_LEN - EMBED    # сколько торчит наружу
HIT_ANGLE = 7.0          # ножи ближе этого угла (градусы) сталкиваются
APPLE_ANGLE = 12.0       # попадание в яблоко
KNIFE_SPEED = 2400       # px/с

# ---------- тайминги ----------

BREAK_TIME = 1.4         # анимация раскола бревна перед следующим уровнем
FAIL_TIME = 0.9          # отскок ножа перед экраном проигрыша
RESTART_DELAY = 1.0      # защита от случайного моргания сразу после проигрыша
TITLE_TIME = 1.2

# ---------- цвета ----------

BG_TOP = (44, 38, 64)
BG_BOTTOM = (18, 16, 28)
TEXT_COLOR = (240, 236, 228)
MUTED_TEXT = (150, 146, 170)
WOOD = (176, 118, 64)
WOOD_DARK = (128, 80, 40)
BARK = (92, 56, 30)
BOSS_WOOD = (130, 132, 150)
BOSS_DARK = (86, 88, 104)
BOSS_BARK = (58, 58, 70)
BLADE = (214, 220, 230)
BLADE_EDGE = (150, 158, 172)
GUARD = (70, 70, 80)
HANDLE = (60, 36, 24)
APPLE = (220, 40, 50)

PLAY, BREAK, FAIL, GAME_OVER = "play", "break", "fail", "game_over"


def angle_diff(a, b):
    """Минимальная разница двух углов в градусах (0..180)."""
    return abs((a - b + 180) % 360 - 180)


def level_config(n):
    """Параметры уровня n (с 1). Чем больше n, тем сложнее."""
    boss = n % 5 == 0
    knives = min(5 + n // 2, 11) + (2 if boss else 0)
    if boss:
        obstacles = min(2 + n // 5, 6)
        pattern = "chaos"
    else:
        obstacles = min((n - 1) // 2, 5)
        # первые уровни — ровное вращение, дальше по кругу всё более хитрые режимы
        order = ["const", "const", "wave", "reverse", "stopgo", "wave", "reverse", "stopgo"]
        pattern = order[(n - 1) % len(order)]
    speed = min(80 + 14 * n, 300)
    direction = 1 if n % 2 else -1
    apples = 1 if n == 1 else random.choice([0, 1, 1, 2])
    return {"boss": boss, "knives": knives, "obstacles": obstacles, "pattern": pattern,
            "speed": speed, "direction": direction, "apples": apples}


def angular_speed(pattern, base, t):
    """Скорость вращения бревна (градусы/с) в момент t от начала уровня."""
    if pattern == "const":
        return base
    if pattern == "wave":          # то разгоняется, то почти замирает
        return base * (0.55 + 0.65 * math.sin(1.3 * t))
    if pattern == "reverse":       # держит скорость, тормозит и крутится обратно
        return base * 1.3 * max(-1.0, min(1.0, 2.5 * math.sin(2 * math.pi * t / 3.2)))
    if pattern == "stopgo":        # рывки с остановками
        return base * 1.8 * max(0.0, math.sin(2.4 * t)) ** 2
    # chaos (босс): сумма двух волн — непредсказуемые разгоны и развороты
    return base * (0.9 * math.sin(1.1 * t) + 0.8 * math.sin(2.7 * t + 1.0) + 0.3)


class KnifeHitGame:
    def __init__(self, screen, width, height, reserved=None):
        """reserved — занятая область окна (окно камеры): столбик ножей рисуется над ней."""
        self.screen = screen
        self.width = width
        self.height = height
        self.icons_bottom = reserved.top - 24 if reserved else height - 60
        self.center = (width // 2, 250)
        self.start_tip_y = height - 180      # где ждёт следующий нож (острие)

        self.font_small = pygame.font.SysFont("arial", 17)
        self.font = pygame.font.SysFont("arial", 24, bold=True)
        self.font_big = pygame.font.SysFont("arial", 46, bold=True)

        self.background = self._render_background()
        self.log_surfaces = {False: self._render_log(WOOD, WOOD_DARK, BARK),
                             True: self._render_log(BOSS_WOOD, BOSS_DARK, BOSS_BARK)}
        self.best_level = 0
        self.best_score = 0
        self.reset()

    def reset(self):
        self.now = 0.0
        self.score = 0
        self.apples_total = 0
        self.level = 0
        self.over_since = 0.0
        self._start_level(1)

    def _start_level(self, n):
        self.level = n
        self.cfg = level_config(n)
        self.phase = PLAY
        self.level_t = 0.0
        self.rotation = random.uniform(0, 360)
        self.knives_left = self.cfg["knives"]
        self.flying = None           # острие летящего ножа: y
        self.hit_time = -1.0
        self.title_until = self.now + TITLE_TIME
        self.debris = []             # обломки бревна и ножи, разлетающиеся при расколе
        self.bounce = None

        # заранее воткнутые ножи и яблоки — локальные углы на бревне, разнесённые друг от друга
        taken = []

        def free_angle(min_gap):
            for _ in range(200):
                a = random.uniform(0, 360)
                if all(angle_diff(a, b) >= min_gap for b in taken):
                    taken.append(a)
                    return a
            return None

        self.stuck = [a for a in (free_angle(30) for _ in range(self.cfg["obstacles"])) if a is not None]
        self.apples = [a for a in (free_angle(25) for _ in range(self.cfg["apples"])) if a is not None]

    # ---------- ввод ----------

    def throw(self):
        if self.phase == GAME_OVER:
            if self.now - self.over_since >= RESTART_DELAY:
                self.reset()
            return
        if self.phase != PLAY or self.flying is not None or self.knives_left <= 0:
            return
        self.flying = float(self.start_tip_y)
        self.knives_left -= 1

    # ---------- логика ----------

    def _impact_angle(self):
        """Локальный угол бревна, который сейчас внизу — туда входит летящий нож."""
        return (90 - self.rotation) % 360

    def update(self, dt):
        self.now += dt
        if self.phase in (PLAY, FAIL, GAME_OVER):
            self.level_t += dt
            self.rotation += self.cfg["direction"] * angular_speed(
                self.cfg["pattern"], self.cfg["speed"], self.level_t) * dt

        if self.phase == PLAY and self.flying is not None:
            self.flying -= KNIFE_SPEED * dt
            cy = self.center[1]
            local = self._impact_angle()
            if self.flying <= cy + LOG_R + KNIFE_OUT:
                # летящий нож в зоне торчащих рукоятей — проверяем столкновение
                if any(angle_diff(local, a) < HIT_ANGLE for a in self.stuck):
                    self._fail(max(self.flying, cy + LOG_R - EMBED))
            if self.phase == PLAY and self.flying <= cy + LOG_R - EMBED:
                self._stick(local)

        elif self.phase == BREAK and self.now >= self.phase_until:
            self._start_level(self.level + 1)
        elif self.phase == FAIL and self.now >= self.phase_until:
            self.phase = GAME_OVER
            self.over_since = self.now

        self._update_debris(dt)

    def _stick(self, local):
        self.flying = None
        self.stuck.append(local)
        self.score += 1
        self.hit_time = self.now
        for a in [a for a in self.apples if angle_diff(local, a) < APPLE_ANGLE]:
            self.apples.remove(a)
            self.apples_total += 1
            self._burst_apple(a)
        if self.knives_left == 0:
            self._break_log()

    def _fail(self, tip_y):
        self.flying = None
        self.phase = FAIL
        self.phase_until = self.now + FAIL_TIME
        self.bounce = {"x": self.center[0], "y": tip_y, "vx": random.choice([-1, 1]) * 220,
                       "vy": 700, "angle": 90, "spin": random.choice([-1, 1]) * 900}
        self.best_level = max(self.best_level, self.level)
        self.best_score = max(self.best_score, self.score)

    def _break_log(self):
        self.phase = BREAK
        self.phase_until = self.now + BREAK_TIME
        cx, cy = self.center
        wood, dark, _ = self._log_colors()
        pieces = 6
        for i in range(pieces):
            mid = self.rotation + (i + 0.5) * 360 / pieces
            a = math.radians(mid)
            self.debris.append({"kind": "wedge", "x": cx, "y": cy, "angle": mid,
                                "vx": math.cos(a) * random.uniform(150, 260),
                                "vy": math.sin(a) * random.uniform(150, 260) - 250,
                                "spin": random.uniform(-200, 200), "color": wood if i % 2 else dark})
        for local in self.stuck:
            world = local + self.rotation
            a = math.radians(world)
            r = LOG_R - EMBED
            self.debris.append({"kind": "knife", "x": cx + math.cos(a) * r, "y": cy + math.sin(a) * r,
                                "angle": world, "vx": math.cos(a) * random.uniform(250, 450),
                                "vy": math.sin(a) * random.uniform(250, 450) - 300,
                                "spin": random.uniform(-500, 500)})
        self.stuck = []
        self.apples = []

    def _burst_apple(self, local):
        cx, cy = self.center
        a = math.radians(local + self.rotation)
        x, y = cx + math.cos(a) * (LOG_R + 12), cy + math.sin(a) * (LOG_R + 12)
        for _ in range(10):
            b = random.uniform(0, 2 * math.pi)
            v = random.uniform(120, 300)
            self.debris.append({"kind": "juice", "x": x, "y": y, "angle": 0,
                                "vx": math.cos(b) * v, "vy": math.sin(b) * v - 150, "spin": 0})

    def _update_debris(self, dt):
        for d in self.debris:
            d["vy"] += 1400 * dt
            d["x"] += d["vx"] * dt
            d["y"] += d["vy"] * dt
            d["angle"] += d["spin"] * dt
        self.debris = [d for d in self.debris if d["y"] < self.height + 150]
        if self.bounce:
            b = self.bounce
            b["vy"] += 1400 * dt
            b["x"] += b["vx"] * dt
            b["y"] += b["vy"] * dt
            b["angle"] += b["spin"] * dt

    def _log_colors(self):
        if self.cfg["boss"]:
            return BOSS_WOOD, BOSS_DARK, BOSS_BARK
        return WOOD, WOOD_DARK, BARK

    # ---------- отрисовка ----------

    def draw(self):
        self.screen.blit(self.background, (0, 0))
        cx, cy = self.center
        shake = 0
        if self.phase == PLAY and self.now - self.hit_time < 0.1:
            shake = -8 * (1 - (self.now - self.hit_time) / 0.1)    # бревно «подпрыгивает» от удара
        if self.phase == FAIL and self.now < self.phase_until - FAIL_TIME + 0.25:
            shake = random.uniform(-5, 5)
        center = (cx, cy + shake)

        if self.phase in (PLAY, FAIL, GAME_OVER):
            # ножи рисуем до бревна, чтобы воткнутая часть лезвия пряталась под ним
            for local in self.stuck:
                world = local + self.rotation
                a = math.radians(world)
                r = LOG_R - EMBED
                self._draw_knife((center[0] + math.cos(a) * r, center[1] + math.sin(a) * r), world)
            for local in self.apples:
                self._draw_apple(center, local + self.rotation)
            log = pygame.transform.rotozoom(self.log_surfaces[self.cfg["boss"]], -self.rotation, 1)
            self.screen.blit(log, log.get_rect(center=center))
            if self.now - self.hit_time < 0.08:
                flash = pygame.Surface((LOG_R * 2, LOG_R * 2), pygame.SRCALPHA)
                pygame.draw.circle(flash, (255, 255, 255, 70), (LOG_R, LOG_R), LOG_R)
                self.screen.blit(flash, flash.get_rect(center=center))

        self._draw_debris()

        if self.flying is not None:
            self._draw_knife((cx, self.flying), 90)
        elif self.phase == PLAY and self.knives_left > 0:
            self._draw_knife((cx, self.start_tip_y), 90)
        if self.bounce:
            self._draw_knife((self.bounce["x"], self.bounce["y"]), self.bounce["angle"])

        self._draw_hud()
        if self.phase == GAME_OVER:
            self._draw_game_over()

    def _draw_knife(self, tip, angle):
        """Нож с остриём в точке tip; angle — направление от острия к рукояти (градусы, y вниз)."""
        a = math.radians(angle)
        d = (math.cos(a), math.sin(a))
        n = (-d[1], d[0])
        half = KNIFE_W / 2

        def p(k, w=0.0):
            return (tip[0] + d[0] * k + n[0] * w, tip[1] + d[1] * k + n[1] * w)

        blade = [p(0, 0), p(16, -half), p(BLADE_LEN, -half), p(BLADE_LEN, half), p(10, half)]
        pygame.draw.polygon(self.screen, BLADE, blade)
        pygame.draw.polygon(self.screen, BLADE_EDGE, [p(0, 0), p(16, -half), p(BLADE_LEN, -half),
                                                       p(BLADE_LEN, -half + 3), p(16, -half + 3)])
        pygame.draw.polygon(self.screen, GUARD, [p(BLADE_LEN, -half - 3), p(BLADE_LEN + 6, -half - 3),
                                                 p(BLADE_LEN + 6, half + 3), p(BLADE_LEN, half + 3)])
        handle = [p(BLADE_LEN + 6, -half + 1), p(KNIFE_LEN, -half + 1), p(KNIFE_LEN, half - 1),
                  p(BLADE_LEN + 6, half - 1)]
        pygame.draw.polygon(self.screen, HANDLE, handle)
        for k in (78, 90, 102):
            pygame.draw.line(self.screen, (90, 60, 40), p(k, -half + 1), p(k, half - 1), 2)

    def _draw_apple(self, center, world):
        a = math.radians(world)
        x, y = center[0] + math.cos(a) * (LOG_R + 12), center[1] + math.sin(a) * (LOG_R + 12)
        pygame.draw.circle(self.screen, APPLE, (x, y), 13)
        pygame.draw.circle(self.screen, (255, 140, 140), (x - 4, y - 4), 4)
        pygame.draw.line(self.screen, (90, 60, 30), (x, y - 12), (x + 2, y - 18), 3)
        pygame.draw.ellipse(self.screen, (60, 170, 70), (x + 2, y - 20, 10, 6))

    def _draw_debris(self):
        for d in self.debris:
            if d["kind"] == "knife":
                self._draw_knife((d["x"], d["y"]), d["angle"])
            elif d["kind"] == "juice":
                pygame.draw.circle(self.screen, APPLE, (d["x"], d["y"]), 4)
            else:
                a0 = math.radians(d["angle"] - 30)
                a1 = math.radians(d["angle"] + 30)
                pts = [(d["x"], d["y"])]
                for k in range(7):
                    a = a0 + (a1 - a0) * k / 6
                    pts.append((d["x"] + math.cos(a) * LOG_R, d["y"] + math.sin(a) * LOG_R))
                pygame.draw.polygon(self.screen, d["color"], pts)
                pygame.draw.polygon(self.screen, BARK, pts, 3)

    def _draw_hud(self):
        w = self.width
        # очки и яблоки
        self.screen.blit(self.font.render(str(self.score), True, TEXT_COLOR), (22, 18))
        apples = self.font.render(str(self.apples_total), True, TEXT_COLOR)
        self.screen.blit(apples, (w - 22 - apples.get_width(), 18))
        pygame.draw.circle(self.screen, APPLE, (w - 40 - apples.get_width(), 32), 10)

        # номер уровня и точки этапа (5 уровней в этапе, последний — босс)
        title = "БОСС" if self.cfg["boss"] else f"Уровень {self.level}"
        txt = self.font.render(title, True, (255, 110, 110) if self.cfg["boss"] else TEXT_COLOR)
        self.screen.blit(txt, txt.get_rect(midtop=(w // 2, 16)))
        first = (self.level - 1) // 5 * 5 + 1
        for i in range(5):
            n = first + i
            x = w // 2 + (i - 2) * 26
            color = (255, 90, 90) if n % 5 == 0 else (240, 200, 90)
            if n < self.level:
                pygame.draw.circle(self.screen, color, (x, 58), 7)
            elif n == self.level:
                pygame.draw.circle(self.screen, color, (x, 58), 9, 3)
            else:
                pygame.draw.circle(self.screen, (90, 86, 110), (x, 58), 6)

        # оставшиеся ножи — столбик иконок слева снизу
        total = self.cfg["knives"]
        for i in range(total):
            y = self.icons_bottom - i * 26
            color = (230, 230, 240) if i < self.knives_left else (70, 66, 90)
            pygame.draw.polygon(self.screen, color, [(22, y), (30, y - 5), (54, y - 5), (54, y + 5), (26, y + 5)])
            pygame.draw.rect(self.screen, color, (56, y - 3, 16, 7))

        if self.now < self.title_until and self.phase == PLAY:
            k = (self.title_until - self.now) / TITLE_TIME
            big = self.font_big.render("БОСС!" if self.cfg["boss"] else f"Уровень {self.level}", True, TEXT_COLOR)
            big.set_alpha(int(255 * min(1.0, k * 2)))
            self.screen.blit(big, big.get_rect(center=(w // 2, self.center[1] + LOG_R + 150)))

    def _draw_game_over(self):
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))
        cy = self.height // 2 - 60
        lines = [
            ("ПРОИГРЫШ", self.font_big, (255, 110, 110)),
            (f"Уровень: {self.level}   Ножей: {self.score}", self.font, TEXT_COLOR),
            (f"Рекорд: уровень {self.best_level}, ножей {self.best_score}", self.font, TEXT_COLOR),
            ("Моргните или нажмите R, чтобы начать заново", self.font_small, MUTED_TEXT),
        ]
        for text, font, color in lines:
            txt = font.render(text, True, color)
            self.screen.blit(txt, txt.get_rect(center=(self.width // 2, cy)))
            cy += 56

    def _render_background(self):
        surf = pygame.Surface((self.width, self.height))
        for y in range(self.height):
            k = y / self.height
            color = [int(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)]
            pygame.draw.line(surf, color, (0, y), (self.width, y))
        return surf

    def _render_log(self, wood, dark, bark):
        """Срез бревна с годовыми кольцами и трещинами — по ним видно вращение."""
        rng = random.Random(7)
        size = LOG_R * 2 + 4
        c = size // 2
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(surf, bark, (c, c), LOG_R)
        pygame.draw.circle(surf, wood, (c, c), LOG_R - 9)
        for r in range(14, LOG_R - 12, 13):
            pygame.draw.circle(surf, dark, (c + rng.randint(-2, 2), c + rng.randint(-2, 2)), r, 2)
        for _ in range(4):
            a = rng.uniform(0, 2 * math.pi)
            r0, r1 = rng.uniform(20, 45), rng.uniform(70, LOG_R - 12)
            pygame.draw.line(surf, dark, (c + math.cos(a) * r0, c + math.sin(a) * r0),
                             (c + math.cos(a) * r1, c + math.sin(a) * r1), 3)
        pygame.draw.circle(surf, dark, (c, c), 6)
        return surf
