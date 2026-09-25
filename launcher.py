"""
Лаунчер — единое меню всех игр проекта
======================================

Сам находит игры: каждая папка games/<игра>/ с файлами config.py и main.py —
это плитка в меню. В config.py задаются название (NAME), квадратная иконка
(ICON), описание (DESCRIPTION), чем управлять (CONTROLS) и порядок (ORDER).
Чтобы добавить игру — достаточно положить новую папку, лаунчер трогать не нужно.

Выбор игры рукой: указательный палец ведёт курсор, щипок на плитке и
удержание, пока не заполнится круг, — запуск. Игра запускается отдельным
процессом; лаунчер на это время отпускает камеру (она нужна игре) и ждёт.
ESC в игре — возврат в меню.

Управление:
  Рука    — навести на игру, сжать пальцы (щипок) и держать ~0.6 с
  Мышь    — клик по плитке
  ← → ↑ ↓ + Enter — выбрать и запустить с клавиатуры
  K       — показать / спрятать окно камеры
  ESC     — выход
"""

import importlib.util
import math
import os
import subprocess
import sys
import time

import pygame

from core.camera_preview import CameraPreview, hand_status, preview_rect
from core.gesture_tracker import GestureTracker, PinchHysteresis

ROOT = os.path.dirname(os.path.abspath(__file__))
GAMES_DIR = os.path.join(ROOT, "games")

WINDOW_W, WINDOW_H = 1180, 760
FPS = 60
HOLD_TIME = 0.6          # сек удерживать щипок на плитке для запуска
CAM_SMOOTHING = 0.5
CAM_MARGIN = 0.15
MOUSE_PRIORITY = 1.5     # сек после движения мыши курсор берётся от мыши, а не от руки

# область плиток и текста меню — правее окна камеры (оно в левом нижнем углу)
CONTENT_LEFT, CONTENT_RIGHT = preview_rect(WINDOW_H).right + 24, WINDOW_W - 24
CONTENT_X = (CONTENT_LEFT + CONTENT_RIGHT) // 2

BG_TOP = (30, 34, 52)
BG_BOTTOM = (14, 16, 26)
TILE = (40, 45, 66)
TILE_HOVER = (56, 63, 92)
ACCENT = (255, 205, 80)
TEXT = (240, 242, 248)
MUTED = (150, 156, 180)
BADGE_COLORS = {"рука": (70, 160, 110), "глаза": (90, 120, 210)}


# ---------- игры ----------

class GameEntry:
    def __init__(self, folder, config):
        self.folder = folder
        self.path = os.path.join(GAMES_DIR, folder)
        self.main = os.path.join(self.path, "main.py")
        self.name = getattr(config, "NAME", folder)
        self.description = getattr(config, "DESCRIPTION", "")
        self.controls = getattr(config, "CONTROLS", "")
        self.order = getattr(config, "ORDER", 100)
        icon = getattr(config, "ICON", None)
        self.icon_path = os.path.join(self.path, icon) if icon else None
        self.icon = None


def discover_games():
    """Все папки games/*/ с config.py и main.py, по порядку ORDER."""
    games = []
    for folder in sorted(os.listdir(GAMES_DIR)):
        cfg_path = os.path.join(GAMES_DIR, folder, "config.py")
        if not (os.path.isfile(cfg_path) and os.path.isfile(os.path.join(GAMES_DIR, folder, "main.py"))):
            continue
        # у каждой игры свой config.py — грузим под уникальным именем модуля
        spec = importlib.util.spec_from_file_location(f"games_config_{folder}", cfg_path)
        config = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(config)
        except Exception as exc:
            print(f"[launcher] Пропускаю {folder}: ошибка в config.py — {exc}")
            continue
        games.append(GameEntry(folder, config))
    games.sort(key=lambda g: (g.order, g.name))
    return games


def load_icon(game, size, font):
    """Иконка из config.ICON; если её нет — цветной квадрат с первой буквой названия."""
    if game.icon_path and os.path.isfile(game.icon_path):
        try:
            return pygame.transform.smoothscale(pygame.image.load(game.icon_path).convert_alpha(), (size, size))
        except pygame.error as exc:
            print(f"[launcher] Не удалось загрузить иконку {game.icon_path}: {exc}")
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    hue = sum(map(ord, game.folder)) % 360
    color = pygame.Color(0)
    color.hsva = (hue, 55, 75, 100)
    pygame.draw.rect(surf, color, (0, 0, size, size), border_radius=size // 5)
    letter = font.render(game.name[:1].upper(), True, TEXT)
    surf.blit(letter, letter.get_rect(center=(size // 2, size // 2)))
    return surf


# ---------- раскладка ----------

def layout(n):
    """Прямоугольники плиток: сетка под заголовком, справа от окна камеры."""
    cols = 3 if n <= 6 else 4
    rows = max(1, math.ceil(n / cols))
    gap, top, bottom = 26, 150, 70
    left, right = CONTENT_LEFT, CONTENT_RIGHT
    tile_w = min(290, (right - left - gap * (cols - 1)) // cols)
    tile_h = min(int(tile_w * 1.05), (WINDOW_H - top - bottom - gap * (rows - 1)) // rows)
    total_h = rows * tile_h + gap * (rows - 1)
    y0 = top + (WINDOW_H - top - bottom - total_h) // 2
    rects = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        x0 = left + (right - left - (in_row * tile_w + gap * (in_row - 1))) // 2
        rects.append(pygame.Rect(x0 + c * (tile_w + gap), y0 + r * (tile_h + gap), tile_w, tile_h))
    return rects


def wrap(text, font, width, max_lines=2):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if font.size(candidate)[0] <= width:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines[:max_lines]


def cam_to_screen(x, y):
    def norm(v):
        return min(max((v - CAM_MARGIN) / (1 - 2 * CAM_MARGIN), 0.0), 1.0)
    return int(norm(x) * (WINDOW_W - 1)), int(norm(y) * (WINDOW_H - 1))


class Launcher:
    def __init__(self):
        pygame.init()
        self.font_title = pygame.font.SysFont("arial", 40, bold=True)
        self.font_name = pygame.font.SysFont("arial", 23, bold=True)
        self.font = pygame.font.SysFont("arial", 17)
        self.font_small = pygame.font.SysFont("arial", 14, bold=True)
        self.font_icon = pygame.font.SysFont("arial", 90, bold=True)

        self.games = discover_games()
        self.rects = layout(len(self.games))
        self.selected = 0
        self.message = None
        self._open_window()
        self.background = self._render_background()
        # иконка — сколько влезет над названием и двумя строками описания
        icon_size = min(int(min(r.width for r in self.rects) * 0.58),
                        min(r.height for r in self.rects) - 122) if self.rects else 150
        for g in self.games:
            g.icon = load_icon(g, icon_size, self.font_icon)
        self._start_tracker()
        self.preview = CameraPreview(self.tracker, WINDOW_H)

    # ---------- окно и камера ----------

    def _open_window(self):
        pygame.display.init()
        pygame.display.set_caption("CV-игры — меню")
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))

    def _start_tracker(self):
        self.tracker = GestureTracker(cam_index=0)
        self.tracker.start()
        self.pinch = PinchHysteresis()
        self.last_sample_t = 0.0
        self.hand_x = self.hand_y = 0.5
        self.hand_detected = False
        self.pinching = False
        self.hold_tile = None        # плитка, на которой начат щипок
        self.hold = 0.0              # прогресс удержания 0..1
        self.mouse_until = 0.0
        self.cursor = (WINDOW_W // 2, WINDOW_H // 2)

    def launch(self, index):
        game = self.games[index]
        self._draw()
        self._banner(f"Запуск: {game.name}…")
        pygame.display.flip()

        # камеру может держать только один процесс — отпускаем её на время игры
        self.tracker.stop()
        pygame.display.quit()
        result = subprocess.run([sys.executable, game.main], cwd=game.path)

        self._open_window()
        self._start_tracker()
        self.preview.set_tracker(self.tracker)
        pygame.event.clear()
        if result.returncode != 0:
            self.message = f"«{game.name}» завершилась с ошибкой (код {result.returncode}) — подробности в консоли"
        else:
            self.message = None

    # ---------- ввод ----------

    def tile_at(self, pos):
        for i, rect in enumerate(self.rects):
            if rect.collidepoint(pos):
                return i
        return None

    def _read_hand(self):
        for sample in self.tracker.get_samples_since(self.last_sample_t):
            self.last_sample_t = sample.t
            self.hand_detected = sample.detected
            if sample.detected:
                self.hand_x += (sample.x - self.hand_x) * CAM_SMOOTHING
                self.hand_y += (sample.y - self.hand_y) * CAM_SMOOTHING
            closed = self.pinch.update(sample)
            if closed and not self.pinching:
                # удержание засчитываем, только если щипок начался на плитке
                self.hold_tile = self.tile_at(cam_to_screen(self.hand_x, self.hand_y))
                self.hold = 0.0
            elif not closed:
                self.hold_tile = None
                self.hold = 0.0
            self.pinching = closed

    def run(self):
        clock = pygame.time.Clock()
        while True:
            dt = clock.tick(FPS) / 1000.0
            now = time.time()
            launch = None

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return
                    if event.key == pygame.K_k:
                        self.preview.toggle()
                    cols = 3 if len(self.games) <= 6 else 4
                    step = {pygame.K_LEFT: -1, pygame.K_RIGHT: 1, pygame.K_UP: -cols, pygame.K_DOWN: cols}
                    if event.key in step and self.games:
                        self.selected = max(0, min(len(self.games) - 1, self.selected + step[event.key]))
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE) and self.games:
                        launch = self.selected
                elif event.type == pygame.MOUSEMOTION:
                    self.mouse_until = now + MOUSE_PRIORITY
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    tile = self.tile_at(event.pos)
                    if tile is not None:
                        launch = tile

            self._read_hand()
            if now < self.mouse_until or not self.hand_detected:
                self.cursor = pygame.mouse.get_pos()
            else:
                self.cursor = cam_to_screen(self.hand_x, self.hand_y)
            hovered = self.tile_at(self.cursor)
            if hovered is not None:
                self.selected = hovered

            if self.pinching and self.hold_tile is not None:
                if hovered == self.hold_tile:
                    self.hold = min(1.0, self.hold + dt / HOLD_TIME)
                    if self.hold >= 1.0:
                        launch = self.hold_tile
                else:
                    self.hold_tile, self.hold = None, 0.0     # увели руку с плитки — отмена

            if launch is not None:
                self.launch(launch)
                continue

            self._draw()
            pygame.display.flip()

    # ---------- отрисовка ----------

    def _render_background(self):
        surf = pygame.Surface((WINDOW_W, WINDOW_H))
        for y in range(WINDOW_H):
            k = y / WINDOW_H
            pygame.draw.line(surf, [int(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)], (0, y), (WINDOW_W, y))
        return surf

    def _draw(self):
        self.screen.blit(self.background, (0, 0))
        title = self.font_title.render("CV-игры", True, TEXT)
        self.screen.blit(title, title.get_rect(midtop=(CONTENT_X, 34)))
        if self.message:
            self._banner(self.message, (255, 150, 150))
        else:
            hint = "Наведите палец на игру, сожмите пальцы (щипок) и держите, пока не заполнится круг"
            txt = self.font.render(hint, True, MUTED)
            self.screen.blit(txt, txt.get_rect(midtop=(CONTENT_X, 88)))

        if not self.games:
            txt = self.font_name.render("В папке games/ не найдено ни одной игры с config.py и main.py", True, TEXT)
            self.screen.blit(txt, txt.get_rect(center=(CONTENT_X, WINDOW_H // 2)))

        for i, (game, rect) in enumerate(zip(self.games, self.rects)):
            self._draw_tile(game, rect, i == self.selected, self.hold if i == self.hold_tile else 0.0)

        footer = "Мышь: клик по плитке   ·   Стрелки + Enter   ·   K — камера   ·   ESC — выход   ·   ESC в игре — назад в меню"
        txt = self.font.render(footer, True, MUTED)
        self.screen.blit(txt, txt.get_rect(midbottom=(CONTENT_X, WINDOW_H - 16)))
        status = "рука в кадре" if self.hand_detected else "рука не найдена — можно мышью"
        color = (110, 220, 140) if self.hand_detected else (240, 130, 130)
        pygame.draw.circle(self.screen, color, (26, 28), 6)
        self.screen.blit(self.font.render(status, True, color), (38, 18))

        self.preview.draw(self.screen, hand_status(self.hand_detected, self.pinching))

        # курсор: кольцо, при щипке — красное с заполняющейся дугой удержания
        x, y = self.cursor
        color = (255, 80, 80) if self.pinching else (255, 255, 255)
        pygame.draw.circle(self.screen, (0, 0, 0), (x, y), 15, 5)
        pygame.draw.circle(self.screen, color, (x, y), 14, 3)
        if self.hold > 0:
            rect = pygame.Rect(0, 0, 44, 44)
            rect.center = (x, y)
            pygame.draw.arc(self.screen, ACCENT, rect, math.pi / 2, math.pi / 2 + 2 * math.pi * self.hold, 5)

    def _draw_tile(self, game, rect, active, hold):
        r = rect.inflate(10, 10) if active else rect
        shadow = r.move(0, 6)
        pygame.draw.rect(self.screen, (8, 9, 14), shadow, border_radius=22)
        pygame.draw.rect(self.screen, TILE_HOVER if active else TILE, r, border_radius=22)
        if active:
            pygame.draw.rect(self.screen, ACCENT, r, 3, border_radius=22)

        icon = game.icon
        self.screen.blit(icon, icon.get_rect(midtop=(r.centerx, r.top + 18)))
        y = r.top + 18 + icon.get_height() + 12
        name = self.font_name.render(game.name, True, TEXT)
        self.screen.blit(name, name.get_rect(midtop=(r.centerx, y)))
        y += 32
        for line in wrap(game.description, self.font, r.width - 28):
            txt = self.font.render(line, True, MUTED)
            self.screen.blit(txt, txt.get_rect(midtop=(r.centerx, y)))
            y += 21

        if game.controls:
            badge = self.font_small.render(game.controls, True, TEXT)
            box = badge.get_rect(topright=(r.right - 14, r.top + 14)).inflate(16, 8)
            pygame.draw.rect(self.screen, BADGE_COLORS.get(game.controls, (110, 110, 130)), box, border_radius=10)
            self.screen.blit(badge, badge.get_rect(center=box.center))

        if hold > 0:
            bar = pygame.Rect(r.left + 16, r.bottom - 14, int((r.width - 32) * hold), 6)
            pygame.draw.rect(self.screen, ACCENT, bar, border_radius=3)

    def _banner(self, text, color=TEXT):
        txt = self.font_name.render(text, True, color)
        box = txt.get_rect(center=(CONTENT_X, 100)).inflate(30, 12)
        pygame.draw.rect(self.screen, (0, 0, 0), box, border_radius=10)
        self.screen.blit(txt, txt.get_rect(center=box.center))


def main():
    launcher = Launcher()
    try:
        launcher.run()
    finally:
        launcher.tracker.stop()
        pygame.quit()


if __name__ == "__main__":
    main()
