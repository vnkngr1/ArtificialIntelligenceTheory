"""
Выход жестом «средний палец»: показать камере руку с поднятым средним
пальцем (указательный, безымянный и мизинец согнуты) и подержать ~0.7 с.
Пока жест держится, в центре экрана заполняется круг «Выход».

Жест распознают трекеры (GestureTracker и BlinkTracker — у второго для
этого дополнительно работает модель руки): tracker.exit_gesture().

Жест «взводится» только после того, как трекер хотя бы раз увидел, что
жеста нет. Иначе, выйдя жестом из игры и не опустив руку, человек сразу
закрыл бы и меню, в которое вернулся.

    exit_gesture = ExitGesture()
    ...
    if exit_gesture.update(dt, tracker):     # каждый кадр
        running = False
    exit_gesture.draw(screen)                # поверх всего, перед flip()
"""

import math

import pygame

HOLD_TIME = 0.7


class ExitGesture:
    def __init__(self, hold_time=HOLD_TIME):
        self.hold_time = hold_time
        self.armed = False
        self.t = 0.0
        self.font = pygame.font.SysFont("arial", 20, bold=True)

    def reset(self):
        """Снова ждать, пока жест исчезнет (например, после возврата из игры)."""
        self.armed = False
        self.t = 0.0

    def update(self, dt, tracker):
        """Возвращает True, когда жест удерживали достаточно долго."""
        if not tracker.camera_ready():
            self.t = 0.0
            return False
        active = tracker.exit_gesture()
        if not self.armed:
            self.armed = not active
            return False
        self.t = self.t + dt if active else 0.0
        return self.t >= self.hold_time

    @property
    def progress(self):
        return min(1.0, self.t / self.hold_time)

    def draw(self, screen):
        if self.t <= 0.08:
            return
        cx, cy = screen.get_width() // 2, screen.get_height() // 2
        overlay = pygame.Surface((150, 150), pygame.SRCALPHA)
        pygame.draw.circle(overlay, (0, 0, 0, 190), (75, 75), 70)
        screen.blit(overlay, (cx - 75, cy - 75))
        rect = pygame.Rect(0, 0, 124, 124)
        rect.center = (cx, cy)
        pygame.draw.circle(screen, (90, 40, 40), (cx, cy), 62, 8)
        pygame.draw.arc(screen, (255, 90, 90), rect, math.pi / 2, math.pi / 2 + 2 * math.pi * self.progress, 8)
        txt = self.font.render("Выход", True, (255, 235, 235))
        screen.blit(txt, txt.get_rect(center=(cx, cy)))
