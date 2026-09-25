"""
Гонка в потоке — Eye Edition
============================

Точка входа гонки. Машина едет туда, куда вы смотрите: положение зрачков
по горизонтали (MediaPipe FaceLandmarker, точки радужки) задаёт, в какую
точку дороги рулить.

Перед игрой — калибровка: по очереди смотрите на три точки (центр, левая
полоса, правая полоса), голову при этом держите неподвижно. Калибровку
можно повторить клавишей C — например, если пересели.

Управление:
  Взгляд влево / вправо — рулить
  Моргание / ПРОБЕЛ     — начать заново после аварии
  G   — режим ГЛАЗА → МЫШЬ → КЛАВИШИ (стрелки ← →), для отладки без камеры
  C   — перекалибровать взгляд
  K   — показать / спрятать окно камеры
  R   — начать заново
  ESC — выход
"""

import math
import os
import sys
import time

# Корень проекта — в sys.path, чтобы найти общий пакет core/ (и при запуске
# через launcher.py, и при запуске этого файла напрямую, например из PyCharm).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from core.blink_tracker import BlinkTracker
from core.camera_preview import CameraPreview
from core.gaze_calibration import GazeCalibration
from game import LANE_CENTERS, RaceGame

WINDOW_W, WINDOW_H = 900, 760
FPS = 60

GAZE_TAU = 0.15         # сек, сглаживание взгляда (больше — плавнее, но с запаздыванием)
GAZE_GAIN = 1.0         # >1 — хватает меньшего движения глаз, чтобы доехать до края
FACE_LOST_PAUSE = 0.4   # сек без лица — пауза

MODES = ["ГЛАЗА", "МЫШЬ", "КЛАВИШИ"]


def main():
    pygame.init()
    pygame.display.set_caption("Гонка в потоке — Eye Edition")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)
    font = pygame.font.SysFont("arial", 20)
    font_big = pygame.font.SysFont("arial", 40, bold=True)

    game = RaceGame(screen, WINDOW_W, WINDOW_H)
    calibration = GazeCalibration(font, font_big, LANE_CENTERS[0], LANE_CENTERS[-1], WINDOW_H // 2, GAZE_GAIN)

    tracker = BlinkTracker(cam_index=0)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    seen_blinks = 0

    mode = 0
    smooth_gaze = None
    key_u = 2 / 3
    last_face_t = time.time()

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
                    mode = (mode + 1) % len(MODES)
                elif event.key == pygame.K_c:
                    calibration.restart()
                    mode = 0
                elif event.key == pygame.K_k:
                    preview.toggle()
                elif event.key == pygame.K_r:
                    game.reset()
                elif event.key == pygame.K_SPACE:
                    game.action()

        face_detected, _, _, blinks = tracker.get_state()
        gaze = tracker.get_gaze()
        if face_detected:
            last_face_t = time.time()
        calibrating = MODES[mode] == "ГЛАЗА" and not calibration.done

        if blinks > seen_blinks:
            seen_blinks = blinks
            if not calibrating:
                game.action()

        if MODES[mode] == "ГЛАЗА":
            if calibrating:
                calibration.update(dt, face_detected, gaze)
                smooth_gaze = None
            elif gaze is not None:
                alpha = 1 - math.exp(-dt / GAZE_TAU)
                smooth_gaze = gaze if smooth_gaze is None else smooth_gaze + (gaze - smooth_gaze) * alpha
                game.set_target(calibration.map(smooth_gaze))
            game.paused = calibrating or time.time() - last_face_t > FACE_LOST_PAUSE
        elif MODES[mode] == "МЫШЬ":
            mx = pygame.mouse.get_pos()[0]
            game.set_target((mx - LANE_CENTERS[0]) / (LANE_CENTERS[-1] - LANE_CENTERS[0]))
            game.paused = False
        else:
            keys = pygame.key.get_pressed()
            key_u += (keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]) * 1.4 * dt
            key_u = max(-0.15, min(1.15, key_u))
            game.set_target(key_u)
            game.paused = False

        game.update(dt)
        game.draw()
        if MODES[mode] == "ГЛАЗА" and not calibration.done:
            calibration.draw(screen, face_detected)
        elif MODES[mode] == "ГЛАЗА" and game.paused:
            warn = font.render("Лицо не найдено в кадре камеры — пауза", True, (255, 120, 120))
            screen.blit(warn, warn.get_rect(center=(WINDOW_W // 2, 80)))

        mode_txt = f"Режим: {MODES[mode]}  (G — сменить, C — калибровка, R — заново, K — камера, ESC — выход)"
        txt = font_small.render(mode_txt, True, (235, 235, 235))
        screen.blit(txt, (WINDOW_W - txt.get_width() - 12, WINDOW_H - 44))

        preview.draw(screen)
        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
