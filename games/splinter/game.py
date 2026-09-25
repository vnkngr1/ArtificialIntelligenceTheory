"""
Симулятор удаления заноз на Pygame.

Крупным планом — ладонь пациента, в коже сидят занозы: под кожей видна
тёмная часть, наружу торчит кончик. Щипок пальцами (пинцет) на торчащем
кончике — захватить, дальше тянуть руку вдоль занозы, наружу.

  - Тянуть надо ровно по оси занозы и плавно. Увод в сторону и рывки
    копят «натяжение»; когда оно доходит до предела — заноза ломается:
    торчащая часть отрывается, обломок остаётся в коже почти заподлицо,
    и ухватить его гораздо труднее.
  - Всё это больно: боль копится и от щипков кожи мимо занозы.
    Чем больнее, тем сильнее дрожит рука пациента (и мешает тянуть ровно).
    Боль 100% — пациент отдёргивает руку, уровень заново.
  - Разжатые пальцы отпускают занозу: вытянутая часть так и остаётся
    снаружи, можно перехватиться и тянуть дальше.

Виды заноз: деревянная, иголка кактуса (тонкая и хрупкая), стекло (очень
больно тянуть вкось), металлическая стружка. С каждым уровнем заноз
больше, они короче торчат и хрупче, а рука пациента дрожит сильнее.

Игра ничего не знает про камеру: on_press / on_drag / on_release(pos).
"""

import math
import random
from collections import deque

import pygame

# ---------- виды заноз ----------

TYPES = {
    "wood":   {"name": "деревянная", "width": 7, "length": (80, 115), "fragility": 1.0,
               "lat_tol": 16, "max_speed": 650, "pain": 1.0,
               "color": (126, 84, 44), "light": (176, 128, 76)},
    "cactus": {"name": "иголка кактуса", "width": 3, "length": (50, 70), "fragility": 1.6,
               "lat_tol": 10, "max_speed": 420, "pain": 0.8,
               "color": (205, 200, 150), "light": (245, 240, 205)},
    "glass":  {"name": "стекло", "width": 8, "length": (45, 70), "fragility": 0.6,
               "lat_tol": 11, "max_speed": 550, "pain": 1.9,
               "color": (120, 190, 215), "light": (215, 245, 255)},
    "metal":  {"name": "стружка", "width": 4, "length": (40, 60), "fragility": 0.4,
               "lat_tol": 13, "max_speed": 600, "pain": 1.3,
               "color": (120, 124, 132), "light": (215, 218, 225)},
}

# ---------- баланс ----------

MIN_EXPOSED = 4          # столько торчит обломок после поломки
PAIN_SKIN_PINCH = 7      # щипок кожи мимо занозы
PAIN_BREAK = 15
PAIN_DECAY = 4           # в секунду, пока пинцет ничего не тянет
STRESS_DECAY = 0.8       # в секунду, когда тянут правильно
SPEED_WINDOW = 0.06      # сек — по такому окну меряем скорость вытягивания
LENS_R = 85              # лупа вокруг пинцета
LENS_ZOOM = 2.0

# ---------- цвета ----------

BG = (28, 52, 58)
SKIN = (236, 192, 160)
SKIN_DARK = (214, 164, 132)
TEXT_COLOR = (240, 244, 245)
MUTED_TEXT = (170, 190, 195)

PLAY, LEVEL_DONE, FAILED = "play", "done", "failed"


def level_config(n):
    """Параметры уровня n (с 1)."""
    pool = ["wood"] if n <= 2 else ["wood", "cactus"] if n == 3 else \
        ["wood", "cactus", "glass"] if n == 4 else list(TYPES)
    return {
        "count": min(5, 1 + n // 2),
        "types": pool,
        "exposed": max(0.12, 0.45 - 0.04 * n),     # доля длины, которая торчит наружу
        "fragility": 1 + 0.08 * (n - 1),
        "tremor": min(4.0, 0.4 * (n - 1)),          # базовая дрожь руки пациента, px
        "guide": n <= 2,                            # подсказка-направление на первых уровнях
    }


class Splinter:
    def __init__(self, kind, entry, angle_deg, length, exposed_frac, fragility_mult):
        spec = TYPES[kind]
        self.kind = kind
        self.spec = spec
        self.entry = entry                       # точка входа в кожу (координаты кожи)
        a = math.radians(angle_deg)
        self.d = (math.cos(a), math.sin(a))      # направление «наружу» вдоль занозы
        self.length = length
        self.embedded = length * (1 - exposed_frac)
        self.fragility = spec["fragility"] * fragility_mult
        self.broken = False
        self.removed = False
        self.redness = 0.0                       # покраснение вокруг ранки

    @property
    def exposed(self):
        return self.length - self.embedded

    def point(self, s):
        """Точка на оси занозы: s > 0 — снаружи от точки входа, s < 0 — под кожей."""
        return self.entry[0] + self.d[0] * s, self.entry[1] + self.d[1] * s

    def distance_to_exposed(self, p):
        ex = self.exposed
        vx, vy = p[0] - self.entry[0], p[1] - self.entry[1]
        s = max(0.0, min(ex, vx * self.d[0] + vy * self.d[1]))
        q = self.point(s)
        return math.hypot(p[0] - q[0], p[1] - q[1])


class SplinterGame:
    def __init__(self, screen, width, height, reserved=None):
        """reserved — занятая область окна (окно камеры): туда не ставим занозы."""
        self.screen = screen
        self.width = width
        self.height = height
        self.reserved = reserved.inflate(60, 60) if reserved else None
        self.skin_rect = pygame.Rect(70, 96, width - 140, height - 150)

        self.font_small = pygame.font.SysFont("arial", 17)
        self.font = pygame.font.SysFont("arial", 23, bold=True)
        self.font_big = pygame.font.SysFont("arial", 48, bold=True)

        self.skin = self._render_skin()
        self.skin_mask = pygame.mask.from_surface(self.skin)
        self.lens_on = True
        self.best_level = 0
        self.total_score = 0
        self.level = 1
        self._start_level()

    # ---------- уровни ----------

    def _start_level(self):
        self.cfg = level_config(self.level)
        self.phase = PLAY
        self.now = 0.0
        self.phase_since = 0.0
        self.pain = 0.0
        self.level_score = 0
        self.max_pain = 0.0
        self.splinters = []
        self.debris = []           # выпавшие занозы и обломки
        self.popups = []
        self.message = None
        self.message_until = 0.0

        self.held = None           # заноза в пинцете
        self.grip0 = None          # где был пинцет в момент захвата (координаты кожи)
        self.emb0 = 0.0
        self.along = 0.0
        self.lat = 0.0
        self.along_speed = 0.0
        self.stress = 0.0
        self.pain_at_grab = 0.0
        self.pulled_out = False    # заноза целиком вытащена и висит в пинцете
        self.cursor = (self.width // 2, self.height // 2)
        self.pinching = False

        margin = 30
        tries = 0
        while len(self.splinters) < self.cfg["count"] and tries < 2000:
            tries += 1
            kind = random.choice(self.cfg["types"])
            length = random.uniform(*TYPES[kind]["length"])
            entry = (random.uniform(self.skin_rect.left + 90, self.skin_rect.right - 90),
                     random.uniform(self.skin_rect.top + 90, self.skin_rect.bottom - 70))
            sp = Splinter(kind, entry, random.uniform(0, 360), length,
                          self.cfg["exposed"] * random.uniform(0.85, 1.15), self.cfg["fragility"])
            ends = [sp.point(-sp.embedded - margin), sp.point(sp.exposed + margin)]
            if not all(self._on_skin(p) for p in ends):
                continue
            if self.reserved and any(self.reserved.collidepoint(p) for p in ends + [entry]):
                continue
            if any(math.dist(entry, o.entry) < 130 for o in self.splinters):
                continue
            self.splinters.append(sp)

    def next_level(self):
        self.level += 1
        self._start_level()

    def restart_level(self):
        self._start_level()

    # ---------- координаты ----------

    def tremor_offset(self):
        """Дрожь руки пациента: всё, что на коже, сдвигается на экране."""
        amp = self.cfg["tremor"] + self.pain / 100 * 6
        t = self.now
        return (amp * (math.sin(t * 11.3) * 0.6 + math.sin(t * 17.9 + 1.3) * 0.4),
                amp * (math.sin(t * 13.1 + 0.7) * 0.6 + math.sin(t * 7.7 + 2.1) * 0.4))

    def to_skin(self, pos):
        ox, oy = self.tremor_offset()
        return pos[0] - ox, pos[1] - oy

    def to_screen(self, p):
        ox, oy = self.tremor_offset()
        return p[0] + ox, p[1] + oy

    def _on_skin(self, p):
        x, y = int(p[0] - self.skin_rect.left), int(p[1] - self.skin_rect.top)
        return 0 <= x < self.skin_rect.width and 0 <= y < self.skin_rect.height and self.skin_mask.get_at((x, y))

    # ---------- ввод ----------

    def on_press(self, pos):
        self.cursor, self.pinching = pos, True
        if self.phase == LEVEL_DONE and self.now - self.phase_since > 1.0:
            self.next_level()
            return
        if self.phase == FAILED and self.now - self.phase_since > 1.0:
            self.restart_level()
            return
        if self.phase != PLAY:
            return
        p = self.to_skin(pos)
        best, best_d = None, None
        for sp in self.splinters:
            if sp.removed:
                continue
            reach = 11 + min(9.0, sp.exposed / 5)     # короткий кончик — ухватить труднее
            d = sp.distance_to_exposed(p)
            if d <= reach and (best_d is None or d < best_d):
                best, best_d = sp, d
        if best is None:
            if self._on_skin(p):
                self._hurt(PAIN_SKIN_PINCH, "Ай! Это кожа, а не заноза")
            return
        self.held = best
        self.grip0 = p
        self.emb0 = best.embedded
        self.along = self.lat = self.along_speed = 0.0
        self.along_history = deque([(self.now, 0.0)])
        self.stress = 0.0
        self.pain_at_grab = self.pain
        self.pulled_out = False

    def on_drag(self, pos):
        self.cursor = pos

    def on_release(self, pos):
        self.cursor, self.pinching = pos, False
        if self.held is None:
            return
        if self.pulled_out:
            self._drop(self.held, pos)
        self.held = None

    def on_hover(self, pos):
        self.cursor = pos

    def toggle_lens(self):
        self.lens_on = not self.lens_on

    # ---------- логика ----------

    def _hurt(self, amount, text=None):
        self.pain = min(100.0, self.pain + amount)
        if text:
            self._say(text)
        if self.pain >= 100 and self.phase == PLAY:
            self._fail()

    def _say(self, text, seconds=1.8):
        self.message = text
        self.message_until = self.now + seconds

    def update(self, dt):
        self.now += dt
        if self.phase == PLAY:
            if self.held is not None and not self.pulled_out:
                self._pull(dt)
            elif self.held is None:
                self.pain = max(0.0, self.pain - PAIN_DECAY * dt)
            self.max_pain = max(self.max_pain, self.pain)
        for sp in self.splinters:
            sp.redness = max(0.0, sp.redness - 0.1 * dt)
        for d in self.debris:
            d["vy"] += 900 * dt
            d["x"] += d["vx"] * dt
            d["y"] += d["vy"] * dt
            d["angle"] += d["spin"] * dt
        self.debris = [d for d in self.debris if d["y"] < self.height + 100]
        self.popups = [p for p in self.popups if self.now - p["t0"] < 1.2]
        if self.message and self.now > self.message_until:
            self.message = None

    def _pull(self, dt):
        sp = self.held
        p = self.to_skin(self.cursor)
        vx, vy = p[0] - self.grip0[0], p[1] - self.grip0[1]
        along = vx * sp.d[0] + vy * sp.d[1]
        self.lat = vx * -sp.d[1] + vy * sp.d[0]            # смещение поперёк занозы
        # скорость — по окну SPEED_WINDOW: камера даёт ~30 кадров/с, покадровая разница «рваная»
        self.along_history.append((self.now, along))
        while len(self.along_history) > 2 and self.now - self.along_history[1][0] >= SPEED_WINDOW:
            self.along_history.popleft()
        t0, a0 = self.along_history[0]
        self.along_speed = (along - a0) / max(self.now - t0, SPEED_WINDOW)
        self.along = along
        emb_before = sp.embedded
        new_emb = max(0.0, min(self.emb0, self.emb0 - along))

        spec = sp.spec
        bend = max(0.0, abs(self.lat) - spec["lat_tol"] * 0.5) / spec["lat_tol"]
        jerk = max(0.0, self.along_speed - spec["max_speed"]) / spec["max_speed"]
        rate = sp.fragility * (6.0 * bend ** 2 + 4.0 * jerk)
        if rate > 0:
            self.stress += rate * dt
        else:
            self.stress = max(0.0, self.stress - STRESS_DECAY * dt)
        if jerk >= 1.0:
            self.stress = max(self.stress, 1.0)    # рывок вдвое быстрее допустимого — сразу щелчок

        # больно: тянуть вкось, дёргать и вообще тянуть (немножко)
        pain = spec["pain"] * (14 * bend + 10 * jerk + 0.012 * max(0.0, self.along_speed))
        self._hurt(pain * dt)
        sp.redness = min(1.0, sp.redness + (bend + jerk) * dt)

        if self.stress >= 1.0 and emb_before > 10:
            self._break(sp)             # ломается там, где была в момент перегрузки
            return
        self.stress = min(self.stress, 0.99)   # почти вышла — дотянуть ещё можно
        sp.embedded = new_emb
        if sp.embedded <= 0:
            self._extracted(sp)

    def _extracted(self, sp):
        sp.removed = True
        self.pulled_out = True
        gained = self.pain - self.pain_at_grab
        points = max(20, int(150 - gained * 3)) + (0 if sp.broken else 50)
        self.level_score += points
        self.total_score += points
        self.popups.append({"text": f"Вытащили! +{points}", "pos": self.to_screen(sp.entry), "t0": self.now,
                            "color": (120, 240, 150)})
        if all(s.removed for s in self.splinters):
            self._level_done()

    def _break(self, sp):
        """Заноза ломается у самой кожи: торчащая часть улетает, обломок остаётся."""
        piece = sp.exposed
        grip = self.cursor
        self.debris.append({"kind": sp.kind, "len": piece, "x": grip[0], "y": grip[1],
                            "angle": math.degrees(math.atan2(sp.d[1], sp.d[0])),
                            "vx": random.uniform(-80, 80), "vy": -120, "spin": random.uniform(-300, 300)})
        sp.length = sp.embedded + MIN_EXPOSED
        sp.broken = True
        sp.redness = 1.0
        self.held = None
        self.stress = 0.0
        self._hurt(PAIN_BREAK, "Треск! Заноза сломалась — обломок остался в коже")

    def _drop(self, sp, pos):
        self.debris.append({"kind": sp.kind, "len": sp.length, "x": pos[0], "y": pos[1],
                            "angle": math.degrees(math.atan2(sp.d[1], sp.d[0])),
                            "vx": random.uniform(-60, 60), "vy": -60, "spin": random.uniform(-200, 200)})

    def _level_done(self):
        self.phase = LEVEL_DONE
        self.phase_since = self.now
        self.best_level = max(self.best_level, self.level)

    def _fail(self):
        self.phase = FAILED
        self.phase_since = self.now
        self.held = None

    # ---------- отрисовка ----------

    def draw(self):
        self.screen.fill(BG)
        ox, oy = self.tremor_offset()
        self.screen.blit(self.skin, (self.skin_rect.left + ox, self.skin_rect.top + oy))

        # сначала всё, что под кожей (один полупрозрачный слой на всех), потом торчащие части
        layer = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        alive = [sp for sp in self.splinters if not sp.removed]
        for sp in alive:
            self._draw_under_skin(sp, layer)
        self.screen.blit(layer, (0, 0))
        for sp in alive:
            self._draw_outside(sp)
        if self.held is not None and self.pulled_out:
            self._draw_held_out(self.held)
        for d in self.debris:
            self._draw_piece(d)

        if self.lens_on and self.phase == PLAY:
            self._draw_lens()
        self._draw_tweezers()
        self._draw_tension()
        self._draw_hud()

        for p in self.popups:
            age = (self.now - p["t0"]) / 1.2
            txt = self.font.render(p["text"], True, p["color"])
            txt.set_alpha(int(255 * (1 - age)))
            self.screen.blit(txt, txt.get_rect(center=(p["pos"][0], p["pos"][1] - 40 - 40 * age)))
        if self.message:
            txt = self.font.render(self.message, True, (255, 225, 120))
            box = txt.get_rect(center=(self.width // 2, self.height - 26)).inflate(26, 12)
            pygame.draw.rect(self.screen, (0, 0, 0), box, border_radius=8)
            self.screen.blit(txt, txt.get_rect(center=box.center))

        if self.phase == LEVEL_DONE:
            stars = 3 if self.max_pain < 35 else 2 if self.max_pain < 70 else 1
            self._overlay([("Все занозы вытащены!", self.font_big, (140, 240, 160)),
                           ("★" * stars + "☆" * (3 - stars), self.font_big, (255, 215, 80)),
                           (f"Очки за уровень: {self.level_score}   Максимум боли: {int(self.max_pain)}%", self.font, TEXT_COLOR),
                           ("Щипок — следующий уровень", self.font_small, MUTED_TEXT)])
        elif self.phase == FAILED:
            self._overlay([("Пациент отдёрнул руку!", self.font_big, (255, 120, 120)),
                           ("Слишком больно. Тяните ровнее и плавнее.", self.font, TEXT_COLOR),
                           ("Щипок — попробовать уровень заново", self.font_small, MUTED_TEXT)])

    def _draw_under_skin(self, sp, layer):
        """Часть занозы под кожей — тёмная полупрозрачная тень, плюс покраснение вокруг ранки."""
        entry = self.to_screen(sp.entry)
        inner = self.to_screen(sp.point(-sp.embedded))
        w = sp.spec["width"]
        shade = tuple(int(c * 0.55) for c in sp.spec["color"])
        if sp.redness > 0.02 or sp.broken:
            r = int(14 + 16 * sp.redness)
            pygame.draw.circle(layer, (220, 60, 60, int(40 + 80 * sp.redness)), entry, r)
        pygame.draw.line(layer, (*shade, 110), inner, entry, w + 2)
        pygame.draw.line(layer, (*shade, 150), inner, entry, max(1, w - 2))

    def _draw_outside(self, sp):
        spec = sp.spec
        held = sp is self.held
        entry = self.to_screen(sp.entry)
        w = spec["width"]
        pygame.draw.circle(self.screen, (150, 60, 60), entry, max(2, w // 2 + 1))

        # снаружи: от точки входа к кончику; в пинцете — изгибается к пинцету
        if held:
            grip = self.cursor
            tail = max(0.0, sp.exposed - math.dist(entry, grip))
            tip = (grip[0] + sp.d[0] * tail, grip[1] + sp.d[1] * tail)
            pts = [entry, grip, tip]
        else:
            pts = [entry, self.to_screen(sp.point(sp.exposed))]
        pygame.draw.lines(self.screen, spec["color"], False, pts, w)
        pygame.draw.lines(self.screen, spec["light"], False, pts, max(1, w // 3))
        if self.cfg["guide"] and held:
            far = self.to_screen(sp.point(sp.exposed + 160))
            self._dashed(self.to_screen(sp.point(sp.exposed)), far, (255, 255, 255))

    def _draw_held_out(self, sp):
        grip = self.cursor
        a = (grip[0] - sp.d[0] * sp.length * 0.3, grip[1] - sp.d[1] * sp.length * 0.3)
        b = (grip[0] + sp.d[0] * sp.length * 0.7, grip[1] + sp.d[1] * sp.length * 0.7)
        pygame.draw.line(self.screen, sp.spec["color"], a, b, sp.spec["width"])
        pygame.draw.line(self.screen, sp.spec["light"], a, b, max(1, sp.spec["width"] // 3))

    def _draw_piece(self, d):
        spec = TYPES[d["kind"]]
        a = math.radians(d["angle"])
        half = d["len"] / 2
        p1 = (d["x"] - math.cos(a) * half, d["y"] - math.sin(a) * half)
        p2 = (d["x"] + math.cos(a) * half, d["y"] + math.sin(a) * half)
        pygame.draw.line(self.screen, spec["color"], p1, p2, spec["width"])

    def _dashed(self, a, b, color):
        n = 10
        for i in range(0, n, 2):
            p = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
            q = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
            pygame.draw.line(self.screen, color, p, q, 2)

    def _draw_lens(self):
        """Лупа: область вокруг пинцета увеличена в LENS_ZOOM раз."""
        cx, cy = int(self.cursor[0]), int(self.cursor[1])
        half = int(LENS_R / LENS_ZOOM)
        src = pygame.Rect(cx - half, cy - half, 2 * half, 2 * half)
        clipped = src.clip(self.screen.get_rect())
        if clipped.width < 2 or clipped.height < 2:
            return
        zoomed = pygame.transform.smoothscale(self.screen.subsurface(clipped).copy(),
                                              (int(clipped.width * LENS_ZOOM), int(clipped.height * LENS_ZOOM)))
        lens = pygame.Surface((2 * LENS_R, 2 * LENS_R), pygame.SRCALPHA)
        lens.fill((*BG, 255))
        lens.blit(zoomed, ((clipped.x - src.x) * LENS_ZOOM, (clipped.y - src.y) * LENS_ZOOM))
        mask = pygame.Surface((2 * LENS_R, 2 * LENS_R), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255), (LENS_R, LENS_R), LENS_R)
        lens.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self.screen.blit(lens, (cx - LENS_R, cy - LENS_R))
        pygame.draw.circle(self.screen, (40, 40, 44), (cx, cy), LENS_R + 3, 6)
        pygame.draw.circle(self.screen, (200, 205, 215), (cx, cy), LENS_R + 1, 2)

    def _draw_tweezers(self):
        """Пинцет: две бранши сходятся к кончику; разжатые пальцы — бранши расходятся."""
        tip = self.cursor
        base_angle = math.radians(-55)             # ручка уходит вверх-вправо
        spread = math.radians(2 if self.pinching else 9)
        length = 190
        for side in (-1, 1):
            a = base_angle + side * spread
            end = (tip[0] + math.cos(a) * length, tip[1] + math.sin(a) * length)
            start = (tip[0] + math.cos(a) * 6 * (0 if self.pinching else 1),
                     tip[1] + math.sin(a) * 6 * (0 if self.pinching else 1))
            pygame.draw.line(self.screen, (70, 74, 82), start, end, 7)
            pygame.draw.line(self.screen, (205, 210, 220), start, end, 3)
        grip = (tip[0] + math.cos(base_angle) * length, tip[1] + math.sin(base_angle) * length)
        pygame.draw.circle(self.screen, (90, 94, 102), grip, 8)
        pygame.draw.circle(self.screen, (255, 80, 80) if self.pinching else (255, 255, 255), tip, 3)

    def _draw_tension(self):
        if self.held is None or self.pulled_out:
            return
        below = LENS_R + 12 if self.lens_on else 26            # под лупой, чтобы не закрывать вид
        x, y = self.cursor[0] - 40, self.cursor[1] + below
        pygame.draw.rect(self.screen, (0, 0, 0), (x - 2, y - 2, 84, 12), border_radius=4)
        k = min(1.0, self.stress)
        color = (int(80 + 175 * k), int(220 - 170 * k), 80)
        pygame.draw.rect(self.screen, color, (x, y, int(80 * k), 8), border_radius=3)
        label = self.font_small.render("натяжение", True, TEXT_COLOR)
        self.screen.blit(label, label.get_rect(midtop=(self.cursor[0], y + 12)))

    def _draw_hud(self):
        left = sum(1 for s in self.splinters if not s.removed)
        self.screen.blit(self.font.render(f"Уровень {self.level}", True, TEXT_COLOR), (20, 16))
        self.screen.blit(self.font_small.render(f"Заноз осталось: {left}   Очки: {self.total_score}",
                                                True, MUTED_TEXT), (20, 50))
        kinds = sorted({TYPES[s.kind]["name"] for s in self.splinters})
        self.screen.blit(self.font_small.render(", ".join(kinds), True, MUTED_TEXT), (20, 70))

        # боль пациента
        bx, by, bw = self.width // 2 - 150, 24, 300
        self.screen.blit(self.font_small.render("Боль пациента", True, MUTED_TEXT), (bx, by - 20))
        pygame.draw.rect(self.screen, (0, 0, 0), (bx - 2, by - 2, bw + 4, 22), border_radius=6)
        k = self.pain / 100
        color = (int(90 + 165 * k), int(210 - 160 * k), 90)
        pygame.draw.rect(self.screen, color, (bx, by, int(bw * k), 18), border_radius=5)
        self._draw_face(self.width - 60, 48, k)

    def _draw_face(self, cx, cy, k):
        """Лицо пациента: от улыбки до слёз."""
        face = (255, 214, 160) if k < 0.7 else (255, 180, 150)
        pygame.draw.circle(self.screen, face, (cx, cy), 32)
        pygame.draw.circle(self.screen, (60, 40, 30), (cx, cy), 32, 2)
        eye_h = 8 if k < 0.5 else 3
        for dx in (-11, 11):
            pygame.draw.ellipse(self.screen, (40, 30, 30), (cx + dx - 4, cy - 10 - eye_h // 2, 8, eye_h))
        curve = 1 - 2 * min(1.0, k * 1.3)            # 1 — улыбка, -1 — гримаса
        pts = [(cx + x, cy + 12 + curve * 6 * (1 - (x / 12) ** 2)) for x in range(-12, 13, 3)]   # y вниз: + — улыбка
        pygame.draw.lines(self.screen, (120, 40, 40), False, pts, 3)
        if k > 0.6:
            pygame.draw.circle(self.screen, (120, 180, 255), (cx + 18, cy + 2 + int(self.now * 40) % 16), 4)
        if k > 0.35:
            pygame.draw.circle(self.screen, (150, 200, 255), (cx - 26, cy - 22), 4)   # капля пота

    def _overlay(self, lines):
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))
        y = self.height // 2 - 30 * len(lines)
        for text, font, color in lines:
            txt = font.render(text, True, color)
            self.screen.blit(txt, txt.get_rect(center=(self.width // 2, y)))
            y += 64

    def _render_skin(self):
        """Ладонь крупным планом: кожа, поры и линии ладони."""
        rng = random.Random(3)
        w, h = self.skin_rect.size
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, SKIN, (0, 0, w, h), border_radius=140)
        for _ in range(900):                                      # поры и неровности
            x, y = rng.uniform(20, w - 20), rng.uniform(20, h - 20)
            c = SKIN_DARK if rng.random() < 0.6 else (244, 206, 178)
            pygame.draw.circle(surf, c, (x, y), rng.choice([1, 1, 2]))
        for k in range(3):                                        # линии ладони
            y0 = h * (0.3 + 0.2 * k)
            pts = [(x, y0 + math.sin(x / (120 + 40 * k) + k) * 40 + x * 0.12 * (k - 1))
                   for x in range(40, w - 40, 12)]
            pygame.draw.lines(surf, (200, 150, 120), False, pts, 3)
        for _ in range(60):                                       # мелкие складки
            x, y = rng.uniform(60, w - 60), rng.uniform(60, h - 60)
            a = rng.uniform(0, math.pi)
            l = rng.uniform(15, 40)
            pygame.draw.line(surf, (222, 176, 146), (x, y), (x + math.cos(a) * l, y + math.sin(a) * l), 1)
        pygame.draw.rect(surf, (200, 150, 120), (0, 0, w, h), 3, border_radius=140)
        return surf
