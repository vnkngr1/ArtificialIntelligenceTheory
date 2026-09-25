"""
Дартс — Gesture Edition
=======================

Точка входа игры в дартс. Запускает окно Pygame и, параллельно в отдельном
потоке, тот же трекер жестов на OpenCV/MediaPipe, что и Block Blast.

Как бросать:
  1. Прицел: щипок (большой + указательный) и движение руки двигают
     перекрестие; разжатие пальцев фиксирует точку на мишени.
  2. Бросок: снова щипок — дротик «в руке». Резкое движение руки вперёд
     (к камере) и вверх, пальцы разжимаются в конце движения — дротик летит.
     Сила и направление берутся из скорости руки в момент разжатия.

Управление:
  G       — переключить режим ЖЕСТЫ / МЫШЬ (мышь: зажать ЛКМ = щипок,
            бросок — резкий взмах мышью вверх с отпусканием кнопки)
  A       — перенацелиться (вернуться к шагу 1)
  R       — начать заново
  ESC     — выход
"""

import sys
import time
from collections import deque

import pygame

from darts_game import BOARD_AREA, DartsGame, ThrowMotion
from gesture_tracker import GestureTracker

WINDOW_W, WINDOW_H = 1100, 760
FPS = 60

# Щипок определяем по отношению «расстояние между пальцами / размер руки»:
# в отличие от абсолютного порога, оно не меняется, когда рука при броске
# приближается к камере. Два порога (гистерезис) — чтобы щипок не «мигал».
PINCH_CLOSE_RATIO = 0.30
PINCH_OPEN_RATIO = 0.45

CAM_SMOOTHING = 0.5     # сглаживание курсора для прицеливания (0..1)
CAM_MARGIN = 0.12       # края кадра, до которых рука дотягивается с трудом
MOTION_WINDOW = 0.18    # за сколько секунд до разжатия меряем скорость броска


def estimate_motion(trail):
    """Скорость руки по последним MOTION_WINDOW секундам траектории [(t, x, y, size)]."""
    if len(trail) < 2:
        return ThrowMotion()
    t_end = trail[-1][0]
    pts = [p for p in trail if p[0] >= t_end - MOTION_WINDOW]
    if len(pts) < 2:
        pts = list(trail)[-2:]
    (t0, x0, y0, s0), (t1, x1, y1, s1) = pts[0], pts[-1]
    dt = t1 - t0
    if dt < 1e-3:
        return ThrowMotion()
    mean_size = (s0 + s1) / 2
    v_fwd = (s1 - s0) / dt / mean_size if mean_size > 1e-6 else 0.0
    return ThrowMotion(vx=(x1 - x0) / dt, vy=(y1 - y0) / dt, v_fwd=v_fwd)


class PinchInput:
    """Превращает поток кадров (с камеры или от мыши) в курсор, состояние щипка
    и события press / drag / release. Пока пальцы сжаты, пишет траекторию руки,
    по которой потом оценивается скорость броска."""

    def __init__(self, smoothing):
        self.smoothing = smoothing
        self.x = self.y = 0.5          # сглаженная позиция, 0..1
        self.pinching = False
        self.detected = False
        self.trail = deque(maxlen=90)

    def feed(self, t, detected, x, y, size, closed):
        self.detected = detected
        if detected:
            self.x += (x - self.x) * self.smoothing
            self.y += (y - self.y) * self.smoothing
        closed = closed and detected

        event = None
        if closed:
            if not self.pinching:
                self.trail.clear()
                event = "press"
            else:
                event = "drag"
            self.trail.append((t, x, y, size))
        elif self.pinching:
            if detected:
                self.trail.append((t, x, y, size))   # кадр разжатия — конец броска
            event = "release"
        self.pinching = closed
        return event

    def motion(self):
        return estimate_motion(self.trail)


class PinchHysteresis:
    def __init__(self):
        self.closed = False

    def update(self, sample):
        if not sample.detected or sample.hand_size < 1e-6:
            self.closed = False
        else:
            ratio = sample.pinch_dist / sample.hand_size
            self.closed = ratio < (PINCH_OPEN_RATIO if self.closed else PINCH_CLOSE_RATIO)
        return self.closed


def cam_to_screen(x, y):
    """Центральная часть кадра камеры → область мишени в окне."""
    def norm(v):
        return min(max((v - CAM_MARGIN) / (1 - 2 * CAM_MARGIN), 0.0), 1.0)
    return int(norm(x) * (BOARD_AREA - 1)), int(norm(y) * (WINDOW_H - 1))


def dispatch(game, event, pos, hand):
    if event == "press":
        game.on_press(pos)
    elif event == "drag":
        game.on_drag(pos)
    elif event == "release":
        game.on_release(pos, hand.motion())


def main():
    pygame.init()
    pygame.display.set_caption("Дартс — Gesture Edition")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 18)

    game = DartsGame(screen, WINDOW_W, WINDOW_H)

    tracker = GestureTracker(cam_index=0, show_debug=True)
    tracker.start()

    cam_input = PinchInput(CAM_SMOOTHING)
    mouse_input = PinchInput(1.0)
    pinch = PinchHysteresis()
    last_sample_t = 0.0

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
                elif event.key == pygame.K_r:
                    game.reset()
                elif event.key == pygame.K_a:
                    game.reaim()

        if use_gesture:
            hand = cam_input
            # обрабатываем каждый кадр камеры, а не только последний: для оценки
            # скорости броска важны точные времена кадров
            for sample in tracker.get_samples_since(last_sample_t):
                last_sample_t = sample.t
                ev = hand.feed(sample.t, sample.detected, sample.x, sample.y,
                               sample.hand_size, pinch.update(sample))
                dispatch(game, ev, cam_to_screen(hand.x, hand.y), hand)
            cursor = cam_to_screen(hand.x, hand.y)
        else:
            hand = mouse_input
            mx, my = pygame.mouse.get_pos()
            ev = hand.feed(time.time(), True, mx / WINDOW_W, my / WINDOW_H, 1.0,
                           pygame.mouse.get_pressed()[0])
            cursor = (mx, my)
            dispatch(game, ev, cursor, hand)

        game.update(dt)
        game.draw(cursor, hand.pinching, hand.motion() if hand.pinching else None)

        mode_txt = f"Режим: {'ЖЕСТЫ' if use_gesture else 'МЫШЬ'}  (G — переключить, A — прицел, R — заново, ESC — выход)"
        screen.blit(font_small.render(mode_txt, True, (150, 156, 170)), (10, WINDOW_H - 26))
        if use_gesture and not hand.detected:
            warn = font_small.render("Рука не найдена в кадре камеры", True, (240, 90, 90))
            screen.blit(warn, (10, WINDOW_H - 48))

        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
