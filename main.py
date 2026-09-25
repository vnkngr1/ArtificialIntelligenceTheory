"""
Block Blast — Gesture Edition
==============================

Точка входа. Запускает окно Pygame с игрой и, параллельно в отдельном
потоке, трекер жестов на OpenCV/MediaPipe.

Управление:
  G       — переключить режим ЖЕСТЫ / МЫШЬ (для отладки без камеры)
  R       — начать заново
  ESC     — выход
  Щипок (большой + указательный палец) — "зажать" блок и тащить,
            разжатие пальцев — отпустить блок на поле.
"""

import sys

import pygame

from game import BlockBlastGame
from gesture_tracker import GestureTracker

WINDOW_W, WINDOW_H = 760, 760
FPS = 60


def main():
    pygame.init()
    pygame.display.set_caption("Block Blast — Gesture Edition")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 18)

    game = BlockBlastGame(screen, WINDOW_W, WINDOW_H)

    tracker = GestureTracker(cam_index=0, show_debug=True)
    tracker.start()

    use_gesture = True
    prev_down = False
    hand_detected = True

    running = True
    while running:
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

        mode_txt = f"Режим: {'ЖЕСТЫ' if use_gesture else 'МЫШЬ'}  (G — переключить, R — заново, ESC — выход)"
        screen.blit(font_small.render(mode_txt, True, (90, 90, 90)), (10, WINDOW_H - 26))

        if use_gesture and not hand_detected:
            warn = font_small.render("Рука не найдена в кадре камеры", True, (200, 30, 30))
            screen.blit(warn, (10, WINDOW_H - 46))

        pygame.draw.circle(screen, (255, 0, 0) if is_down else (20, 20, 20), (cursor_x, cursor_y), 8, 2)

        pygame.display.flip()
        clock.tick(FPS)

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
