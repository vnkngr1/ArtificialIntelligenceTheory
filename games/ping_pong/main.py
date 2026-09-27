"""
Пинг-понг — Gesture Edition
===========================

Точка входа пинг-понга против компьютера. Ракетка — там, где ваша рука
(точка между большим и указательным пальцами), удар — взмах рукой вверх,
к сопернику: чем быстрее взмах, тем сильнее удар. Движение руки к камере
тоже добавляет силы. Слабый удар не перелетит сетку, слишком сильный уйдёт
в аут — шкала слева показывает, попали ли вы в «окно».

Управление:
  Рука    — вести ракетку; взмах вверх — удар (и подача)
  G       — режим ЖЕСТЫ / МЫШЬ (мышь: ведите ракетку, резкий рывок вверх — удар)
  ПРОБЕЛ  — следующая партия после окончания
  R       — начать партию заново
  K       — показать / спрятать окно камеры
  ESC     — выход
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
from game import NET_Y, OVER, PingPongGame

WINDOW_W, WINDOW_H = 960, 860
FPS = 60

# Рука → ракетка: центральная часть кадра камеры → половина стола игрока (с запасом по бокам)
CAM_X = (0.12, 0.88)
CAM_Y = (0.22, 0.90)
PADDLE_X = (140, 820)
PADDLE_Y = (NET_Y + 25, WINDOW_H - 15)

# Ракетка должна успевать за взмахом — сглаживание слабее, чем у курсоров в других играх
CAM_MIN_CUTOFF = 1.0
CAM_BETA = 20.0

SWING_WINDOW = 0.08     # сек — по такому окну меряем скорость взмаха (по «сырым» кадрам, без фильтра)
FWD_PX = 250            # движение руки к камере: рост руки в кадре 1/с → px/с силы удара


def map_range(v, src, dst):
    k = min(max((v - src[0]) / (src[1] - src[0]), 0.0), 1.0)
    return dst[0] + (dst[1] - dst[0]) * k


def to_screen(x, y):
    return map_range(x, CAM_X, PADDLE_X), map_range(y, CAM_Y, PADDLE_Y)


def measure_swing(trail, now):
    """Скорость взмаха по последним SWING_WINDOW секундам: (vx, vy, сила к сопернику), px/с.
    trail — [(t, x, y, размер_руки)] в пикселях экрана."""
    pts = [p for p in trail if p[0] >= now - SWING_WINDOW]
    if len(pts) < 2 or now - pts[-1][0] > 0.15:
        return 0.0, 0.0, 0.0
    (t0, x0, y0, s0), (t1, x1, y1, s1) = pts[0], pts[-1]
    dt = t1 - t0
    if dt < 1e-3:
        return 0.0, 0.0, 0.0
    vx, vy = (x1 - x0) / dt, (y1 - y0) / dt
    growth = (s1 - s0) / dt / ((s0 + s1) / 2) if s0 > 0 and s1 > 0 else 0.0
    return vx, vy, max(0.0, -vy) + FWD_PX * max(0.0, growth)


def main():
    pygame.init()
    screen = open_window((WINDOW_W, WINDOW_H), "Пинг-понг — Gesture Edition")
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)

    game = PingPongGame(screen, WINDOW_W, WINDOW_H)

    tracker = GestureTracker(cam_index=0)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    exit_gesture = ExitGesture()
    hand_filter = OneEuroFilter2D(CAM_MIN_CUTOFF, CAM_BETA)
    trail = deque(maxlen=40)
    last_sample_t = 0.0
    paddle = (WINDOW_W / 2, PADDLE_Y[1] - 60)
    hand_detected = False

    use_gesture = True
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_g:
                    use_gesture = not use_gesture
                    trail.clear()
                elif event.key == pygame.K_k:
                    preview.toggle()
                elif event.key == pygame.K_r:
                    game.reset_match()
                elif event.key == pygame.K_SPACE and game.phase == OVER:
                    game.next_game()

        now = time.time()
        if use_gesture:
            # каждый кадр камеры по порядку — для точной скорости взмаха
            for s in tracker.get_samples_since(last_sample_t):
                last_sample_t = s.t
                hand_detected = s.detected
                if s.detected:
                    raw = to_screen(s.x, s.y)
                    trail.append((s.t, raw[0], raw[1], s.hand_size))
                    paddle = to_screen(*hand_filter(s.x, s.y, s.t))
            swing = measure_swing(trail, now)
        else:
            mx, my = pygame.mouse.get_pos()
            paddle = (min(max(mx, PADDLE_X[0]), PADDLE_X[1]), min(max(my, PADDLE_Y[0]), PADDLE_Y[1]))
            trail.append((now, paddle[0], paddle[1], 0.0))
            swing = measure_swing(trail, now)

        game.set_paddle(paddle, swing)
        game.update(dt)
        game.draw()

        mode_txt = f"Режим: {'ЖЕСТЫ' if use_gesture else 'МЫШЬ'}  (G — сменить, R — заново, K — камера, ESC — выход)"
        txt = font_small.render(mode_txt, True, (150, 158, 178))
        screen.blit(txt, (WINDOW_W - txt.get_width() - 14, WINDOW_H - 28))
        if use_gesture and not hand_detected:
            warn = font_small.render("Рука не найдена в кадре камеры", True, (255, 120, 120))
            screen.blit(warn, (WINDOW_W - warn.get_width() - 14, WINDOW_H - 50))

        preview.draw(screen, hand_status(hand_detected, False) if use_gesture else None)
        if exit_gesture.update(dt, tracker):
            running = False
        exit_gesture.draw(screen)
        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
