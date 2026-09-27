"""
Пинг-понг на двоих — Gesture Edition
====================================

Два игрока стоят рядом перед одной веб-камерой: игрок 1 слева, игрок 2
справа. Трекер ищет в кадре две руки (MediaPipe, num_hands=2): левая рука
в кадре — ракетка игрока 1, правая — игрока 2. Удар — взмах к сопернику
(игрок 1 — вправо, игрок 2 — влево), сила удара — скорость взмаха.

В левом нижнем углу — два окна камеры: у каждого игрока своя половина
кадра, своя рамка его цвета и свой статус («РУКА» / «НЕТ РУКИ»).

Управление:
  Рука         — вести ракетку; взмах к сопернику — удар (и подача)
  B            — за игрока 2 играет компьютер (если вы один)
  G            — игрок 1 играет мышью (резкий рывок вправо — удар)
  ПРОБЕЛ       — новая партия после окончания
  R            — начать партию заново
  K            — показать / спрятать окна камеры
  ESC          — выход
  Средний палец (показать камере и подержать) — выход
"""

import os
import sys
import time
from collections import deque

# Корень проекта — в sys.path, чтобы найти общий пакет core/ (и при запуске
# через launcher.py, и при запуске этого файла напрямую, например из PyCharm).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from core.camera_preview import CameraPreview, hand_status
from core.display import open_window
from core.exit_gesture import ExitGesture
from core.gesture_tracker import GestureTracker
from core.one_euro import OneEuroFilter2D
from game import COLORS, LEFT, OVER, RIGHT, SIDES, PingPong2PGame, direction

WINDOW_W, WINDOW_H = 1280, 800
FPS = 60

# Рука → ракетка. Кадр делится пополам: левая половина — игрок 1, правая — игрок 2.
CAM_X = {LEFT: (0.04, 0.48), RIGHT: (0.52, 0.96)}
CAM_X_SOLO = (0.12, 0.88)               # играете один против компьютера — весь кадр ваш
CAM_Y = (0.12, 0.88)
PADDLE_X = {LEFT: (30, 600), RIGHT: (680, 1250)}
PADDLE_Y = (110, 720)

CAM_MIN_CUTOFF = 1.0                    # ракетка должна успевать за взмахом — сглаживание слабое
CAM_BETA = 20.0
SWING_WINDOW = 0.08                     # сек — окно для скорости взмаха (по «сырым» кадрам)
FWD_PX = 250                            # движение руки к камере тоже добавляет силы

# Окна камеры: два рядом в левом нижнем углу, у каждого игрока своя половина кадра
PREVIEW_W, PREVIEW_H, PREVIEW_GAP = 100, 150, 8


def map_range(v, src, dst):
    k = min(max((v - src[0]) / (src[1] - src[0]), 0.0), 1.0)
    return dst[0] + (dst[1] - dst[0]) * k


class Player:
    """Рука одного игрока: сглаженная позиция ракетки и история для скорости взмаха."""

    def __init__(self, side):
        self.side = side
        self.filter = OneEuroFilter2D(CAM_MIN_CUTOFF, CAM_BETA)
        self.trail = deque(maxlen=40)
        self.paddle = (PADDLE_X[side][0] + 40 if side == LEFT else PADDLE_X[side][1] - 40, WINDOW_H / 2)
        self.detected = False

    def to_screen(self, x, y, solo):
        cam_x = CAM_X_SOLO if solo else CAM_X[self.side]
        return map_range(x, cam_x, PADDLE_X[self.side]), map_range(y, CAM_Y, PADDLE_Y)

    def feed(self, sample, solo):
        self.detected = True
        raw = self.to_screen(sample.x, sample.y, solo)
        self.trail.append((sample.t, raw[0], raw[1], sample.hand_size))
        self.paddle = self.to_screen(*self.filter(sample.x, sample.y, sample.t), solo)

    def swing(self, now):
        return measure_swing(self.trail, now, self.side)


def measure_swing(trail, now, side):
    """(скорость к сопернику, скорость вверх/вниз, сила взмаха), px/с — по последним
    SWING_WINDOW секундам траектории [(t, x, y, размер_руки)] в пикселях экрана."""
    pts = [p for p in trail if p[0] >= now - SWING_WINDOW]
    if len(pts) < 2 or now - pts[-1][0] > 0.15:
        return 0.0, 0.0, 0.0
    (t0, x0, y0, s0), (t1, x1, y1, s1) = pts[0], pts[-1]
    dt = t1 - t0
    if dt < 1e-3:
        return 0.0, 0.0, 0.0
    forward = direction(side) * (x1 - x0) / dt
    growth = (s1 - s0) / dt / ((s0 + s1) / 2) if s0 > 0 and s1 > 0 else 0.0
    return forward, (y1 - y0) / dt, max(0.0, forward) + FWD_PX * max(0.0, growth)


def assign_hands(hands, solo):
    """Какая рука чья: левая в кадре — игрок 1, правая — игрок 2.
    Одна рука — тому, на чьей половине кадра она. Вдвоём с компьютером — первая рука ваша."""
    if solo:
        return {LEFT: min(hands, key=lambda s: s.x)} if hands else {}
    hands = sorted(hands, key=lambda s: s.x)
    if len(hands) >= 2:
        return {LEFT: hands[0], RIGHT: hands[-1]}
    if hands:
        return {LEFT if hands[0].x < 0.5 else RIGHT: hands[0]}
    return {}


def main():
    pygame.init()
    screen = open_window((WINDOW_W, WINDOW_H), "Пинг-понг на двоих — Gesture Edition")
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)

    game = PingPong2PGame(screen, WINDOW_W, WINDOW_H)

    tracker = GestureTracker(cam_index=0, num_hands=2)
    tracker.start()
    top = WINDOW_H - PREVIEW_H - 8
    previews = {
        LEFT: CameraPreview(tracker, WINDOW_H, rect=pygame.Rect(8, top, PREVIEW_W, PREVIEW_H),
                            crop_x=(0.0, 0.5), border=COLORS[LEFT]),
        RIGHT: CameraPreview(tracker, WINDOW_H, rect=pygame.Rect(8 + PREVIEW_W + PREVIEW_GAP, top, PREVIEW_W, PREVIEW_H),
                             crop_x=(0.5, 1.0), border=COLORS[RIGHT]),
    }
    exit_gesture = ExitGesture()
    players = {s: Player(s) for s in SIDES}
    last_frame_t = 0.0
    use_mouse = False
    mouse_trail = deque(maxlen=40)

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        now = time.time()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_b:
                    game.set_bot(None if game.bot else RIGHT)
                    # один против компьютера — окно игрока 1 показывает весь кадр
                    previews[LEFT].crop_x = (0.0, 1.0) if game.bot else (0.0, 0.5)
                    previews[LEFT].set_tracker(tracker)
                elif event.key == pygame.K_g:
                    use_mouse = not use_mouse
                    mouse_trail.clear()
                elif event.key == pygame.K_k:
                    for p in previews.values():
                        p.toggle()
                elif event.key == pygame.K_r:
                    game.reset_match()
                elif event.key == pygame.K_SPACE and game.phase == OVER:
                    game.reset_match()

        solo = game.bot is not None
        for frame_t, hands in tracker.get_hands_since(last_frame_t):
            last_frame_t = frame_t
            owners = assign_hands(hands, solo)
            for side, player in players.items():
                if side in owners:
                    player.feed(owners[side], solo)
                else:
                    player.detected = False

        for side, player in players.items():
            if side == game.bot:
                continue                                # ракеткой двигает компьютер
            if side == LEFT and use_mouse:
                mx, my = pygame.mouse.get_pos()
                pos = (min(max(mx, PADDLE_X[LEFT][0]), PADDLE_X[LEFT][1]), min(max(my, PADDLE_Y[0]), PADDLE_Y[1]))
                mouse_trail.append((now, pos[0], pos[1], 0.0))
                game.set_paddle(side, pos, measure_swing(mouse_trail, now, side))
            else:
                game.set_paddle(side, player.paddle, player.swing(now))

        game.update(dt)
        game.draw()

        mode = "МЫШЬ" if use_mouse else "ЖЕСТЫ"
        bot = "компьютер" if game.bot else "человек"
        info = f"Игрок 1: {mode}, игрок 2: {bot}   (B — компьютер за игрока 2, G — мышь, R — заново, K — камера, ESC — выход)"
        txt = font_small.render(info, True, (150, 158, 178))
        screen.blit(txt, (WINDOW_W - txt.get_width() - 14, WINDOW_H - 28))

        for side, preview in previews.items():
            if side == game.bot:
                continue                                # за этого игрока играет компьютер — окно не нужно
            preview.draw(screen, hand_status(players[side].detected, False))
        if exit_gesture.update(dt, tracker):
            running = False
        exit_gesture.draw(screen)
        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
