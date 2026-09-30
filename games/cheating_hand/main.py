"""
Симулятор списывания — Gesture Edition (шпаргалка в руке)
========================================================

Точка входа версии «рукой». Щипок (большой + указательный) — достать
шпаргалку из-под парты и посмотреть в неё; разжать пальцы — спрятать и
снова видеть класс. Пока шпаргалка в руке, учителя не видно — ориентируйтесь
по стуку мела. Двигайте рукой, чтобы подвинуть листок.

Управление:
  Щипок — смотреть в шпаргалку, разжать — спрятать
  Клавиатура — печатать ответ, Enter — записать, Tab — пропустить задание
  ↓ (удерживать) — смотреть в шпаргалку без камеры
  F2 — окно камеры, ESC — выход
  Средний палец (показать камере и подержать) — выход
"""

import os
import sys

# Корень проекта — в sys.path, чтобы найти общий пакет core/ и игру games/cheating,
# на которой построена эта версия (и при запуске через launcher.py, и напрямую).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from core.camera_preview import CameraPreview, hand_status, preview_rect
from core.display import open_window
from core.exit_gesture import ExitGesture
from core.gesture_tracker import GestureTracker, PinchHysteresis
from core.one_euro import OneEuroFilter2D
from game import HandCheatingGame
from games.cheating.game import RESULT

WINDOW_W, WINDOW_H = 1280, 800
FPS = 60
CAM_MARGIN = 0.12


def cam_to_screen(x, y):
    def norm(v):
        return min(max((v - CAM_MARGIN) / (1 - 2 * CAM_MARGIN), 0.0), 1.0)
    return norm(x) * WINDOW_W, norm(y) * WINDOW_H


def main():
    pygame.init()
    screen = open_window((WINDOW_W, WINDOW_H), "Списывание рукой — Gesture Edition")
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)

    game = HandCheatingGame(screen, WINDOW_W, WINDOW_H, reserved=preview_rect(WINDOW_H))
    tracker = GestureTracker(cam_index=0)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    exit_gesture = ExitGesture()
    pinch = PinchHysteresis()
    hand_filter = OneEuroFilter2D(0.5, 10.0)
    last_sample_t = 0.0
    pinching = hand_detected = False
    pygame.key.start_text_input()

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.TEXTINPUT:
                game.type_text(event.text)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F2:
                    preview.toggle()
                elif event.key == pygame.K_BACKSPACE:
                    game.backspace()
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if game.phase == RESULT:
                        game.continue_after_result()
                    else:
                        game.submit()
                elif event.key == pygame.K_TAB:
                    game.skip()

        # рука: щипок — шпаргалка в руке, положение руки — где листок
        for sample in tracker.get_samples_since(last_sample_t):
            last_sample_t = sample.t
            hand_detected = sample.detected
            pinching = pinch.update(sample)
            if sample.detected:
                game.set_hand(cam_to_screen(*hand_filter(sample.x, sample.y, sample.t)))
        holding = pinching or pygame.key.get_pressed()[pygame.K_DOWN]

        game.set_looking_down(holding)
        game.update(dt)
        game.draw()

        if holding:
            status = ("ШПАРГАЛКА", (255, 170, 80))
        else:
            status = hand_status(hand_detected, False, pinch.ratio)
        preview.draw(screen, status)

        help_txt = "Щипок — шпаргалка · F2 — камера · ↓ — шпаргалка с клавиатуры · ESC — выход"
        txt = font_small.render(help_txt, True, (200, 205, 220))
        box = txt.get_rect(bottomright=(WINDOW_W - 12, WINDOW_H - 8)).inflate(14, 6)
        pygame.draw.rect(screen, (20, 22, 30), box, border_radius=6)
        screen.blit(txt, txt.get_rect(center=box.center))

        if exit_gesture.update(dt, tracker):
            running = False
        exit_gesture.draw(screen)
        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
