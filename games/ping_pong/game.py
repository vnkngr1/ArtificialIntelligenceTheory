"""
Пинг-понг против компьютера на Pygame (вид сверху).

Стол вертикально: игрок снизу, компьютер сверху. У мяча кроме положения
на столе (x, y) есть высота z — он летит дугой над сеткой, отскакивает от
стола и отбрасывает тень. Поэтому сила удара работает как в жизни:
  - слабо — мяч не перелетает сетку или падает на своей стороне;
  - в самый раз — падает на половину соперника;
  - слишком сильно — перелетает стол (аут).
Направление — от места, которым ракетка встретила мяч (край ракетки уводит
мяч вбок), и от бокового движения руки во время взмаха.

Правила упрощены: мяч после удара должен отскочить на половине соперника;
второй отскок там же или пропущенный мяч — очко ударившему; аут, сетка или
отскок на своей половине — очко сопернику. Бить с лёта можно. Подачи — по
две, при 10:10 — по одной; партия до 11 с разницей в 2 очка. Выиграли
партию — следующий уровень: компьютер быстрее и точнее.

Игра ничего не знает про камеру: каждый кадр ей передают положение ракетки
и скорость взмаха — set_paddle(pos, swing).
"""

import math
import random
from collections import deque

import pygame

# ---------- стол ----------

TABLE = pygame.Rect(290, 90, 380, 680)      # вид сверху, длинная сторона — вертикально
NET_Y = TABLE.centery
NET_H = 18                                  # высота сетки (в тех же единицах, что z мяча)
BALL_R = 9
PADDLE_R = 44
AI_BASE_Y = TABLE.top - 14                  # где обычно стоит ракетка компьютера

# ---------- физика ----------

G = 1500.0                  # гравитация, px/с²
HIT_Z = 40.0                # высота мяча в момент удара
VZ_HIT = 380.0              # вертикальная скорость после удара — дуга над сеткой
BOUNCE = 0.75               # упругость отскока от стола (по вертикали)
TABLE_FRICTION = 0.93       # потеря горизонтальной скорости при отскоке
SUBSTEPS = 3

# ---------- удар игрока ----------

SWING_GAIN = 1.0            # скорость мяча на 1 px/с взмаха
INCOMING_KEEP = 0.35        # доля скорости прилетевшего мяча, которая остаётся при ударе
MIN_BALL_SPEED, MAX_BALL_SPEED = 120.0, 1700.0
EDGE_ANGLE = 12             # град: удар краем ракетки
SIDE_ANGLE = 20             # град: боковое движение руки
AIM_ASSIST = 0.7            # доля доворота к центру половины соперника: камера не видит наклон
                            # ракетки, и без доворота прямой взмах от края стола уходил бы в аут вбок
SERVE_SWING = 300           # px/с — взмах, с которого начинается подача

# ---------- матч ----------

POINTS_TO_WIN = 11
POINT_PAUSE = 1.4
AI_SERVE_DELAY = 1.1

# ---------- цвета ----------

FLOOR = (38, 42, 52)
FLOOR_LINE = (46, 51, 63)
TABLE_BLUE = (24, 88, 168)
TABLE_EDGE = (14, 58, 118)
WHITE = (245, 245, 245)
TEXT = (238, 240, 246)
MUTED = (150, 158, 178)
GOOD = (110, 225, 140)
BAD = (255, 120, 110)

SERVE, RALLY, POINT, OVER = "serve", "rally", "point", "over"
PLAYER, AI = "player", "ai"


def other(side):
    return AI if side == PLAYER else PLAYER


def flight_time(z0=HIT_Z, vz=VZ_HIT, z_end=0.0):
    """Сколько летит мяч после удара, пока не опустится до высоты z_end."""
    return (vz + math.sqrt(vz * vz + 2 * G * (z0 - z_end))) / G


def ai_skill(level):
    """Параметры компьютера на уровне level (с 1)."""
    return {
        "speed": 260 + 50 * level,                  # px/с — как быстро двигает ракетку
        "reaction": max(0.06, 0.30 - 0.03 * level),  # с — задержка, прежде чем побежать к мячу
        "predict_err": max(6, 36 - 4 * level),       # px — ошибка в оценке, куда прилетит мяч
        "aim_err": max(12, 52 - 5 * level),          # px — разброс его ударов
        "handle": 650 + 90 * level,                  # px/с — быстрее этого мяча начинает ошибаться
        "blunder": max(0.03, 0.14 - 0.012 * level),  # доля ударов с грубой ошибкой — иначе
                                                     # розыгрыши на высоких уровнях не заканчиваются
    }


class Ball:
    def __init__(self):
        self.x, self.y, self.z = TABLE.centerx, TABLE.bottom, HIT_Z
        self.vx = self.vy = self.vz = 0.0
        self.trail = deque(maxlen=10)

    def copy(self):
        b = Ball()
        b.x, b.y, b.z, b.vx, b.vy, b.vz = self.x, self.y, self.z, self.vx, self.vy, self.vz
        return b

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)


class PingPongGame:
    def __init__(self, screen, width, height):
        self.screen = screen
        self.width = width
        self.height = height
        self.font_small = pygame.font.SysFont("arial", 16)
        self.font = pygame.font.SysFont("arial", 21, bold=True)
        self.font_score = pygame.font.SysFont("arial", 46, bold=True)
        self.font_big = pygame.font.SysFont("arial", 50, bold=True)
        self.background = self._render_background()
        self.level = 1
        self.best_level = 1
        self.paddle = (TABLE.centerx, TABLE.bottom + 20)
        self.prev_paddle = self.paddle
        self.swing = (0.0, 0.0, 0.0)        # vx, vy (px/с), сила взмаха «к сопернику»
        self.reset_match()

    # ---------- партия ----------

    def reset_match(self):
        self.now = 0.0
        self.score = {PLAYER: 0, AI: 0}
        self.skill = ai_skill(self.level)
        self.ball = Ball()
        self.ai_pos = [TABLE.centerx, AI_BASE_Y]
        self.ai_target = (TABLE.centerx, AI_BASE_Y)
        self.ai_move_at = 0.0
        self.popups = []
        self.last_hit = None                # (скорость мяча, мин. и макс. «в стол») — для шкалы
        self.winner = None
        self._new_serve()

    def next_game(self):
        if self.winner == PLAYER:
            self.level += 1
            self.best_level = max(self.best_level, self.level)
        self.reset_match()

    @property
    def server(self):
        total = self.score[PLAYER] + self.score[AI]
        if min(self.score.values()) >= POINTS_TO_WIN - 1:
            turn = total                    # при 10:10 подача меняется каждое очко
        else:
            turn = total // 2
        return PLAYER if turn % 2 == 0 else AI

    def _new_serve(self):
        self.phase = SERVE
        self.phase_since = self.now
        self.hitter = None
        self.bounced_on = None
        self.net_checked = False
        self.ball.trail.clear()
        if self.server == AI:
            self.ai_pos = [TABLE.centerx + random.uniform(-100, 100), AI_BASE_Y]

    # ---------- ввод ----------

    def set_paddle(self, pos, swing):
        """pos — где ракетка игрока; swing — (vx, vy, сила взмаха к сопернику), px/с."""
        self.prev_paddle = self.paddle
        self.paddle = (float(pos[0]), float(pos[1]))
        self.swing = swing

    def swing_power(self):
        return self.swing[2]

    # ---------- логика ----------

    def update(self, dt):
        self.now += dt
        if self.phase == SERVE:
            self._update_serve()
        elif self.phase == OVER:
            if self.now - self.phase_since > 1.5 and self.swing_power() > SERVE_SWING * 1.5:
                self.next_game()                # взмах — следующая партия
            return

        if self.phase in (RALLY, POINT):
            h = dt / SUBSTEPS
            (ox, oy), (px, py) = self.prev_paddle, self.paddle
            for i in range(SUBSTEPS):
                # ракетка за кадр сдвигается один раз — внутри кадра ведём её плавно
                k0, k1 = i / SUBSTEPS, (i + 1) / SUBSTEPS
                self._step_ball(h, (ox + (px - ox) * k0, oy + (py - oy) * k0),
                                (ox + (px - ox) * k1, oy + (py - oy) * k1))
            if self.phase == POINT and self.now - self.phase_since > POINT_PAUSE:
                if self.winner:
                    self.phase = OVER
                    self.phase_since = self.now
                else:
                    self._new_serve()

        self._update_ai(dt)
        self.popups = [p for p in self.popups if self.now - p["t0"] < 1.3]

    def _update_serve(self):
        b = self.ball
        b.vx = b.vy = b.vz = 0.0
        b.z = HIT_Z
        if self.server == PLAYER:
            # мяч висит над ракеткой; взмах к сопернику — подача
            b.x, b.y = self.paddle[0], self.paddle[1] - PADDLE_R - BALL_R
            if self.now - self.phase_since > 0.4 and self.swing_power() > SERVE_SWING:
                self._player_hit(serve=True)
        else:
            b.x, b.y = self.ai_pos[0], self.ai_pos[1] + PADDLE_R * 0.6
            if self.now - self.phase_since > AI_SERVE_DELAY:
                self._ai_hit(serve=True)

    def _step_ball(self, h, pad0, pad1):
        b = self.ball
        px, py = b.x, b.y
        b.vz -= G * h
        b.x += b.vx * h
        b.y += b.vy * h
        b.z += b.vz * h
        live = self.phase == RALLY

        # сетка: мяч пересёк середину стола слишком низко
        if live and not self.net_checked and (py - NET_Y) * (b.y - NET_Y) <= 0:
            self.net_checked = True
            if b.z < NET_H and TABLE.left - 20 <= b.x <= TABLE.right + 20:
                b.vy *= -0.15
                b.vx *= 0.3
                self._point(other(self.hitter), "Сетка")

        if b.z <= 0:
            if TABLE.collidepoint(b.x, b.y):
                b.z = 0.0
                b.vz = -b.vz * BOUNCE
                b.vx *= TABLE_FRICTION
                b.vy *= TABLE_FRICTION
                if live:
                    self._bounce(PLAYER if b.y > NET_Y else AI)
            else:
                b.z = 0.0
                b.vz = -b.vz * 0.4
                b.vx *= 0.5
                b.vy *= 0.5
                if live:
                    self._floor()

        if live:
            self._check_player_hit((px, py), pad0, pad1)
            self._check_ai_hit()
            if not (-80 < b.y < self.height + 80 and -80 < b.x < self.width + 80):
                self._floor()

    def _bounce(self, side):
        if self.bounced_on is None:
            if side == self.hitter:
                self._point(other(self.hitter), "Не перелетел")
            else:
                self.bounced_on = side
                if side == AI:
                    self._plan_ai()
        else:
            self._point(self.hitter, "Два отскока")

    def _floor(self):
        if self.phase != RALLY:
            return
        if self.bounced_on is None:
            self._point(other(self.hitter), "Аут")
        else:
            self._point(self.hitter, "Не достал")

    def _point(self, winner, reason):
        if self.phase != RALLY:
            return
        self.score[winner] += 1
        self.phase = POINT
        self.phase_since = self.now
        if winner == PLAYER:
            text = {"Не достал": "Компьютер не достал!", "Два отскока": "Компьютер не успел!"}.get(
                reason, f"{reason} у компьютера")
            self._popup(f"+1  {text}", GOOD)
        else:
            text = {"Не достал": "Пропустили", "Два отскока": "Пропустили"}.get(reason, reason)
            self._popup(text, BAD)
        s, a = self.score[PLAYER], self.score[AI]
        if max(s, a) >= POINTS_TO_WIN and abs(s - a) >= 2:
            self.winner = PLAYER if s > a else AI

    # ---------- удар игрока ----------

    def _check_player_hit(self, ball0, pad0, pad1):
        """Ракетка встретила мяч: проверяем по отрезку движения мяча относительно ракетки,
        чтобы быстрый мяч или резкий взмах не «проскакивали» между кадрами."""
        b = self.ball
        if self.hitter != AI or b.vy <= 0 or b.y < NET_Y:
            return
        ax, ay = ball0[0] - pad0[0], ball0[1] - pad0[1]       # мяч относительно ракетки: было
        bx, by = b.x - pad1[0], b.y - pad1[1]                 # и стало
        dx, dy = bx - ax, by - ay
        k = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        if math.hypot(ax + dx * k, ay + dy * k) <= PADDLE_R + BALL_R:
            self._player_hit()

    def _player_hit(self, serve=False):
        b = self.ball
        vx_hand, _, power = self.swing
        # «окно» скоростей, при которых мяч лёг бы на стол соперника (для шкалы силы)
        v_min = (b.y - NET_Y) / flight_time(z_end=NET_H)
        v_max = (b.y - TABLE.top) / flight_time()
        side = max(-1.0, min(1.0, vx_hand / 900))
        if serve:
            # подача всегда в стол: сильнее взмах — глубже, взмах вбок — ближе к углу
            k = max(0.1, min(0.9, (power - SERVE_SWING) / 1200))
            ty = NET_Y - (NET_Y - TABLE.top) * (0.25 + 0.6 * k)
            tx = min(max(TABLE.centerx + side * TABLE.width * 0.35, TABLE.left + 30), TABLE.right - 30)
            v = math.hypot(tx - b.x, b.y - ty) / flight_time()
            angle = math.atan2(tx - b.x, b.y - ty)
        else:
            v = INCOMING_KEEP * b.speed + SWING_GAIN * power
            v = min(MAX_BALL_SPEED, max(MIN_BALL_SPEED, v))
            offset = max(-1.0, min(1.0, (b.x - self.paddle[0]) / PADDLE_R))
            to_center = math.atan2(TABLE.centerx - b.x, b.y - (TABLE.top + NET_Y) / 2)
            angle = AIM_ASSIST * to_center + math.radians(offset * EDGE_ANGLE + side * SIDE_ANGLE)
        self._launch(v, angle, PLAYER)
        self.last_hit = (v, v_min, v_max)
        if not serve:
            label = "Слабо" if v < v_min else "Сильно!" if v > v_max else "Хороший удар"
            color = BAD if v < v_min or v > v_max else GOOD
            self._popup(label, color, pos=(b.x, b.y - 40), small=True)
        self._plan_ai()

    def _launch(self, v, angle, hitter):
        b = self.ball
        direction = -1 if hitter == PLAYER else 1
        b.vx = v * math.sin(angle)
        b.vy = direction * v * math.cos(angle)
        b.z, b.vz = HIT_Z, VZ_HIT
        self.hitter = hitter
        self.bounced_on = None
        self.net_checked = False
        self.phase = RALLY

    # ---------- компьютер ----------

    def _plan_ai(self):
        """Куда бежать компьютеру: симулируем полёт мяча до точки, где его удобно отбить."""
        skill = self.skill
        target = (TABLE.centerx, AI_BASE_Y)
        if self.hitter == PLAYER:
            sim = self.ball.copy()
            bounced = self.bounced_on == AI
            for _ in range(int(3.0 * 120)):
                h = 1 / 120
                sim.vz -= G * h
                sim.x += sim.vx * h
                sim.y += sim.vy * h
                sim.z += sim.vz * h
                if sim.z <= 0:
                    if not TABLE.collidepoint(sim.x, sim.y) or sim.y > NET_Y or bounced:
                        break                    # аут / своя сторона / второй отскок — отбивать нечего
                    sim.z, sim.vz = 0.0, -sim.vz * BOUNCE
                    sim.vx *= TABLE_FRICTION
                    sim.vy *= TABLE_FRICTION
                    bounced = True
                elif bounced and (sim.y <= AI_BASE_Y or (sim.vz < 0 and sim.z < 25)):
                    target = (sim.x + random.gauss(0, skill["predict_err"]), max(sim.y, AI_BASE_Y - 30))
                    break
        self.ai_target = target
        self.ai_move_at = self.now + skill["reaction"]

    def _update_ai(self, dt):
        if self.phase == SERVE and self.server == AI:
            return
        if self.now < self.ai_move_at:
            return
        tx, ty = self.ai_target
        dx, dy = tx - self.ai_pos[0], ty - self.ai_pos[1]
        dist = math.hypot(dx, dy)
        step = self.skill["speed"] * dt
        if dist > 0:
            k = min(1.0, step / dist)
            self.ai_pos[0] += dx * k
            self.ai_pos[1] += dy * k

    def _check_ai_hit(self):
        b = self.ball
        if self.hitter != PLAYER or self.bounced_on != AI or b.vy >= 0:
            return
        if math.hypot(b.x - self.ai_pos[0], b.y - self.ai_pos[1]) <= PADDLE_R + BALL_R + 6:
            self._ai_hit()

    def _ai_hit(self, serve=False):
        """Компьютер целится в точку на половине игрока; быстрые мячи он отбивает хуже."""
        b = self.ball
        skill = self.skill
        err = skill["aim_err"]
        if not serve and b.speed > skill["handle"]:
            err *= 1 + (b.speed - skill["handle"]) / 250     # не успевает — бьёт неточно
        if not serve and random.random() < skill["blunder"]:
            err = max(err * 4, 140)                           # просто ошибся — грубо, мимо стола или в сетку
        depth = min(0.85, 0.45 + 0.05 * self.level)
        tx = random.uniform(TABLE.left + 40, TABLE.right - 40) + random.gauss(0, err)
        ty = NET_Y + (TABLE.bottom - NET_Y) * random.uniform(0.35, depth) + random.gauss(0, err)
        if serve:
            b.x, b.y = self.ai_pos[0], self.ai_pos[1] + PADDLE_R * 0.6
        dist = math.hypot(tx - b.x, ty - b.y)
        v = dist / flight_time()
        angle = math.atan2(tx - b.x, ty - b.y)
        self._launch(v, angle, AI)
        self.ai_target = (TABLE.centerx + random.uniform(-40, 40), AI_BASE_Y)   # вернуться в центр
        self.ai_move_at = self.now + 0.2

    # ---------- отрисовка ----------

    def _popup(self, text, color, pos=None, small=False):
        self.popups.append({"text": text, "color": color, "t0": self.now, "small": small,
                            "pos": pos or (TABLE.centerx, NET_Y + (60 if color == BAD else -60))})

    def draw(self):
        self.screen.blit(self.background, (0, 0))
        self._draw_table()
        self._draw_ai_paddle()
        self._draw_ball()
        self._draw_player_paddle()
        for p in self.popups:
            age = (self.now - p["t0"]) / 1.3
            font = self.font if p["small"] else self.font_big
            txt = font.render(p["text"], True, p["color"])
            txt.set_alpha(int(255 * min(1.0, 2 * (1 - age))))
            shadow = font.render(p["text"], True, (0, 0, 0))
            shadow.set_alpha(txt.get_alpha())
            x, y = p["pos"]
            rect = txt.get_rect(center=(x, y - 30 * age))
            self.screen.blit(shadow, rect.move(2, 2))
            self.screen.blit(txt, rect)
        self._draw_panel()
        if self.phase == OVER:
            self._draw_over()

    def _draw_table(self):
        pygame.draw.rect(self.screen, (20, 22, 28), TABLE.move(8, 10), border_radius=4)   # тень
        pygame.draw.rect(self.screen, TABLE_BLUE, TABLE)
        pygame.draw.rect(self.screen, TABLE_EDGE, TABLE, 3)
        inner = TABLE.inflate(-10, -10)
        pygame.draw.rect(self.screen, WHITE, inner, 4)
        pygame.draw.line(self.screen, WHITE, (TABLE.centerx, inner.top), (TABLE.centerx, inner.bottom), 2)
        # сетка со стойками
        pygame.draw.line(self.screen, (10, 10, 14), (TABLE.left - 18, NET_Y + 5), (TABLE.right + 18, NET_Y + 5), 6)
        pygame.draw.line(self.screen, (225, 225, 230), (TABLE.left - 18, NET_Y), (TABLE.right + 18, NET_Y), 5)
        for x in range(TABLE.left - 12, TABLE.right + 14, 12):
            pygame.draw.line(self.screen, (160, 160, 170), (x, NET_Y - 2), (x, NET_Y + 2), 1)
        for x in (TABLE.left - 20, TABLE.right + 20):
            pygame.draw.circle(self.screen, (60, 60, 66), (x, NET_Y), 7)

    def _draw_ball(self):
        b = self.ball
        # тень на столе / полу — там, где мяч над поверхностью
        shadow = pygame.Surface((40, 24), pygame.SRCALPHA)
        spread = min(1.0, b.z / 120)
        pygame.draw.ellipse(shadow, (0, 0, 0, int(120 - 60 * spread)), (4 + spread * 4, 4, 32 - spread * 8, 16))
        self.screen.blit(shadow, (b.x - 20 + b.z * 0.25, b.y - 12 + b.z * 0.2))
        sx, sy = b.x, b.y - b.z * 0.6              # мяч — выше своей тени
        if self.phase in (RALLY, POINT):
            b.trail.append((sx, sy))
        for i, (tx, ty) in enumerate(b.trail):
            k = (i + 1) / len(b.trail)
            pygame.draw.circle(self.screen, (250, 200, 120), (tx, ty), max(1, int(BALL_R * 0.6 * k)))
        r = BALL_R * (1 + b.z / 300)
        pygame.draw.circle(self.screen, (255, 250, 240), (sx, sy), r)
        pygame.draw.circle(self.screen, (255, 170, 60), (sx, sy), r, 2)

    def _draw_player_paddle(self):
        x, y = self.paddle
        power = self.swing_power()
        if power > SERVE_SWING:                     # след от быстрого взмаха
            ox, oy = self.prev_paddle
            for k in (0.33, 0.66):
                gx, gy = x + (ox - x) * k * 3, y + (oy - y) * k * 3
                ghost = pygame.Surface((PADDLE_R * 2, PADDLE_R * 2), pygame.SRCALPHA)
                pygame.draw.circle(ghost, (220, 50, 50, 70), (PADDLE_R, PADDLE_R), PADDLE_R)
                self.screen.blit(ghost, (gx - PADDLE_R, gy - PADDLE_R))
        pygame.draw.rect(self.screen, (120, 80, 44), (x - 9, y + PADDLE_R - 8, 18, 46), border_radius=6)
        pygame.draw.circle(self.screen, (0, 0, 0), (x + 3, y + 4), PADDLE_R)
        pygame.draw.circle(self.screen, (210, 40, 44), (x, y), PADDLE_R)
        pygame.draw.circle(self.screen, (150, 24, 28), (x, y), PADDLE_R, 4)
        pygame.draw.circle(self.screen, (235, 90, 90), (x - 12, y - 14), 8)

    def _draw_ai_paddle(self):
        x, y = self.ai_pos
        pygame.draw.rect(self.screen, (120, 80, 44), (x - 9, y - PADDLE_R - 38, 18, 46), border_radius=6)
        pygame.draw.circle(self.screen, (0, 0, 0), (x + 3, y + 4), PADDLE_R)
        pygame.draw.circle(self.screen, (34, 34, 38), (x, y), PADDLE_R)
        pygame.draw.circle(self.screen, (70, 70, 78), (x, y), PADDLE_R, 4)

    def _draw_panel(self):
        x = 22
        self.screen.blit(self.font_big.render("Пинг-понг", True, TEXT), (x, 18))
        self.screen.blit(self.font_small.render(f"Уровень {self.level}  ·  лучший {self.best_level}",
                                                True, MUTED), (x, 80))
        y = 118
        for side, name in ((PLAYER, "Вы"), (AI, "Компьютер")):
            color = GOOD if side == PLAYER else (200, 205, 220)
            self.screen.blit(self.font.render(name, True, color), (x, y + 12))
            score = self.font_score.render(str(self.score[side]), True, TEXT)
            self.screen.blit(score, score.get_rect(topright=(x + 230, y)))
            if self.phase in (SERVE, RALLY) and self.server == side and self.phase == SERVE:
                pygame.draw.circle(self.screen, (255, 250, 240), (x + 160, y + 26), 7)
            y += 58
        serve = "Ваша подача" if self.server == PLAYER else "Подаёт компьютер"
        self.screen.blit(self.font_small.render(serve, True, MUTED), (x, y + 4))

        # шкала: сила последнего удара и «окно», при котором мяч лёг бы на стол
        y += 50
        self.screen.blit(self.font_small.render("Сила последнего удара:", True, MUTED), (x, y))
        y += 24
        w, h = 240, 18
        pygame.draw.rect(self.screen, (60, 64, 80), (x, y, w, h), border_radius=5)
        if self.last_hit:
            v, v_min, v_max = self.last_hit

            def px(val):
                return x + int(w * min(val, MAX_BALL_SPEED) / MAX_BALL_SPEED)

            pygame.draw.rect(self.screen, (50, 130, 80), (px(v_min), y, max(2, px(v_max) - px(v_min)), h))
            pygame.draw.polygon(self.screen, WHITE, [(px(v), y + h + 2), (px(v) - 7, y + h + 13), (px(v) + 7, y + h + 13)])
        self.screen.blit(self.font_small.render("слабо", True, MUTED), (x, y + h + 14))
        strong = self.font_small.render("аут", True, MUTED)
        self.screen.blit(strong, (x + w - strong.get_width(), y + h + 14))

        y += h + 52
        if self.phase == SERVE and self.server == PLAYER:
            hint = "Подача: резко взмахните рукой вверх — к сопернику."
        else:
            hint = ("Ракетка там, где ваша рука. Отбивайте взмахом вверх, к сопернику: "
                    "сильнее взмах — сильнее удар. Край ракетки и движение руки вбок уводят мяч в сторону.")
        for line in self._wrap(hint, self.font_small, 250):
            self.screen.blit(self.font_small.render(line, True, TEXT), (x, y))
            y += 21

    def _draw_over(self):
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))
        won = self.winner == PLAYER
        lines = [("ПОБЕДА!" if won else "ПОРАЖЕНИЕ", self.font_big, GOOD if won else BAD),
                 (f"{self.score[PLAYER]} : {self.score[AI]}", self.font_score, TEXT),
                 (f"Следующая партия — уровень {self.level + 1}" if won else f"Ещё раз — уровень {self.level}",
                  self.font, TEXT),
                 ("Сильный взмах или ПРОБЕЛ — продолжить,  R — переиграть уровень", self.font_small, MUTED)]
        y = self.height // 2 - 110
        for text, font, color in lines:
            txt = font.render(text, True, color)
            self.screen.blit(txt, txt.get_rect(center=(self.width // 2, y)))
            y += 66

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

    def _render_background(self):
        surf = pygame.Surface((self.width, self.height))
        surf.fill(FLOOR)
        for x in range(0, self.width, 60):
            pygame.draw.line(surf, FLOOR_LINE, (x, 0), (x, self.height), 1)
        for y in range(0, self.height, 60):
            pygame.draw.line(surf, FLOOR_LINE, (0, y), (self.width, y), 1)
        return surf
