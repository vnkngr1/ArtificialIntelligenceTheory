"""
Симулятор списывания — Eye Edition
==================================

Точка входа. Камера следит за лицом (MediaPipe FaceLandmarker): опустили
взгляд или голову — в игре вы смотрите вниз, на шпаргалку, и не видите
учителя. Подняли — снова видно класс.

Перед игрой — калибровка (около 5 секунд): посмотреть на экран, затем вниз,
как на шпаргалку на коленях. По ней игра узнаёт, как именно вы опускаете
взгляд — глазами, головой или и тем и другим.

Управление:
  Взгляд вниз / на экран — шпаргалка / класс с учителем
  Клавиатура — печатать ответ, Enter — записать, Tab — пропустить задание
  ↓ (удерживать) — смотреть вниз без камеры
  F1 — перекалибровать, F2 — окно камеры, ESC — выход
  Средний палец (показать камере и подержать) — выход
"""

import math
import os
import sys

# Корень проекта — в sys.path, чтобы найти общий пакет core/ (и при запуске
# через launcher.py, и при запуске этого файла напрямую, например из PyCharm).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from core.blink_tracker import BlinkTracker
from core.camera_preview import CameraPreview, preview_rect
from core.display import open_window
from core.exit_gesture import ExitGesture
from game import RESULT, CheatingGame
from look import LookDownDetector

WINDOW_W, WINDOW_H = 1280, 800
FPS = 60

LOOK_UP_COLOR = (90, 230, 120)
LOOK_DOWN_COLOR = (255, 170, 80)
NO_FACE_COLOR = (255, 110, 110)


def draw_calibration(screen, fonts, detector, face_detected):
    font, font_big = fonts
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 185))
    screen.blit(overlay, (0, 0))
    w = screen.get_width()

    def center(text, f, y, color=(240, 242, 248)):
        txt = f.render(text, True, color)
        screen.blit(txt, txt.get_rect(center=(w // 2, y)))

    center("Калибровка взгляда", font_big, 150)
    center(f"Шаг {min(detector.phase + 1, 2)} из 2", font, 205, (170, 176, 196))
    center(detector.instruction, font, 300, (255, 220, 120))
    if detector.message:
        center(detector.message, font, 250, (255, 130, 120))
    if not face_detected:
        center("Лицо не найдено в кадре камеры", font, 470, (255, 130, 120))
    # стрелка: куда смотреть
    arrow_y = 380
    if detector.phase == 0:
        pygame.draw.circle(screen, (255, 220, 120), (w // 2, arrow_y), 14)
    else:
        pygame.draw.polygon(screen, (255, 220, 120), [(w // 2 - 26, arrow_y - 10), (w // 2 + 26, arrow_y - 10),
                                                      (w // 2, arrow_y + 24)])
    rect = pygame.Rect(0, 0, 90, 90)
    rect.center = (w // 2, arrow_y)
    if detector.progress > 0:
        pygame.draw.arc(screen, (255, 220, 120), rect, math.pi / 2, math.pi / 2 + 2 * math.pi * detector.progress, 5)


def main():
    pygame.init()
    screen = open_window((WINDOW_W, WINDOW_H), "Списывание — Eye Edition")
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)
    font = pygame.font.SysFont("arial", 24)
    font_big = pygame.font.SysFont("arial", 44, bold=True)

    game = CheatingGame(screen, WINDOW_W, WINDOW_H, reserved=preview_rect(WINDOW_H))
    tracker = BlinkTracker(cam_index=0, head_pose=True)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    exit_gesture = ExitGesture()
    detector = LookDownDetector()
    keyboard_only = False
    pygame.key.start_text_input()

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.TEXTINPUT:
                if detector.done or keyboard_only:
                    game.type_text(event.text)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F1:
                    detector.restart()
                    keyboard_only = False
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

        # без камеры — играем клавишей ↓ (калибровка не нужна)
        if tracker.error and not detector.done:
            keyboard_only = True
        face_detected = tracker.get_state()[0]
        features = tracker.get_look()
        arrow_down = pygame.key.get_pressed()[pygame.K_DOWN]

        calibrating = not detector.done and not keyboard_only
        if calibrating:
            detector.calibrate(dt, features)
            looking_down = False
        elif keyboard_only:
            looking_down = arrow_down
        else:
            looking_down = detector.update(features) or arrow_down

        if not calibrating:
            game.set_looking_down(looking_down)
            game.update(dt)
        game.draw()
        if calibrating:
            draw_calibration(screen, (font, font_big), detector, face_detected)

        # окно камеры и подпись: куда сейчас смотрит игрок
        if keyboard_only:
            status = ("ВНИЗ (↓)", LOOK_DOWN_COLOR) if looking_down else ("КЛАВИАТУРА", (200, 205, 220))
        elif not face_detected and not calibrating:
            status = ("НЕТ ЛИЦА — ВНИЗ", NO_FACE_COLOR)
        elif calibrating:
            status = None
        else:
            status = ("ВНИЗ", LOOK_DOWN_COLOR) if looking_down else ("НА ЭКРАН", LOOK_UP_COLOR)
        preview.draw(screen, status)
        if not calibrating and not keyboard_only and detector.done and features is not None:
            k = max(0.0, min(1.0, detector.score(features)))       # «насколько вниз» — полоска под окном камеры
            r = preview.rect
            pygame.draw.rect(screen, (40, 44, 58), (r.right + 10, r.top, 8, r.height), border_radius=4)
            fill = int(r.height * k)
            pygame.draw.rect(screen, LOOK_DOWN_COLOR if looking_down else LOOK_UP_COLOR,
                             (r.right + 10, r.bottom - fill, 8, fill), border_radius=4)

        help_txt = "F1 — калибровка · F2 — камера · ↓ — смотреть вниз с клавиатуры · ESC — выход"
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
