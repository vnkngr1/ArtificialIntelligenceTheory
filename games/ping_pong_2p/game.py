"""
Пинг-понг на двоих на Pygame (вид сверху, стол горизонтально).

Игрок 1 — слева, игрок 2 — справа: так же, как они стоят перед камерой.
Физика та же, что в одиночном пинг-понге: у мяча есть высота z, он летит
дугой над сеткой и отскакивает от стола, поэтому сила удара решает —
слабо: сетка или своя сторона, слишком сильно: аут.

Удар — взмах к сопернику: игрок 1 бьёт вправо, игрок 2 — влево. Сила —
скорость взмаха. Край ракетки и движение руки вверх/вниз уводят мяч в
сторону, прямой взмах слегка доворачивает мяч к центру половины соперника.

Правила: мяч после удара должен отскочить на половине соперника; второй
отскок там же или пропущенный мяч — очко ударившему; аут, сетка или отскок
на своей половине — очко сопернику. Подачи по две, при 10:10 — по одной,
партия до 11 с разницей в 2.

Если играете один — за игрока 2 может играть компьютер (set_bot).
Игра ничего не знает про камеру: каждый кадр ей передают положение и
скорость взмаха каждой ракетки — set_paddle(side, pos, swing).
"""

import math
import random
from collections import deque

import pygame

# ---------- стол ----------

TABLE = pygame.Rect(200, 190, 880, 440)     # вид сверху, длинная сторона — горизонтально
NET_X = TABLE.centerx
NET_H = 18
BALL_R = 9
PADDLE_R = 44

# ---------- физика ----------

G = 1500.0
HIT_Z = 40.0
VZ_HIT = 420.0
BOUNCE = 0.75
TABLE_FRICTION = 0.93
SUBSTEPS = 3

# ---------- удар ----------

SWING_GAIN = 1.0            # скорость мяча на 1 px/с взмаха
INCOMING_KEEP = 0.35
MIN_BALL_SPEED, MAX_BALL_SPEED = 150.0, 2200.0
EDGE_ANGLE = 12             # град: удар краем ракетки
SIDE_ANGLE = 20             # град: движение руки вверх/вниз во время взмаха
AIM_ASSIST = 0.7            # доворот к центру половины соперника (камера не видит наклон ракетки)
SERVE_SWING = 300           # px/с — взмах, с которого начинается подача
BOT_BLUNDER = 0.08          # доля ударов компьютера с грубой ошибкой — чтобы у человека был шанс

# ---------- матч ----------

POINTS_TO_WIN = 11
POINT_PAUSE = 1.4

# ---------- цвета ----------

FLOOR = (38, 42, 52)
FLOOR_LINE = (46, 51, 63)
TABLE_BLUE = (24, 110, 80)
TABLE_EDGE = (14, 74, 52)
WHITE = (245, 245, 245)
TEXT = (238, 240, 246)
MUTED = (150, 158, 178)

LEFT, RIGHT = "left", "right"
SIDES = (LEFT, RIGHT)
NAMES = {LEFT: "Игрок 1", RIGHT: "Игрок 2"}
COLORS = {LEFT: (230, 70, 70), RIGHT: (70, 140, 240)}
DARK = {LEFT: (150, 30, 34), RIGHT: (30, 80, 170)}
SERVE, RALLY, POINT, OVER = "serve", "rally", "point", "over"


def other(side):
    return RIGHT if side == LEFT else LEFT


def direction(side):
    """Куда бьёт игрок: +1 — вправо (игрок 1), -1 — влево (игрок 2)."""
    return 1 if side == LEFT else -1


def half(side):
    """Половина стола игрока."""
    return pygame.Rect(TABLE.left, TABLE.top, TABLE.width // 2, TABLE.height) if side == LEFT else \
        pygame.Rect(NET_X, TABLE.top, TABLE.width // 2, TABLE.height)


def flight_time(z0=HIT_Z, vz=VZ_HIT, z_end=0.0):
    return (vz + math.sqrt(vz * vz + 2 * G * (z0 - z_end))) / G


class Ball:
    def __init__(self):
        self.x, self.y, self.z = TABLE.left, TABLE.centery, HIT_Z
        self.vx = self.vy = self.vz = 0.0
        self.trail = deque(maxlen=10)

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)


class PingPong2PGame:
    def __init__(self, screen, width, height):
        self.screen = screen
        self.width = width
        self.height = height
        self.font_small = pygame.font.SysFont("arial", 16)
        self.font = pygame.font.SysFont("arial", 22, bold=True)
        self.font_score = pygame.font.SysFont("arial", 52, bold=True)
        self.font_big = pygame.font.SysFont("arial", 50, bold=True)
        self.background = self._render_background()
        self.paddle = {LEFT: (TABLE.left - 60.0, TABLE.centery), RIGHT: (TABLE.right + 60.0, TABLE.centery)}
        self.prev_paddle = dict(self.paddle)
        self.swing = {LEFT: (0.0, 0.0, 0.0), RIGHT: (0.0, 0.0, 0.0)}
        self.bot = None                     # сторона, за которую играет компьютер (или None)
        self.bot_state = {"target": None, "move_at": 0.0}
        self.wins = {LEFT: 0, RIGHT: 0}
        self.reset_match()

    # ---------- партия ----------

    def reset_match(self):
        self.now = 0.0
        self.score = {LEFT: 0, RIGHT: 0}
        self.ball = Ball()
        self.popups = []
        self.last_hit = {LEFT: None, RIGHT: None}
        self.winner = None
        self._new_serve()

    def set_bot(self, side):
        """За сторону side играет компьютер (None — оба игрока люди)."""
        self.bot = side

    @property
    def server(self):
        total = self.score[LEFT] + self.score[RIGHT]
        turn = total if min(self.score.values()) >= POINTS_TO_WIN - 1 else total // 2
        return LEFT if turn % 2 == 0 else RIGHT

    def _new_serve(self):
        self.phase = SERVE
        self.phase_since = self.now
        self.hitter = None
        self.bounced_on = None
        self.net_checked = False
        self.ball.trail.clear()

    # ---------- ввод ----------

    def set_paddle(self, side, pos, swing):
        """pos — где ракетка; swing — (скорость к сопернику, скорость вверх/вниз, сила взмаха), px/с."""
        self.prev_paddle[side] = self.paddle[side]
        self.paddle[side] = (float(pos[0]), float(pos[1]))
        self.swing[side] = swing

    # ---------- логика ----------

    def update(self, dt):
        self.now += dt
        if self.bot:
            self._update_bot(dt)
        if self.phase == SERVE:
            self._update_serve()
        elif self.phase == OVER:
            if self.now - self.phase_since > 1.5 and any(
                    self.swing[s][2] > SERVE_SWING * 1.5 for s in SIDES if s != self.bot):
                self.reset_match()
            return

        if self.phase in (RALLY, POINT):
            h = dt / SUBSTEPS
            pads = {}
            for i in range(SUBSTEPS):
                # ракетки за кадр сдвигаются один раз — внутри кадра ведём их плавно
                for s in SIDES:
                    (ox, oy), (px, py) = self.prev_paddle[s], self.paddle[s]
                    k0, k1 = i / SUBSTEPS, (i + 1) / SUBSTEPS
                    pads[s] = ((ox + (px - ox) * k0, oy + (py - oy) * k0),
                               (ox + (px - ox) * k1, oy + (py - oy) * k1))
                self._step_ball(h, pads)
            if self.phase == POINT and self.now - self.phase_since > POINT_PAUSE:
                if self.winner:
                    self.phase = OVER
                    self.phase_since = self.now
                else:
                    self._new_serve()
        self.popups = [p for p in self.popups if self.now - p["t0"] < 1.4]

    def _update_serve(self):
        s = self.server
        b = self.ball
        b.vx = b.vy = b.vz = 0.0
        b.z = HIT_Z
        px, py = self.paddle[s]
        b.x, b.y = px + direction(s) * (PADDLE_R + BALL_R), py
        if self.now - self.phase_since > 0.5 and self.swing[s][2] > SERVE_SWING:
            self._hit(s, serve=True)

    def _step_ball(self, h, pads):
        b = self.ball
        bx0, by0 = b.x, b.y
        b.vz -= G * h
        b.x += b.vx * h
        b.y += b.vy * h
        b.z += b.vz * h
        live = self.phase == RALLY

        if live and not self.net_checked and (bx0 - NET_X) * (b.x - NET_X) <= 0:
            self.net_checked = True
            if b.z < NET_H and TABLE.top - 20 <= b.y <= TABLE.bottom + 20:
                b.vx *= -0.15
                b.vy *= 0.3
                self._point(other(self.hitter), "сетка")

        if b.z <= 0:
            if TABLE.collidepoint(b.x, b.y):
                b.z = 0.0
                b.vz = -b.vz * BOUNCE
                b.vx *= TABLE_FRICTION
                b.vy *= TABLE_FRICTION
                if live:
                    self._bounce(LEFT if b.x < NET_X else RIGHT)
            else:
                b.z = 0.0
                b.vz = -b.vz * 0.4
                b.vx *= 0.5
                b.vy *= 0.5
                if live:
                    self._floor()

        if self.phase == RALLY:
            for s in SIDES:
                if self._touches(s, (bx0, by0), *pads[s]):
                    self._hit(s)
                    break
            if not (-80 < b.x < self.width + 80 and -80 < b.y < self.height + 80):
                self._floor()

    def _bounce(self, side):
        if self.bounced_on is None:
            if side == self.hitter:
                self._point(other(self.hitter), "не перелетел")
            else:
                self.bounced_on = side
        else:
            self._point(self.hitter, "два отскока")

    def _floor(self):
        if self.phase != RALLY:
            return
        if self.bounced_on is None:
            self._point(other(self.hitter), "аут")
        else:
            self._point(self.hitter, "не отбил")

    def _point(self, winner, reason):
        if self.phase != RALLY:
            return
        self.score[winner] += 1
        self.phase = POINT
        self.phase_since = self.now
        loser = other(winner)
        self.popups.append({"text": f"+1 {NAMES[winner]}", "sub": f"{NAMES[loser]}: {reason}",
                            "color": COLORS[winner], "t0": self.now, "pos": (TABLE.centerx, TABLE.top - 70)})
        s, r = self.score[LEFT], self.score[RIGHT]
        if max(s, r) >= POINTS_TO_WIN and abs(s - r) >= 2:
            self.winner = LEFT if s > r else RIGHT
            self.wins[self.winner] += 1

    # ---------- удары ----------

    def _touches(self, side, ball0, pad0, pad1):
        """Ракетка встретила мяч: отрезок движения мяча относительно ракетки проходит
        ближе радиуса — так быстрый мяч или резкий взмах не «проскакивают» между кадрами."""
        b = self.ball
        d = direction(side)
        if self.hitter == side or b.vx * d >= 0 or (b.x - NET_X) * d > 0:
            return False                    # мяч летит не к этому игроку или уже на чужой половине
        ax, ay = ball0[0] - pad0[0], ball0[1] - pad0[1]
        bx, by = b.x - pad1[0], b.y - pad1[1]
        dx, dy = bx - ax, by - ay
        k = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        return math.hypot(ax + dx * k, ay + dy * k) <= PADDLE_R + BALL_R

    def _hit(self, side, serve=False):
        b = self.ball
        d = direction(side)
        _, lateral, power = self.swing[side]
        far_end = TABLE.right if d > 0 else TABLE.left
        v_min = abs(NET_X - b.x) / flight_time(z_end=NET_H)
        v_max = abs(far_end - b.x) / flight_time()
        tilt = max(-1.0, min(1.0, lateral / 900))
        if serve:
            # подача всегда в стол: сильнее взмах — глубже, взмах вверх/вниз — ближе к углу
            k = max(0.1, min(0.9, (power - SERVE_SWING) / 1400))
            tx = NET_X + d * (TABLE.width / 2) * (0.25 + 0.6 * k)
            ty = min(max(TABLE.centery + tilt * TABLE.height * 0.35, TABLE.top + 30), TABLE.bottom - 30)
            v = math.hypot(tx - b.x, ty - b.y) / flight_time()
            angle = math.atan2(ty - b.y, abs(tx - b.x))
        else:
            v = INCOMING_KEEP * b.speed + SWING_GAIN * power
            v = min(MAX_BALL_SPEED, max(MIN_BALL_SPEED, v))
            offset = max(-1.0, min(1.0, (b.y - self.paddle[side][1]) / PADDLE_R))
            cx = (NET_X + far_end) / 2
            to_center = math.atan2(TABLE.centery - b.y, abs(cx - b.x))
            angle = AIM_ASSIST * to_center + math.radians(offset * EDGE_ANGLE + tilt * SIDE_ANGLE)
        b.vx = d * v * math.cos(angle)
        b.vy = v * math.sin(angle)
        b.z, b.vz = HIT_Z, VZ_HIT
        self.hitter = side
        self.bounced_on = None
        self.net_checked = False
        self.phase = RALLY
        self.last_hit[side] = (v, v_min, v_max)
        if self.bot:
            self._plan_bot()

    # ---------- компьютер за одного из игроков ----------

    def _plan_bot(self):
        """Куда бежать компьютеру: на линию за своим краем стола, туда, где пройдёт мяч."""
        side = self.bot
        b = self.ball
        home_x = (TABLE.right + 50) if side == RIGHT else (TABLE.left - 50)
        target = (home_x, TABLE.centery)
        if self.hitter != side and b.vx * direction(side) < 0:
            t = (home_x - b.x) / b.vx if b.vx else 0
            y = b.y + b.vy * max(0.0, t)
            # отражаем от «стенок», чтобы не бежать за пределы разумного
            target = (home_x, min(max(y + random.gauss(0, 14), TABLE.top - 60), TABLE.bottom + 60))
        self.bot_state = {"target": target, "move_at": self.now + 0.12}

    def _update_bot(self, dt):
        side = self.bot
        if self.bot_state["target"] is None:
            self._plan_bot()
        st = self.bot_state                 # _plan_bot заменяет словарь — берём уже новый
        px, py = self.paddle[side]
        swing = (0.0, 0.0, 0.0)
        if self.now >= st["move_at"]:
            tx, ty = st["target"]
            dx, dy = tx - px, ty - py
            dist = math.hypot(dx, dy)
            step = 700 * dt
            if dist > 0:
                k = min(1.0, step / dist)
                px, py = px + dx * k, py + dy * k
        b = self.ball
        d = direction(side)
        if self.phase == SERVE and self.server == side and self.now - self.phase_since > 1.0:
            swing = (700.0, random.uniform(-400, 400), 700.0)
        elif self.phase == RALLY and self.hitter != side and b.vx * d < 0 and abs(b.x - px) < 160:
            far_end = TABLE.right if d > 0 else TABLE.left
            v_need = abs((NET_X + far_end) / 2 - b.x) / flight_time() * random.gauss(1.0, 0.12)
            if random.random() < BOT_BLUNDER:
                v_need *= random.choice((0.55, 1.5))      # иногда ошибается грубо: в сетку или в аут
            power = max(0.0, (v_need - INCOMING_KEEP * b.speed) / SWING_GAIN)
            swing = (power, random.gauss(0, 200), power)
        self.set_paddle(side, (px, py), swing)

    # ---------- отрисовка ----------

    def draw(self):
        self.screen.blit(self.background, (0, 0))
        self._draw_table()
        self._draw_ball_shadow()
        for s in SIDES:
            self._draw_paddle(s)
        self._draw_ball()
        self._draw_hud()
        for p in self.popups:
            age = (self.now - p["t0"]) / 1.4
            alpha = int(255 * min(1.0, 2 * (1 - age)))
            x, y = p["pos"]
            for text, font, color, dy in ((p["text"], self.font_big, p["color"], 0),
                                          (p["sub"], self.font, TEXT, 44)):
                txt = font.render(text, True, color)
                txt.set_alpha(alpha)
                self.screen.blit(txt, txt.get_rect(center=(x, y + dy - 20 * age)))
        if self.phase == OVER:
            self._draw_over()

    def _draw_table(self):
        pygame.draw.rect(self.screen, (20, 22, 28), TABLE.move(8, 10), border_radius=4)
        pygame.draw.rect(self.screen, TABLE_BLUE, TABLE)
        pygame.draw.rect(self.screen, TABLE_EDGE, TABLE, 3)
        inner = TABLE.inflate(-10, -10)
        pygame.draw.rect(self.screen, WHITE, inner, 4)
        pygame.draw.line(self.screen, WHITE, (inner.left, TABLE.centery), (inner.right, TABLE.centery), 2)
        pygame.draw.line(self.screen, (10, 10, 14), (NET_X + 5, TABLE.top - 18), (NET_X + 5, TABLE.bottom + 18), 6)
        pygame.draw.line(self.screen, (225, 225, 230), (NET_X, TABLE.top - 18), (NET_X, TABLE.bottom + 18), 5)
        for y in range(TABLE.top - 12, TABLE.bottom + 14, 12):
            pygame.draw.line(self.screen, (160, 160, 170), (NET_X - 2, y), (NET_X + 2, y), 1)
        for y in (TABLE.top - 20, TABLE.bottom + 20):
            pygame.draw.circle(self.screen, (60, 60, 66), (NET_X, y), 7)
        for s in SIDES:                     # подписи половин
            r = half(s)
            label = self.font_small.render(NAMES[s], True, (255, 255, 255))
            label.set_alpha(70)
            self.screen.blit(label, label.get_rect(center=(r.centerx, r.bottom - 22)))

    def _draw_ball_shadow(self):
        b = self.ball
        shadow = pygame.Surface((40, 24), pygame.SRCALPHA)
        spread = min(1.0, b.z / 120)
        pygame.draw.ellipse(shadow, (0, 0, 0, int(120 - 60 * spread)), (4 + spread * 4, 4, 32 - spread * 8, 16))
        self.screen.blit(shadow, (b.x - 20 + b.z * 0.25, b.y - 12 + b.z * 0.2))

    def _draw_ball(self):
        b = self.ball
        sx, sy = b.x, b.y - b.z * 0.6
        if self.phase in (RALLY, POINT):
            b.trail.append((sx, sy))
        for i, (tx, ty) in enumerate(b.trail):
            k = (i + 1) / len(b.trail)
            pygame.draw.circle(self.screen, (250, 200, 120), (tx, ty), max(1, int(BALL_R * 0.6 * k)))
        r = BALL_R * (1 + b.z / 300)
        pygame.draw.circle(self.screen, (255, 250, 240), (sx, sy), r)
        pygame.draw.circle(self.screen, (255, 170, 60), (sx, sy), r, 2)

    def _draw_paddle(self, side):
        x, y = self.paddle[side]
        d = direction(side)
        if self.swing[side][2] > SERVE_SWING:          # след от быстрого взмаха
            ox, oy = self.prev_paddle[side]
            for k in (0.33, 0.66):
                gx, gy = x + (ox - x) * k * 3, y + (oy - y) * k * 3
                ghost = pygame.Surface((PADDLE_R * 2, PADDLE_R * 2), pygame.SRCALPHA)
                pygame.draw.circle(ghost, (*COLORS[side], 70), (PADDLE_R, PADDLE_R), PADDLE_R)
                self.screen.blit(ghost, (gx - PADDLE_R, gy - PADDLE_R))
        handle_x = x - PADDLE_R - 38 if d > 0 else x + PADDLE_R - 8     # ручка — за спиной у игрока
        pygame.draw.rect(self.screen, (120, 80, 44), (handle_x, y - 9, 46, 18), border_radius=6)
        pygame.draw.circle(self.screen, (0, 0, 0), (x + 3, y + 4), PADDLE_R)
        pygame.draw.circle(self.screen, COLORS[side], (x, y), PADDLE_R)
        pygame.draw.circle(self.screen, DARK[side], (x, y), PADDLE_R, 4)
        pygame.draw.circle(self.screen, [min(255, c + 60) for c in COLORS[side]], (x - 12, y - 14), 8)
        if self.bot == side:
            tag = self.font_small.render("компьютер", True, TEXT)
            self.screen.blit(tag, tag.get_rect(center=(x, y + PADDLE_R + 14)))

    def _draw_hud(self):
        cx = self.width // 2
        left = self.font_score.render(str(self.score[LEFT]), True, COLORS[LEFT])
        right = self.font_score.render(str(self.score[RIGHT]), True, COLORS[RIGHT])
        colon = self.font_score.render(":", True, TEXT)
        self.screen.blit(colon, colon.get_rect(midtop=(cx, 14)))
        self.screen.blit(left, left.get_rect(topright=(cx - 22, 14)))
        self.screen.blit(right, right.get_rect(topleft=(cx + 22, 14)))
        for s, anchor_x, align in ((LEFT, 24, "left"), (RIGHT, self.width - 24, "right")):
            name = NAMES[s] + (" (компьютер)" if self.bot == s else "")
            if self.phase == SERVE and self.server == s:
                name += "  · подача"
            txt = self.font.render(name, True, COLORS[s])
            rect = txt.get_rect(topleft=(anchor_x, 16)) if align == "left" else txt.get_rect(topright=(anchor_x, 16))
            self.screen.blit(txt, rect)
            self._draw_meter(s, rect.left if align == "left" else anchor_x - 240, 52)
        wins = f"Партии: {self.wins[LEFT]} : {self.wins[RIGHT]}"
        txt = self.font_small.render(wins, True, MUTED)
        self.screen.blit(txt, txt.get_rect(midtop=(cx, 76)))
        hint = "Взмах к сопернику — удар; сильнее взмах — сильнее удар. Подача — тоже взмахом."
        txt = self.font_small.render(hint, True, MUTED)
        self.screen.blit(txt, txt.get_rect(midtop=(cx, 104)))

    def _draw_meter(self, side, x, y):
        """Шкала силы последнего удара игрока: зелёное «окно» — мяч лёг бы на стол."""
        w, h = 240, 14
        pygame.draw.rect(self.screen, (60, 64, 80), (x, y, w, h), border_radius=4)
        hit = self.last_hit[side]
        if hit:
            v, v_min, v_max = hit

            def px(val):
                return x + int(w * min(val, MAX_BALL_SPEED) / MAX_BALL_SPEED)

            pygame.draw.rect(self.screen, (50, 130, 80), (px(v_min), y, max(2, px(v_max) - px(v_min)), h))
            pygame.draw.polygon(self.screen, WHITE, [(px(v), y + h + 1), (px(v) - 6, y + h + 10), (px(v) + 6, y + h + 10)])
        for label, lx in (("слабо", x), ("аут", x + w)):
            txt = self.font_small.render(label, True, MUTED)
            self.screen.blit(txt, txt.get_rect(topleft=(lx, y + h + 10)) if label == "слабо"
                             else txt.get_rect(topright=(lx, y + h + 10)))

    def _draw_over(self):
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))
        lines = [(f"Победил {NAMES[self.winner]}!", self.font_big, COLORS[self.winner]),
                 (f"{self.score[LEFT]} : {self.score[RIGHT]}", self.font_score, TEXT),
                 (f"Партии: {self.wins[LEFT]} : {self.wins[RIGHT]}", self.font, TEXT),
                 ("Сильный взмах или ПРОБЕЛ — новая партия", self.font_small, MUTED)]
        y = self.height // 2 - 110
        for text, font, color in lines:
            txt = font.render(text, True, color)
            self.screen.blit(txt, txt.get_rect(center=(self.width // 2, y)))
            y += 66

    def _render_background(self):
        surf = pygame.Surface((self.width, self.height))
        surf.fill(FLOOR)
        for x in range(0, self.width, 60):
            pygame.draw.line(surf, FLOOR_LINE, (x, 0), (x, self.height), 1)
        for y in range(0, self.height, 60):
            pygame.draw.line(surf, FLOOR_LINE, (0, y), (self.width, y), 1)
        return surf
