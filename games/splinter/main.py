"""
Занозы — Gesture Edition
========================

Точка входа симулятора удаления заноз. Пинцет управляется рукой через
веб-камеру (OpenCV + MediaPipe Hands): кончик пинцета — точка между
большим и указательным пальцами, щипок — сжать пинцет.

Как вытащить занозу:
  1. Подведите пинцет к торчащему кончику занозы и сведите пальцы.
  2. Не разжимая пальцев, плавно тяните руку вдоль занозы наружу —
     туда, куда она торчит (тёмная часть под кожей показывает ось).
  3. Вкось и рывком — больно, и заноза может сломаться.

Управление:
  G   — режим ЖЕСТЫ / МЫШЬ (мышь: зажать ЛКМ = щипок)
  L   — лупа вкл/выкл
  K   — показать / спрятать окно камеры
  R   — начать уровень заново
  ESC — выход
  Средний палец (показать камере и подержать) — выход
"""

import os
import sys

# Корень проекта — в sys.path, чтобы найти общий пакет core/ (и при запуске
# через launcher.py, и при запуске этого файла напрямую, например из PyCharm).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from core.camera_preview import CameraPreview, hand_status, preview_rect
from core.display import open_window
from core.exit_gesture import ExitGesture
from core.gesture_tracker import GestureTracker, PinchHysteresis
from core.one_euro import OneEuroFilter2D
from game import SplinterGame

WINDOW_W, WINDOW_H = 1000, 720
FPS = 60

CAM_MIN_CUTOFF = 0.5    # фильтр One Euro для пинцета: меньше — меньше дрожания в покое
CAM_BETA = 10.0         # больше — меньше запаздывания при быстром движении
CAM_MARGIN = 0.15       # края кадра, до которых рука дотягивается с трудом


def cam_to_screen(x, y):
    def norm(v):
        return min(max((v - CAM_MARGIN) / (1 - 2 * CAM_MARGIN), 0.0), 1.0)
    return norm(x) * (WINDOW_W - 1), norm(y) * (WINDOW_H - 1)


def main():
    pygame.init()
    screen = open_window((WINDOW_W, WINDOW_H), "Занозы — Gesture Edition")
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)

    game = SplinterGame(screen, WINDOW_W, WINDOW_H, reserved=preview_rect(WINDOW_H))

    tracker = GestureTracker(cam_index=0)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    exit_gesture = ExitGesture()
    pinch = PinchHysteresis()
    last_sample_t = 0.0
    sx = sy = 0.5
    tip_filter = OneEuroFilter2D(CAM_MIN_CUTOFF, CAM_BETA)
    pinching = False
    hand_detected = False

    use_gesture = True
    mouse_down = False
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
                    if game.pinching:
                        game.on_release(game.cursor)
                    pinching = mouse_down = False
                elif event.key == pygame.K_k:
                    preview.toggle()
                elif event.key == pygame.K_l:
                    game.toggle_lens()
                elif event.key == pygame.K_r:
                    game.restart_level()

        if use_gesture:
            # каждый кадр камеры по порядку: щипок и движение не теряются между кадрами игры
            for sample in tracker.get_samples_since(last_sample_t):
                last_sample_t = sample.t
                hand_detected = sample.detected
                if sample.detected:
                    sx, sy = tip_filter(sample.x, sample.y, sample.t)
                closed = pinch.update(sample)
                pos = cam_to_screen(sx, sy)
                if closed and not pinching:
                    game.on_press(pos)
                elif closed:
                    game.on_drag(pos)
                elif pinching:
                    game.on_release(pos)
                else:
                    game.on_hover(pos)
                pinching = closed
        else:
            pos = pygame.mouse.get_pos()
            down = pygame.mouse.get_pressed()[0]
            if down and not mouse_down:
                game.on_press(pos)
            elif down:
                game.on_drag(pos)
            elif mouse_down:
                game.on_release(pos)
            else:
                game.on_hover(pos)
            mouse_down = down

        game.update(dt)
        game.draw()

        mode_txt = f"Режим: {'ЖЕСТЫ' if use_gesture else 'МЫШЬ'}  (G — сменить, L — лупа, R — заново, K — камера, ESC — выход)"
        txt = font_small.render(mode_txt, True, (170, 190, 195))
        screen.blit(txt, (WINDOW_W - txt.get_width() - 14, WINDOW_H - 58))
        if use_gesture and not hand_detected:
            warn = font_small.render("Рука не найдена в кадре камеры", True, (255, 120, 120))
            screen.blit(warn, (preview.rect.right + 12, WINDOW_H - 58))

        preview.draw(screen, hand_status(hand_detected, pinching) if use_gesture else None)
        if exit_gesture.update(dt, tracker):
            running = False
        exit_gesture.draw(screen)
        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
