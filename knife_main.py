"""
Knife Hit — Blink Edition
=========================

Точка входа игры «ножи в бревно». Запускает окно Pygame и, параллельно
в отдельном потоке, трекер моргания на OpenCV/MediaPipe (FaceLandmarker).

Управление:
  Моргание (оба глаза) — бросить нож
  ПРОБЕЛ  — бросить нож с клавиатуры (для отладки без камеры)
  R       — начать заново
  ESC     — выход

Если нож бросается от случайных морганий — увеличьте MIN_CLOSED_TIME:
тогда засчитываются только нарочно долгие моргания.
"""

import sys

import pygame

from blink_tracker import BlinkTracker
from knife_game import KnifeHitGame

WINDOW_W, WINDOW_H = 560, 760
FPS = 60

MIN_CLOSED_TIME = 0.0   # сек; ~0.25 — только долгое «нарочное» моргание


def draw_eye_indicator(screen, font, face_detected, score, closed):
    """Глаз в правом нижнем углу: веко опускается вместе с closed_score."""
    x, y = WINDOW_W - 60, WINDOW_H - 150
    if not face_detected:
        txt = font.render("Лицо не найдено в кадре камеры", True, (240, 90, 90))
        screen.blit(txt, (WINDOW_W - txt.get_width() - 16, WINDOW_H - 56))
        return
    openness = max(0.0, min(1.0, 1 - score))
    h = max(2, int(26 * openness))
    color = (255, 90, 90) if closed else (240, 236, 228)
    pygame.draw.ellipse(screen, color, (x - 24, y - h // 2, 48, h), 3)
    if h > 8:
        pygame.draw.circle(screen, color, (x, y), min(7, h // 2 - 2))
    label = font.render(f"моргание {score:.2f}", True, (150, 146, 170))
    screen.blit(label, (x - label.get_width() // 2, y + 20))


def main():
    pygame.init()
    pygame.display.set_caption("Knife Hit — Blink Edition")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)

    game = KnifeHitGame(screen, WINDOW_W, WINDOW_H)

    tracker = BlinkTracker(cam_index=0, min_closed_time=MIN_CLOSED_TIME, show_debug=True)
    tracker.start()
    seen_blinks = 0

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    game.throw()
                elif event.key == pygame.K_r:
                    game.reset()

        face_detected, score, closed, blinks = tracker.get_state()
        if blinks > seen_blinks:
            seen_blinks = blinks
            game.throw()

        game.update(dt)
        game.draw()

        hint = font_small.render("Моргните — бросить нож  (ПРОБЕЛ, R — заново, ESC — выход)", True, (150, 146, 170))
        screen.blit(hint, (WINDOW_W // 2 - hint.get_width() // 2, WINDOW_H - 28))
        draw_eye_indicator(screen, font_small, face_detected, score, closed)

        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
