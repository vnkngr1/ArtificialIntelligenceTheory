"""
Block Blast — Gesture Edition
==============================

Точка входа. Запускает окно Pygame с игрой и, параллельно в отдельном
потоке, трекер жестов на OpenCV/MediaPipe.

Управление:
  G       — переключить режим ЖЕСТЫ / МЫШЬ (для отладки без камеры)
  R       — начать заново
  K       — показать / спрятать окно камеры
  ESC     — выход
  Средний палец (показать камере и подержать) — выход
  Щипок (большой + указательный палец) — "зажать" блок и тащить,
            разжатие пальцев — отпустить блок на поле.
"""

import os
import sys

# Корень проекта — в sys.path, чтобы найти общий пакет core/ (и при запуске
# через launcher.py, и при запуске этого файла напрямую, например из PyCharm).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from game import BlockBlastGame
from core.camera_preview import CameraPreview, preview_rect
from core.display import open_window
from core.exit_gesture import ExitGesture
from core.gesture_tracker import GestureTracker

WINDOW_W, WINDOW_H = 760, 760
FPS = 60


def main():
    pygame.init()
    screen = open_window((WINDOW_W, WINDOW_H), "Block Blast — Gesture Edition")
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 18)

    game = BlockBlastGame(screen, WINDOW_W, WINDOW_H, reserved=preview_rect(WINDOW_H))

    tracker = GestureTracker(cam_index=0)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    exit_gesture = ExitGesture()
    text_x = preview.rect.right + 12

    use_gesture = True
    prev_down = False
    hand_detected = True

    running = True
    dt = 0.0
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_g:
                    use_gesture = not use_gesture
                elif event.key == pygame.K_k:
                    preview.toggle()
                elif event.key == pygame.K_r:
                    game.reset()

        if use_gesture:
            gx, gy, pinching, hand_detected = tracker.get_state()
            cursor_x = int(gx * WINDOW_W)
            cursor_y = int(gy * WINDOW_H)
            is_down = pinching
        else:
            cursor_x, cursor_y = pygame.mouse.get_pos()
            is_down = pygame.mouse.get_pressed()[0]

        if is_down and not prev_down:
            game.on_press((cursor_x, cursor_y))
        elif is_down and prev_down:
            game.on_drag((cursor_x, cursor_y))
        elif not is_down and prev_down:
            game.on_release((cursor_x, cursor_y))
        prev_down = is_down

        game.update()
        game.draw((cursor_x, cursor_y))

        mode_txt = f"Режим: {'ЖЕСТЫ' if use_gesture else 'МЫШЬ'}  (G — сменить, R — заново, K — камера, ESC — выход)"
        screen.blit(font_small.render(mode_txt, True, (90, 90, 90)), (text_x, WINDOW_H - 26))

        if use_gesture and not hand_detected:
            warn = font_small.render("Рука не найдена в кадре камеры", True, (200, 30, 30))
            screen.blit(warn, (text_x, WINDOW_H - 46))

        pygame.draw.circle(screen, (255, 0, 0) if is_down else (20, 20, 20), (cursor_x, cursor_y), 8, 2)

        preview.draw(screen)
        if exit_gesture.update(dt, tracker):
            running = False
        exit_gesture.draw(screen)
        pygame.display.flip()
        dt = clock.tick(FPS) / 1000.0

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
