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
  R   — начать заново
  ESC — выход
"""

import math
import statistics
import sys
import time

import pygame

from blink_tracker import BlinkTracker
from race_game import LANE_CENTERS, RaceGame

WINDOW_W, WINDOW_H = 900, 760
FPS = 60

GAZE_TAU = 0.15         # сек, сглаживание взгляда (больше — плавнее, но с запаздыванием)
GAZE_GAIN = 1.0         # >1 — хватает меньшего движения глаз, чтобы доехать до края
FACE_LOST_PAUSE = 0.4   # сек без лица — пауза

MODES = ["ГЛАЗА", "МЫШЬ", "КЛАВИШИ"]


class GazeCalibration:
    """Три точки: центр, левая полоса, правая полоса. На каждой — пауза, чтобы
    перевести взгляд, затем сбор значений и медиана (она устойчива к выбросам)."""

    POINTS = [0.5, 0.0, 1.0]   # u: 0 — центр левой полосы, 1 — центр правой
    SETTLE = 0.8
    COLLECT = 1.2
    MIN_SPAN = 0.015            # слишком маленький разброс лево-право — калибровка не удалась

    def __init__(self, font, font_big):
        self.font = font
        self.font_big = font_big
        self.values = None
        self.message = None
        self.restart()

    def restart(self):
        self.index = 0
        self.t = 0.0
        self.samples = []
        self.collected = []
        self.done = False

    def update(self, dt, face_detected, gaze):
        if self.done or not face_detected:
            return
        self.t += dt
        if self.t > self.SETTLE and gaze is not None:
            self.samples.append(gaze)
        if self.t >= self.SETTLE + self.COLLECT and len(self.samples) >= 8:
            self.collected.append(statistics.median(self.samples))
            self.index += 1
            self.t, self.samples = 0.0, []
            if self.index == len(self.POINTS):
                self._finish()

    def _finish(self):
        c, left, right = self.collected
        if (left - c) * (right - c) < 0 and abs(right - left) >= self.MIN_SPAN \
                and min(abs(left - c), abs(right - c)) > 0.003:
            self.values = (c, left, right)
            self.message = None
            self.done = True
        else:
            self.message = "Не получилось различить взгляд влево и вправо — повторим. Держите голову неподвижно."
            self.restart()

    def map(self, gaze):
        """Значение зрачков → u (0 — левая полоса, 1 — правая), кусочно-линейно через центр."""
        c, left, right = self.values
        if (gaze - c) * (left - c) > 0:
            u = 0.5 - 0.5 * (gaze - c) / (left - c)
        else:
            u = 0.5 + 0.5 * (gaze - c) / (right - c)
        return 0.5 + (u - 0.5) * GAZE_GAIN

    def draw(self, screen, face_detected):
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        screen.blit(overlay, (0, 0))
        w = screen.get_width()

        def center_text(text, font, y, color=(245, 245, 245)):
            txt = font.render(text, True, color)
            screen.blit(txt, txt.get_rect(center=(w // 2, y)))

        center_text("Калибровка взгляда", self.font_big, 110)
        center_text("Смотрите на жёлтую точку, голову держите неподвижно", self.font, 160)
        if self.message:
            center_text(self.message, self.font, 200, (255, 120, 120))
        if not face_detected:
            center_text("Лицо не найдено в кадре камеры", self.font, 600, (255, 120, 120))

        u = self.POINTS[self.index]
        x = LANE_CENTERS[0] + u * (LANE_CENTERS[-1] - LANE_CENTERS[0])
        y = WINDOW_H // 2
        for i, pu in enumerate(self.POINTS):
            px = LANE_CENTERS[0] + pu * (LANE_CENTERS[-1] - LANE_CENTERS[0])
            if i < self.index:
                pygame.draw.circle(screen, (90, 200, 110), (px, y), 8)
        pygame.draw.circle(screen, (255, 220, 60), (x, y), 14)
        pygame.draw.circle(screen, (20, 20, 20), (x, y), 4)
        progress = max(0.0, min(1.0, (self.t - self.SETTLE) / self.COLLECT))
        if progress > 0:
            rect = pygame.Rect(0, 0, 50, 50)
            rect.center = (x, y)
            pygame.draw.arc(screen, (255, 220, 60), rect, math.pi / 2, math.pi / 2 + 2 * math.pi * progress, 4)
        center_text(f"Точка {self.index + 1} из {len(self.POINTS)}", self.font, y + 60)


def main():
    pygame.init()
    pygame.display.set_caption("Гонка в потоке — Eye Edition")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)
    font = pygame.font.SysFont("arial", 20)
    font_big = pygame.font.SysFont("arial", 40, bold=True)

    game = RaceGame(screen, WINDOW_W, WINDOW_H)
    calibration = GazeCalibration(font, font_big)

    tracker = BlinkTracker(cam_index=0, show_debug=True)
    tracker.start()
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

        mode_txt = f"Режим: {MODES[mode]}  (G — сменить, C — калибровка, R — заново, ESC — выход)"
        txt = font_small.render(mode_txt, True, (235, 235, 235))
        screen.blit(txt, (WINDOW_W - txt.get_width() - 12, WINDOW_H - 44))

        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
