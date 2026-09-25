"""
Калибровка взгляда по горизонтали — общая для игр с управлением зрачками.

BlinkTracker.get_gaze() отдаёт «сырое» положение зрачков между уголками
глаз. Его абсолютные значения у всех разные (форма глаз, посадка перед
камерой), поэтому перед игрой человек по очереди смотрит на три точки
на экране: центр, левую и правую. Дальше map() переводит сырое значение
в u: 0 — левая точка, 1 — правая, 0.5 — центр (кусочно-линейно через
центр, так что несимметричность левого и правого глаза не мешает).
"""

import math
import statistics

import pygame


class GazeCalibration:
    POINTS = [0.5, 0.0, 1.0]    # порядок точек: центр, левая, правая
    SETTLE = 0.8                # сек, чтобы перевести взгляд на точку
    COLLECT = 1.2               # сек сбора значений; берётся медиана — она устойчива к выбросам
    MIN_SPAN = 0.015            # слишком маленький разброс лево-право — калибровка не удалась

    def __init__(self, font, font_big, left_x, right_x, y, gain=1.0):
        """left_x / right_x / y — где на экране рисовать точки (u = 0 и u = 1);
        gain > 1 — хватает меньшего движения глаз, чтобы дойти до края."""
        self.font = font
        self.font_big = font_big
        self.left_x = left_x
        self.right_x = right_x
        self.y = y
        self.gain = gain
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
        """Значение зрачков → u (0 — левая точка, 1 — правая)."""
        c, left, right = self.values
        if (gaze - c) * (left - c) > 0:
            u = 0.5 - 0.5 * (gaze - c) / (left - c)
        else:
            u = 0.5 + 0.5 * (gaze - c) / (right - c)
        return 0.5 + (u - 0.5) * self.gain

    def point_x(self, u):
        return self.left_x + u * (self.right_x - self.left_x)

    def draw(self, screen, face_detected):
        if self.done:
            return
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
            center_text("Лицо не найдено в кадре камеры", self.font, self.y + 200, (255, 120, 120))

        x = self.point_x(self.POINTS[self.index])
        for i, pu in enumerate(self.POINTS[:self.index]):
            pygame.draw.circle(screen, (90, 200, 110), (self.point_x(pu), self.y), 8)
        pygame.draw.circle(screen, (255, 220, 60), (x, self.y), 14)
        pygame.draw.circle(screen, (20, 20, 20), (x, self.y), 4)
        progress = max(0.0, min(1.0, (self.t - self.SETTLE) / self.COLLECT))
        if progress > 0:
            rect = pygame.Rect(0, 0, 50, 50)
            rect.center = (x, self.y)
            pygame.draw.arc(screen, (255, 220, 60), rect, math.pi / 2, math.pi / 2 + 2 * math.pi * progress, 4)
        center_text(f"Точка {self.index + 1} из {len(self.POINTS)}", self.font, self.y + 60)
