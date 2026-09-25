"""
Гонка в потоке машин на Pygame (вид сверху).

Дорога на 4 полосы: две левые — встречные, две правые — попутные.
Игрок едет снизу вверх, скорость растёт сама. Управление только по
горизонтали: игра получает «куда рулить» — set_target(u), где u = 0 —
центр левой полосы, u = 1 — центр правой. Откуда берётся u (зрачки,
мышь, клавиши), игра не знает.

Попутные машины едут медленнее (или быстрее — тогда обгоняют сзади),
встречные несутся навстречу: езда по встречке даёт очки x2.
Проскочить впритирку мимо машины — бонус «Близко!». Любое касание — авария.

Машины разных типов: легковая, спорткар, внедорожник, грузовик, автобус,
мотоцикл — разные размеры и скорости. В одной полосе машины не
проезжают друг сквозь друга: догнавшая тормозит за медленной.
"""

import math
import random

import pygame

# ---------- геометрия дороги ----------

LANES = 4
LANE_W = 130
ROAD_LEFT = 190
ROAD_RIGHT = ROAD_LEFT + LANES * LANE_W
LANE_CENTERS = [ROAD_LEFT + LANE_W * (i + 0.5) for i in range(LANES)]
ONCOMING = {0, 1}               # левые полосы — встречка
PX_PER_M = 24                   # масштаб: пикселей на метр

# ---------- игрок ----------

PLAYER_W, PLAYER_L = 48, 92
START_SPEED = 480               # px/с (~72 км/ч)
MAX_SPEED = 1100                # px/с (~165 км/ч)
ACCEL = 5.0                     # px/с² — плавный рост скорости со временем
STEER_SPEED = 650               # макс. боковая скорость, px/с
STEER_ACC = 3200                # боковое ускорение, px/с²

# ---------- трафик ----------

# длина, ширина (px), диапазон скорости (px/с), частота появления, цвета
VEHICLES = {
    "car":   {"length": 96, "width": 50, "speed": (300, 400), "weight": 5,
              "colors": [(60, 110, 200), (230, 230, 235), (40, 40, 45), (200, 170, 60), (90, 150, 90)]},
    "sport": {"length": 90, "width": 48, "speed": (520, 700), "weight": 2,
              "colors": [(255, 140, 0), (240, 220, 40), (30, 190, 200)]},
    "suv":   {"length": 106, "width": 56, "speed": (280, 380), "weight": 3,
              "colors": [(70, 70, 80), (120, 30, 40), (200, 200, 205), (40, 70, 60)]},
    "truck": {"length": 190, "width": 64, "speed": (200, 280), "weight": 2,
              "colors": [(200, 60, 50), (40, 90, 170), (230, 180, 40)]},
    "bus":   {"length": 210, "width": 66, "speed": (210, 270), "weight": 1,
              "colors": [(240, 190, 30), (60, 130, 190)]},
    "moto":  {"length": 56, "width": 24, "speed": (400, 600), "weight": 1,
              "colors": [(220, 30, 30), (30, 30, 30), (40, 120, 220)]},
}
LANE_WEIGHTS = [0.15, 0.2, 0.33, 0.32]   # встречки поменьше — она опаснее
FOLLOW_GAP = 70                 # дистанция, на которой машина начинает тормозить за передней
SPAWN_GAP = 60
NEAR_MISS = 24                  # px между бортами — «впритирку»

# ---------- тайминги ----------

COUNTDOWN = 3.0
CRASH_TIME = 1.3
RESTART_DELAY = 1.2
POPUP_TIME = 1.0

# ---------- цвета ----------

GRASS = (58, 120, 58)
GRASS_DARK = (52, 110, 52)
ASPHALT = (62, 64, 70)
MARKING = (235, 235, 235)
YELLOW = (240, 200, 50)
TEXT_COLOR = (245, 245, 245)
PLAYER_COLOR = (215, 35, 45)

COUNTDOWN_PHASE, DRIVE, CRASH, GAME_OVER = "countdown", "drive", "crash", "game_over"


def make_vehicle_surface(kind, color, stripes=False):
    """Рисует машину вида сверху, передом вверх."""
    spec = VEHICLES[kind]
    w, l = spec["width"], spec["length"]
    surf = pygame.Surface((w + 8, l), pygame.SRCALPHA)
    x0 = 4
    glass = (40, 50, 64)
    tire = (20, 20, 20)
    lighter = tuple(min(255, c + 35) for c in color)

    if kind == "moto":
        pygame.draw.rect(surf, tire, (x0 + w // 2 - 3, 0, 6, 14), border_radius=3)
        pygame.draw.rect(surf, tire, (x0 + w // 2 - 3, l - 14, 6, 14), border_radius=3)
        pygame.draw.rect(surf, color, (x0 + 3, 10, w - 6, l - 22), border_radius=8)
        pygame.draw.line(surf, (160, 160, 170), (x0, 14), (x0 + w, 14), 3)       # руль
        pygame.draw.circle(surf, (30, 30, 30), (x0 + w // 2, l // 2 + 2), 9)    # шлем
        pygame.draw.circle(surf, lighter, (x0 + w // 2, l // 2), 5)
        return surf

    # колёса
    for wy in (l * 0.14, l * 0.72 if kind in ("car", "sport", "suv") else l * 0.8):
        pygame.draw.rect(surf, tire, (0, wy, 8, l * 0.16), border_radius=3)
        pygame.draw.rect(surf, tire, (w, wy, 8, l * 0.16), border_radius=3)

    if kind == "truck":
        cab = int(l * 0.26)
        pygame.draw.rect(surf, (225, 225, 228), (x0, cab + 6, w, l - cab - 6), border_radius=4)  # кузов
        for k in range(cab + 20, l - 8, 22):
            pygame.draw.line(surf, (190, 190, 195), (x0 + 4, k), (x0 + w - 4, k), 2)
        pygame.draw.rect(surf, color, (x0 + 2, 0, w - 4, cab), border_radius=8)                 # кабина
        pygame.draw.rect(surf, glass, (x0 + 7, 8, w - 14, cab * 0.35), border_radius=4)
    elif kind == "bus":
        pygame.draw.rect(surf, color, (x0, 0, w, l), border_radius=10)
        pygame.draw.rect(surf, lighter, (x0 + 8, 30, w - 16, l - 50), border_radius=6)         # крыша
        pygame.draw.rect(surf, glass, (x0 + 5, 6, w - 10, 16), border_radius=4)                 # лобовое
        for k in range(34, l - 20, 26):                                                         # люки
            pygame.draw.rect(surf, color, (x0 + w // 2 - 10, k, 20, 12), border_radius=3)
    else:
        radius = int(w * 0.35)
        pygame.draw.rect(surf, color, (x0, 0, w, l), border_radius=radius)
        pygame.draw.polygon(surf, glass, [(x0 + 7, l * 0.27), (x0 + w - 7, l * 0.27),
                                          (x0 + w - 10, l * 0.40), (x0 + 10, l * 0.40)])       # лобовое
        pygame.draw.rect(surf, lighter, (x0 + 9, l * 0.42, w - 18, l * 0.28), border_radius=6)  # крыша
        pygame.draw.polygon(surf, glass, [(x0 + 10, l * 0.73), (x0 + w - 10, l * 0.73),
                                          (x0 + w - 8, l * 0.82), (x0 + 8, l * 0.82)])         # заднее
        if stripes:
            for dx in (-7, 3):
                pygame.draw.rect(surf, (250, 250, 250), (x0 + w // 2 + dx, 0, 4, l))

    # фары и стоп-сигналы
    pygame.draw.rect(surf, (255, 250, 200), (x0 + 5, 1, 9, 5), border_radius=2)
    pygame.draw.rect(surf, (255, 250, 200), (x0 + w - 14, 1, 9, 5), border_radius=2)
    pygame.draw.rect(surf, (220, 30, 30), (x0 + 5, l - 5, 9, 4), border_radius=2)
    pygame.draw.rect(surf, (220, 30, 30), (x0 + w - 14, l - 5, 9, 4), border_radius=2)
    return surf


class Vehicle:
    def __init__(self, kind, lane, y, speed):
        spec = VEHICLES[kind]
        self.kind = kind
        self.lane = lane
        self.oncoming = lane in ONCOMING
        self.length, self.width = spec["length"], spec["width"]
        self.x = LANE_CENTERS[lane] + random.uniform(-10, 10)
        self.y = y
        self.desired = speed          # желаемая скорость, px/с вдоль своего направления
        self.speed = speed
        self.passed = False           # уже разминулся с игроком
        surf = make_vehicle_surface(kind, random.choice(spec["colors"]))
        self.surface = pygame.transform.rotate(surf, 180) if self.oncoming else surf

    def rect(self, shrink=0):
        r = pygame.Rect(0, 0, self.width - 2 * shrink, self.length - 2 * shrink)
        r.center = (self.x, self.y)
        return r

    def screen_vy(self, player_speed):
        """Скорость по экрану: игрок стоит на месте, мир едет вниз."""
        return player_speed + self.speed if self.oncoming else player_speed - self.speed


class RaceGame:
    def __init__(self, screen, width, height):
        self.screen = screen
        self.width = width
        self.height = height
        self.player_y = height - 150

        self.font_small = pygame.font.SysFont("arial", 17)
        self.font = pygame.font.SysFont("arial", 24, bold=True)
        self.font_big = pygame.font.SysFont("arial", 56, bold=True)

        self.player_surface = make_vehicle_surface("sport", PLAYER_COLOR, stripes=True)
        self.best = 0
        self.paused = False
        self.reset()

    def reset(self):
        self.phase = COUNTDOWN_PHASE
        self.phase_t = 0.0
        self.time = 0.0
        self.now = 0.0
        self.player_x = LANE_CENTERS[2]
        self.player_vx = 0.0
        self.player_speed = START_SPEED
        self.player_angle = 0.0
        self.target_u = 2 / 3
        self.distance = 0.0
        self.score = 0.0
        self.traffic = []
        self.popups = []
        self.sparks = []
        self.spawn_timer = 0.5
        self.over_since = 0.0
        self.decor = [self._new_decor(random.uniform(-40, self.height)) for _ in range(14)]

    # ---------- ввод ----------

    def set_target(self, u):
        """Куда рулить: 0 — центр левой полосы, 1 — центр правой (можно чуть за пределы)."""
        self.target_u = max(-0.15, min(1.15, u))

    def action(self):
        """Моргание / пробел: перезапуск после аварии."""
        if self.phase == GAME_OVER and self.now - self.over_since >= RESTART_DELAY:
            self.reset()

    # ---------- логика ----------

    @property
    def in_oncoming(self):
        return self.player_x < ROAD_LEFT + 2 * LANE_W

    def update(self, dt):
        if self.paused:
            return
        self.now += dt
        self.phase_t += dt

        if self.phase == COUNTDOWN_PHASE:
            self._steer(dt)
            if self.phase_t >= COUNTDOWN:
                self.phase, self.phase_t = DRIVE, 0.0
            return

        if self.phase == DRIVE:
            self.time += dt
            self.player_speed = min(MAX_SPEED, START_SPEED + ACCEL * self.time)
            self._steer(dt)
            self.distance += self.player_speed * dt
            meters = self.player_speed * dt / PX_PER_M
            self.score += meters * (2 if self.in_oncoming else 1)

            self.spawn_timer -= dt
            if self.spawn_timer <= 0:
                self._spawn()
                interval = max(0.22, 0.6 - self.time * 0.003)   # поток со временем плотнее
                self.spawn_timer = interval * random.uniform(0.6, 1.4)
        elif self.phase == CRASH:
            self.player_speed = max(0.0, self.player_speed - 1500 * dt)
            self.player_angle += self.spin * dt
            self.spin *= 0.97
            if self.phase_t >= CRASH_TIME:
                self.phase = GAME_OVER
                self.over_since = self.now
        elif self.phase == GAME_OVER:
            self.player_speed = 0.0

        self._update_traffic(dt)
        if self.phase == DRIVE:
            self._check_player()

        for d in self.decor:
            d["y"] += self.player_speed * dt
        self.decor = [d if d["y"] < self.height + 60 else self._new_decor(-60) for d in self.decor]
        for s in self.sparks:
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt + self.player_speed * dt
            s["life"] -= dt
        self.sparks = [s for s in self.sparks if s["life"] > 0]
        self.popups = [p for p in self.popups if self.now - p["t0"] < POPUP_TIME]

    def _steer(self, dt):
        target_x = LANE_CENTERS[0] + self.target_u * (LANE_CENTERS[-1] - LANE_CENTERS[0])
        target_x = max(ROAD_LEFT + PLAYER_W / 2 + 4, min(ROAD_RIGHT - PLAYER_W / 2 - 4, target_x))
        desired_vx = max(-STEER_SPEED, min(STEER_SPEED, (target_x - self.player_x) * 7))
        dv = max(-STEER_ACC * dt, min(STEER_ACC * dt, desired_vx - self.player_vx))
        self.player_vx += dv
        self.player_x += self.player_vx * dt
        self.player_angle = -self.player_vx * 0.018       # машину чуть поворачивает при манёвре

    def _spawn(self):
        kind = random.choices(list(VEHICLES), weights=[v["weight"] for v in VEHICLES.values()])[0]
        spec = VEHICLES[kind]
        speed = random.uniform(*spec["speed"])
        lanes = list(range(LANES))
        random.shuffle(lanes)
        lanes.sort(key=lambda i: -LANE_WEIGHTS[i] * random.random())
        for lane in lanes:                  # полоса занята — пробуем другую
            if self._can_spawn(lane, spec, speed):
                self.traffic.append(Vehicle(kind, lane, self._spawn_y(lane, spec, speed), speed))
                return

    def _spawn_y(self, lane, spec, speed):
        from_below = lane not in ONCOMING and speed > self.player_speed   # быстрый попутный — обгонит сзади
        return self.height + spec["length"] if from_below else -spec["length"]

    def _can_spawn(self, lane, spec, speed):
        y = self._spawn_y(lane, spec, speed)
        for v in self.traffic:
            if v.lane == lane and abs(v.y - y) < (v.length + spec["length"]) / 2 + SPAWN_GAP:
                return False
        if y < 0:
            # не перекрываем разом все полосы — должен оставаться проезд
            blocked = {v.lane for v in self.traffic if v.lane != lane and v.y - v.length / 2 < 260}
            if len(blocked) >= LANES - 1:
                return False
        return True

    def _update_traffic(self, dt):
        for lane in range(LANES):
            cars = [v for v in self.traffic if v.lane == lane]
            # «впереди» по ходу движения: для встречки — ниже по экрану, для попутки — выше
            cars.sort(key=lambda v: -v.y if lane in ONCOMING else v.y)
            for i, v in enumerate(cars):
                target = v.desired
                if i > 0:
                    lead = cars[i - 1]
                    if v.oncoming:
                        gap = (lead.y - lead.length / 2) - (v.y + v.length / 2)
                    else:
                        gap = (v.y - v.length / 2) - (lead.y + lead.length / 2)
                    if gap < FOLLOW_GAP:
                        target = min(target, lead.speed * (0.9 if gap < FOLLOW_GAP / 2 else 1.0))
                if (not v.oncoming and self.phase != COUNTDOWN_PHASE and v.y > self.player_y
                        and abs(v.x - self.player_x) < (v.width + PLAYER_W) / 2 + 10):
                    # догоняющий сзади попутный притормаживает за игроком
                    gap = (v.y - v.length / 2) - (self.player_y + PLAYER_L / 2)
                    if gap < FOLLOW_GAP:
                        target = min(target, self.player_speed * 0.95)
                v.speed += max(-1500 * dt, min(400 * dt, target - v.speed))

        for v in self.traffic:
            v.y += v.screen_vy(self.player_speed) * dt
        self.traffic = [v for v in self.traffic if -400 < v.y < self.height + 400]

    def _player_rect(self, shrink=6):
        r = pygame.Rect(0, 0, PLAYER_W - 2 * shrink, PLAYER_L - 2 * shrink)
        r.center = (self.player_x, self.player_y)
        return r

    def _check_player(self):
        me = self._player_rect()
        for v in self.traffic:
            if me.colliderect(v.rect(shrink=4)):
                self._crash(v)
                return
            # разминулись: центр машины пересёк линию игрока
            side = v.y - self.player_y
            if not v.passed and (side > 0) == (v.screen_vy(self.player_speed) > 0):
                v.passed = True
                gap = abs(v.x - self.player_x) - (v.width + PLAYER_W) / 2
                if 0 < gap < NEAR_MISS:
                    bonus = 100 if v.oncoming else 50
                    self.score += bonus
                    self._popup(f"Близко! +{bonus}", (255, 220, 80))

    def _crash(self, vehicle):
        self.phase, self.phase_t = CRASH, 0.0
        self.spin = random.choice([-1, 1]) * 540
        self.best = max(self.best, int(self.score))
        vehicle.desired = vehicle.speed = 0 if not vehicle.oncoming else vehicle.speed * 0.3
        cx, cy = (self.player_x + vehicle.x) / 2, (self.player_y + vehicle.y) / 2
        for _ in range(40):
            a = random.uniform(0, 2 * math.pi)
            v = random.uniform(80, 420)
            self.sparks.append({"x": cx, "y": cy, "vx": math.cos(a) * v, "vy": math.sin(a) * v,
                                "life": random.uniform(0.3, 0.9),
                                "color": random.choice([(255, 200, 60), (255, 120, 30), (240, 240, 240)])})

    def _popup(self, text, color):
        self.popups.append({"text": text, "color": color, "t0": self.now,
                            "pos": (self.player_x, self.player_y - PLAYER_L)})

    def _new_decor(self, y):
        side = random.choice([0, 1])
        x = random.uniform(20, ROAD_LEFT - 30) if side == 0 else random.uniform(ROAD_RIGHT + 30, self.width - 20)
        return {"x": x, "y": y, "r": random.uniform(14, 30), "kind": random.choice(["tree", "tree", "bush"])}

    # ---------- отрисовка ----------

    def draw(self):
        self._draw_road()
        for v in self.traffic:
            self.screen.blit(v.surface, v.surface.get_rect(center=(v.x, v.y)))
        self._draw_player()
        for s in self.sparks:
            pygame.draw.circle(self.screen, s["color"], (s["x"], s["y"]), 3)
        for p in self.popups:
            age = (self.now - p["t0"]) / POPUP_TIME
            txt = self.font.render(p["text"], True, p["color"])
            txt.set_alpha(int(255 * (1 - age)))
            self.screen.blit(txt, txt.get_rect(center=(p["pos"][0], p["pos"][1] - 40 * age)))
        self._draw_hud()

        if self.phase == COUNTDOWN_PHASE and not self.paused:
            left = COUNTDOWN - self.phase_t
            text = str(math.ceil(left)) if left > 0.4 else "ПОЕХАЛИ!"
            self._center_text(text, self.font_big, self.height // 2 - 60)
        elif self.phase == GAME_OVER:
            overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            self.screen.blit(overlay, (0, 0))
            self._center_text("АВАРИЯ", self.font_big, self.height // 2 - 90, (255, 110, 110))
            self._center_text(f"Очки: {int(self.score)}   Рекорд: {self.best}", self.font, self.height // 2 - 10)
            self._center_text(f"Проехано: {self.distance / PX_PER_M / 1000:.2f} км", self.font, self.height // 2 + 30)
            self._center_text("Моргните, ПРОБЕЛ или R — заново", self.font_small, self.height // 2 + 80)

    def _center_text(self, text, font, y, color=TEXT_COLOR):
        txt = font.render(text, True, color)
        shadow = font.render(text, True, (0, 0, 0))
        rect = txt.get_rect(center=(self.width // 2, y))
        self.screen.blit(shadow, rect.move(2, 2))
        self.screen.blit(txt, rect)

    def _draw_road(self):
        offset = self.distance % 80
        self.screen.fill(GRASS)
        for y in range(-80, self.height + 80, 80):          # полосы на траве — видно скорость
            pygame.draw.rect(self.screen, GRASS_DARK, (0, y + offset, self.width, 40))
        for d in self.decor:
            if d["kind"] == "tree":
                pygame.draw.circle(self.screen, (30, 80, 35), (d["x"] + 4, d["y"] + 5), d["r"])
                pygame.draw.circle(self.screen, (40, 105, 45), (d["x"], d["y"]), d["r"])
                pygame.draw.circle(self.screen, (60, 135, 60), (d["x"] - d["r"] * 0.3, d["y"] - d["r"] * 0.3), d["r"] * 0.45)
            else:
                pygame.draw.circle(self.screen, (80, 140, 60), (d["x"], d["y"]), d["r"] * 0.6)

        pygame.draw.rect(self.screen, (120, 120, 110), (ROAD_LEFT - 12, 0, ROAD_RIGHT - ROAD_LEFT + 24, self.height))
        pygame.draw.rect(self.screen, ASPHALT, (ROAD_LEFT, 0, ROAD_RIGHT - ROAD_LEFT, self.height))
        pygame.draw.line(self.screen, MARKING, (ROAD_LEFT + 6, 0), (ROAD_LEFT + 6, self.height), 4)
        pygame.draw.line(self.screen, MARKING, (ROAD_RIGHT - 6, 0), (ROAD_RIGHT - 6, self.height), 4)
        for lane_edge in (1, 3):                              # прерывистые между полосами одного направления
            x = ROAD_LEFT + lane_edge * LANE_W
            for y in range(-80, self.height + 80, 80):
                pygame.draw.rect(self.screen, MARKING, (x - 3, y + offset, 6, 44))
        mid = ROAD_LEFT + 2 * LANE_W                          # двойная сплошная
        for dx in (-5, 5):
            pygame.draw.line(self.screen, YELLOW, (mid + dx, 0), (mid + dx, self.height), 3)

    def _draw_player(self):
        car = pygame.transform.rotozoom(self.player_surface, self.player_angle, 1)
        self.screen.blit(car, car.get_rect(center=(self.player_x, self.player_y)))

    def _draw_hud(self):
        speed_kmh = self.player_speed / PX_PER_M * 3.6
        lines = [f"Очки: {int(self.score)}", f"{speed_kmh:.0f} км/ч",
                 f"{self.distance / PX_PER_M / 1000:.2f} км", f"Рекорд: {self.best}"]
        for i, line in enumerate(lines):
            txt = self.font.render(line, True, TEXT_COLOR)
            self.screen.blit(self.font.render(line, True, (0, 0, 0)), (18, 16 + i * 32))
            self.screen.blit(txt, (16, 14 + i * 32))
        if self.phase == DRIVE and self.in_oncoming:
            txt = self.font.render("ВСТРЕЧКА  x2", True, (255, 90, 90))
            self.screen.blit(txt, txt.get_rect(topright=(self.width - 16, 14)))

        # куда сейчас «смотрит руль» — маркер под дорогой
        tx = LANE_CENTERS[0] + self.target_u * (LANE_CENTERS[-1] - LANE_CENTERS[0])
        tx = max(ROAD_LEFT, min(ROAD_RIGHT, tx))
        y = self.height - 14
        pygame.draw.line(self.screen, (20, 20, 20), (ROAD_LEFT, y), (ROAD_RIGHT, y), 4)
        pygame.draw.polygon(self.screen, (80, 230, 255), [(tx, y - 12), (tx - 9, y + 4), (tx + 9, y + 4)])

        if self.paused and self.phase != GAME_OVER:
            txt = self.font.render("ПАУЗА", True, (255, 220, 80))
            self.screen.blit(txt, txt.get_rect(center=(self.width // 2, 40)))
