"""
Паук — Eye Edition
==================

Точка входа пасьянса «Паук» с управлением глазами (MediaPipe FaceLandmarker):

  Взгляд влево / вправо — выбрать колонку (жёлтая рамка)
  Моргание              — взять верхние карты колонки / положить взятые
                          (моргнуть на той же колонке — отменить)
  Закрыть глаза на 1 с  — раздача из запаса

Выбор колонки взглядом — два режима (клавиша V):
  ВЗГЛЯД   — выбрана та колонка, на которую смотрите (после калибровки);
  ДЖОЙСТИК — взгляд в сторону сдвигает выбор на одну колонку, пока
             смотрите в сторону — сдвигает дальше. Надёжнее, если трекинг
             зрачков на вашей камере шумный.

Клавиши (работают всегда, в том числе без камеры):
  G — режим ввода ГЛАЗА → МЫШЬ → КЛАВИШИ     ← → — выбрать колонку
  ПРОБЕЛ — взять / положить    D — раздача    U — отменить ход
  N — новая партия    1 / 2 / 4 — новая партия с 1 / 2 / 4 мастями
  C — перекалибровать взгляд    K — окно камеры    ESC — выход
  Мышь: навести на колонку, ЛКМ — взять / положить, ПКМ — раздача
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
from core.camera_preview import CameraPreview, preview_rect
from core.gaze_calibration import GazeCalibration
from game import CARD_W, COL_STEP, COLUMNS, LEFT, SpiderGame, column_x

WINDOW_W, WINDOW_H = 1100, 800
FPS = 60

LONG_CLOSE = 1.0        # сек закрытых глаз — раздача
MIN_BLINK = 0.0         # сек; ~0.2 — не реагировать на быстрые непроизвольные моргания
GAZE_TAU = 0.2          # сглаживание взгляда
SWITCH_DWELL = 0.15     # ВЗГЛЯД: столько взгляд должен задержаться на новой колонке
SWITCH_MARGIN = 0.15    # ВЗГЛЯД: запас (в долях колонки) против дрожания на границе
STICK_ENTER = 0.3       # ДЖОЙСТИК: отклонение от центра (u), чтобы сдвинуть выбор
STICK_EXIT = 0.18
STICK_REPEAT = 0.6      # ДЖОЙСТИК: пауза между сдвигами, пока смотрите в сторону

MODES = ["ГЛАЗА", "МЫШЬ", "КЛАВИШИ"]


class EyeGestures:
    """Отличает короткое моргание от долгого закрытия глаз.
    Моргание засчитывается, когда глаза открылись (иначе долгое закрытие
    всегда начиналось бы с «моргания»), долгое закрытие — сразу по
    истечении LONG_CLOSE, не дожидаясь открытия."""

    def __init__(self):
        self.closed_since = None
        self.long_fired = False

    def update(self, now, face_detected, closed):
        if not face_detected:
            self.closed_since = None
            return None
        if closed:
            if self.closed_since is None:
                self.closed_since, self.long_fired = now, False
            elif not self.long_fired and now - self.closed_since >= LONG_CLOSE:
                self.long_fired = True
                return "long"
        elif self.closed_since is not None:
            duration = now - self.closed_since
            self.closed_since = None
            if not self.long_fired and duration >= MIN_BLINK:
                return "blink"
        return None

    def closed_for(self, now):
        return 0.0 if self.closed_since is None or self.long_fired else now - self.closed_since


class GazeColumnPicker:
    """u (0 — левая колонка, 1 — правая) → номер колонки, с защитой от дрожания."""

    def __init__(self):
        self.pending = None
        self.pending_since = 0.0
        self.stick_dir = 0
        self.next_step = 0.0

    def absolute(self, now, u, current):
        pos = max(0.0, min(1.0, u)) * (COLUMNS - 1)
        candidate = round(pos)
        if candidate == current or abs(pos - current) < 0.5 + SWITCH_MARGIN:
            self.pending = None
            return current
        if candidate != self.pending:
            self.pending, self.pending_since = candidate, now
        return candidate if now - self.pending_since >= SWITCH_DWELL else current

    def joystick(self, now, u):
        """Возвращает сдвиг выбора: -1, 0 или +1."""
        off = u - 0.5
        if self.stick_dir == 0:
            direction = -1 if off < -STICK_ENTER else (1 if off > STICK_ENTER else 0)
        else:
            direction = self.stick_dir if off * self.stick_dir > STICK_EXIT else 0
        if direction != self.stick_dir:
            self.stick_dir = direction
            self.next_step = now + 0.1
        if direction and now >= self.next_step:
            self.next_step = now + STICK_REPEAT
            return direction
        return 0


def draw_eye_hud(screen, font, gaze_u, closed_for, face_detected, gaze_mode):
    """Шкала взгляда (по верхнему краю окна) и прогресс «закройте глаза для раздачи»."""
    y = 7
    left, right = column_x(0) + CARD_W // 2, column_x(COLUMNS - 1) + CARD_W // 2
    pygame.draw.line(screen, (0, 40, 20), (left, y), (right, y), 4)
    if gaze_mode == "ДЖОЙСТИК":
        for u in (0.5 - STICK_ENTER, 0.5 + STICK_ENTER):
            x = left + u * (right - left)
            pygame.draw.line(screen, (200, 200, 200), (x, y - 8), (x, y + 8), 2)
    if gaze_u is not None:
        x = left + max(-0.05, min(1.05, gaze_u)) * (right - left)
        pygame.draw.circle(screen, (80, 230, 255), (x, y), 7)
    if not face_detected:
        txt = font.render("Лицо не найдено в кадре камеры", True, (255, 120, 120))
        box = txt.get_rect(center=(WINDOW_W // 2, WINDOW_H - 160)).inflate(24, 12)
        pygame.draw.rect(screen, (0, 0, 0), box, border_radius=8)
        screen.blit(txt, txt.get_rect(center=box.center))
    if closed_for > 0.25:
        k = min(1.0, closed_for / LONG_CLOSE)
        rect = pygame.Rect(0, 0, 100, 100)
        rect.center = (WINDOW_W // 2, WINDOW_H // 2)
        pygame.draw.circle(screen, (0, 0, 0), rect.center, 56)
        pygame.draw.arc(screen, (80, 230, 255), rect, math.pi / 2, math.pi / 2 + 2 * math.pi * k, 6)
        txt = font.render("раздача", True, (240, 240, 240))
        screen.blit(txt, txt.get_rect(center=rect.center))


def main():
    pygame.init()
    pygame.display.set_caption("Паук — Eye Edition")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font_small = pygame.font.SysFont("arial", 16)
    font = pygame.font.SysFont("arial", 20)
    font_big = pygame.font.SysFont("arial", 40, bold=True)

    game = SpiderGame(screen, WINDOW_W, WINDOW_H, reserved=preview_rect(WINDOW_H))
    calibration = GazeCalibration(font, font_big, column_x(0) + CARD_W // 2,
                                  column_x(COLUMNS - 1) + CARD_W // 2, WINDOW_H // 2)

    tracker = BlinkTracker(cam_index=0)
    tracker.start()
    preview = CameraPreview(tracker, WINDOW_H)
    gestures = EyeGestures()
    picker = GazeColumnPicker()

    mode = 0
    gaze_mode = "ВЗГЛЯД"
    smooth_gaze = None

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        now = time.time()
        eyes = MODES[mode] == "ГЛАЗА"

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_g:
                    mode = (mode + 1) % len(MODES)
                elif event.key == pygame.K_k:
                    preview.toggle()
                elif event.key == pygame.K_v:
                    gaze_mode = "ДЖОЙСТИК" if gaze_mode == "ВЗГЛЯД" else "ВЗГЛЯД"
                elif event.key == pygame.K_c:
                    calibration.restart()
                    mode = 0
                elif event.key == pygame.K_LEFT:
                    game.move_selection(-1)
                elif event.key == pygame.K_RIGHT:
                    game.move_selection(1)
                elif event.key == pygame.K_SPACE:
                    game.action()
                elif event.key == pygame.K_d:
                    game.deal()
                elif event.key == pygame.K_u:
                    game.undo()
                elif event.key == pygame.K_n:
                    game.reset()
                elif event.key in (pygame.K_1, pygame.K_2, pygame.K_4):
                    game.reset(suits={pygame.K_1: 1, pygame.K_2: 2, pygame.K_4: 4}[event.key])
            elif event.type == pygame.MOUSEBUTTONDOWN and MODES[mode] == "МЫШЬ":
                if event.button == 1:
                    game.action()
                elif event.button == 3:
                    game.deal()

        face_detected, _, closed, _ = tracker.get_state()
        calibrating = eyes and not calibration.done
        gaze_u = None

        if eyes:
            gaze = tracker.get_gaze()
            if calibrating:
                calibration.update(dt, face_detected, gaze)
                smooth_gaze = None
            else:
                gesture = gestures.update(now, face_detected, closed)
                if gesture == "blink":
                    game.action()
                elif gesture == "long":
                    game.deal()
                if gaze is not None:
                    alpha = 1 - math.exp(-dt / GAZE_TAU)
                    smooth_gaze = gaze if smooth_gaze is None else smooth_gaze + (gaze - smooth_gaze) * alpha
                    gaze_u = calibration.map(smooth_gaze)
                    if not closed:          # с закрытыми глазами выбор не двигаем
                        if gaze_mode == "ВЗГЛЯД":
                            game.select(picker.absolute(now, gaze_u, game.selected))
                        else:
                            game.move_selection(picker.joystick(now, gaze_u))
        elif MODES[mode] == "МЫШЬ":
            mx = pygame.mouse.get_pos()[0]
            game.select(int((mx - LEFT + (COL_STEP - CARD_W) / 2) // COL_STEP))

        game.update(dt)
        game.draw()

        if eyes and not calibration.done:     # done мог стать True в этом же кадре
            calibration.draw(screen, face_detected)
        elif eyes:
            draw_eye_hud(screen, font, gaze_u, gestures.closed_for(now), face_detected, gaze_mode)

        sub = f" / {gaze_mode} (V)" if eyes else ""
        mode_txt = (f"Ввод: {MODES[mode]}{sub}   G — сменить, C — калибровка, D — раздача, U — отмена, "
                    f"N / 1 / 2 / 4 — новая партия, K — камера")
        txt = font_small.render(mode_txt, True, (200, 225, 205))
        screen.blit(txt, (preview.rect.right + 12, WINDOW_H - 30))

        preview.draw(screen)
        pygame.display.flip()

    tracker.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
