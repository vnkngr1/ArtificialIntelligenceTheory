"""
Лаунчер — единое меню всех игр проекта
======================================

Сам находит игры: каждая папка games/<игра>/ с файлами config.py и main.py —
это строка в списке. В config.py задаются название (NAME), квадратная иконка
(ICON), описание (DESCRIPTION), чем управлять (CONTROLS) и порядок (ORDER).
Чтобы добавить игру — достаточно положить новую папку, лаунчер трогать не нужно.

Игры показаны списком, который прокручивается:
  - щипок на игре и удержание, пока не заполнится круг, — запуск;
  - щипок ВНЕ игр (по бокам списка, между строками) и движение руки вверх/вниз —
    список тянется за рукой, после отпускания немного прокатывается по инерции;
    если щипок начат на игре, но рука заметно сдвинулась по вертикали — это тоже
    прокрутка, а не запуск.
Игра запускается отдельным процессом; лаунчер на это время отпускает камеру
(она нужна игре) и ждёт. ESC или жест «средний палец» в игре — возврат в меню.

Управление:
  Рука    — навести на игру, сжать пальцы (щипок) и держать ~0.6 с;
            щипок вне игр + движение вверх/вниз — прокрутить список
  Мышь    — клик по игре; перетаскивание или колесо — прокрутка
  ↑ ↓ + Enter — выбрать и запустить с клавиатуры
  K       — показать / спрятать окно камеры
  ESC     — выход
  Средний палец (показать камере и подержать) — выход
"""

import importlib.util
import math
import os
import subprocess
import sys
import time
from collections import deque

import pygame

from core.camera_preview import CameraPreview, hand_status, preview_rect
from core.display import open_window
from core.exit_gesture import ExitGesture
from core.gesture_tracker import GestureTracker, PinchHysteresis
from core.one_euro import OneEuroFilter2D

ROOT = os.path.dirname(os.path.abspath(__file__))
GAMES_DIR = os.path.join(ROOT, "games")

WINDOW_W, WINDOW_H = 1180, 760
FPS = 60
HOLD_TIME = 0.6          # сек удерживать щипок на игре для запуска
CAM_MIN_CUTOFF = 0.5     # фильтр One Euro для курсора руки (см. core/one_euro.py)
CAM_BETA = 10.0
CAM_MARGIN = 0.15
MOUSE_PRIORITY = 1.5     # сек после движения мыши курсор берётся от мыши, а не от руки
DRAG_START = 28          # px по вертикали: щипок на игре превращается в прокрутку
FRICTION = 5.0           # затухание инерции прокрутки, 1/с
WHEEL_STEP = 70

# область списка и текста меню — правее окна камеры (оно в левом нижнем углу)
CONTENT_LEFT, CONTENT_RIGHT = preview_rect(WINDOW_H).right + 24, WINDOW_W - 24
CONTENT_X = (CONTENT_LEFT + CONTENT_RIGHT) // 2
ITEM_W, ITEM_H, ITEM_GAP = 700, 128, 14
ICON_SIZE = ITEM_H - 28
LIST_X = CONTENT_X - ITEM_W // 2
VIEW = pygame.Rect(LIST_X - 12, 138, ITEM_W + 24, WINDOW_H - 138 - 52)   # видимая часть списка

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
        self.font_name = pygame.font.SysFont("arial", 25, bold=True)
        self.font = pygame.font.SysFont("arial", 17)
        self.font_small = pygame.font.SysFont("arial", 14, bold=True)
        self.font_icon = pygame.font.SysFont("arial", 60, bold=True)

        self.games = discover_games()
        self.selected = 0
        self.scroll = 0.0
        self.velocity = 0.0          # инерция прокрутки, px/с
        self.press = None            # текущий щипок / нажатие мыши (см. _press)
        self.message = None
        self._open_window()
        self.background = self._render_background()
        for g in self.games:
            g.icon = load_icon(g, ICON_SIZE, self.font_icon)
        self.exit_gesture = ExitGesture()
        self._start_tracker()
        self.preview = CameraPreview(self.tracker, WINDOW_H)

    # ---------- окно и камера ----------

    def _open_window(self):
        pygame.display.init()
        self.screen = open_window((WINDOW_W, WINDOW_H), "CV-игры — меню")

    def _start_tracker(self):
        self.tracker = GestureTracker(cam_index=0)
        self.tracker.start()
        self.pinch = PinchHysteresis()
        self.hand_filter = OneEuroFilter2D(CAM_MIN_CUTOFF, CAM_BETA)
        self.last_sample_t = 0.0
        self.hand_pos = (WINDOW_W // 2, WINDOW_H // 2)
        self.hand_detected = False
        self.pinching = False
        self.press = None
        self.mouse_until = 0.0
        self.cursor = self.hand_pos
        self.exit_gesture.reset()    # после игры: жест выхода засчитается, только когда рука опустится

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

    # ---------- список ----------

    @property
    def max_scroll(self):
        content = len(self.games) * (ITEM_H + ITEM_GAP) - ITEM_GAP
        return max(0.0, content - VIEW.height + 16)

    def item_rect(self, i):
        y = VIEW.top + 8 + i * (ITEM_H + ITEM_GAP) - self.scroll
        return pygame.Rect(LIST_X, round(y), ITEM_W, ITEM_H)

    def item_at(self, pos):
        if not VIEW.collidepoint(pos):
            return None
        for i in range(len(self.games)):
            if self.item_rect(i).collidepoint(pos):
                return i
        return None

    def _clamp_scroll(self):
        if self.scroll < 0 or self.scroll > self.max_scroll:
            self.velocity = 0.0
        self.scroll = min(max(self.scroll, 0.0), self.max_scroll)

    def _ensure_visible(self, i):
        r = self.item_rect(i)
        if r.top < VIEW.top:
            self.scroll -= VIEW.top - r.top + 8
        elif r.bottom > VIEW.bottom:
            self.scroll += r.bottom - VIEW.bottom + 8
        self._clamp_scroll()

    # ---------- щипок и мышь: нажать / вести / отпустить ----------

    def _press(self, pos, t, source):
        """Щипок (source="hand") или кнопка мыши (source="mouse") нажаты в точке pos."""
        item = self.item_at(pos)
        self.velocity = 0.0
        if item is not None:
            self.press = {"kind": "item", "item": item, "start": pos, "source": source, "hold": 0.0}
        else:
            self._start_drag(pos, t, source)

    def _start_drag(self, pos, t, source):
        self.press = {"kind": "drag", "start": pos, "start_scroll": self.scroll, "source": source,
                      "trail": deque([(t, pos[1])], maxlen=30)}

    def _move(self, pos, t):
        p = self.press
        if p is None:
            return
        if p["kind"] == "item" and abs(pos[1] - p["start"][1]) > DRAG_START:
            self._start_drag(pos, t, p["source"])       # потянули по вертикали — это прокрутка
            p = self.press
        if p["kind"] == "drag":
            self.scroll = p["start_scroll"] - (pos[1] - p["start"][1])     # список едет за рукой
            self._clamp_scroll()
            p["trail"].append((t, pos[1]))

    def _release(self, pos, t):
        p = self.press
        self.press = None
        if p is None:
            return None
        if p["kind"] == "drag":
            # скорость за последние ~0.12 с — для прокатки по инерции
            recent = [(tt, y) for tt, y in p["trail"] if tt >= t - 0.12]
            if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
                self.velocity = -(recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
        elif p["source"] == "mouse" and self.item_at(pos) == p["item"]:
            return p["item"]                            # клик мышью — запуск сразу
        return None

    def _read_hand(self):
        launch = None
        for sample in self.tracker.get_samples_since(self.last_sample_t):
            self.last_sample_t = sample.t
            self.hand_detected = sample.detected
            if sample.detected:
                self.hand_pos = cam_to_screen(*self.hand_filter(sample.x, sample.y, sample.t))
            closed = self.pinch.update(sample)
            hand_press = self.press is None or self.press["source"] == "hand"
            if closed and not self.pinching:
                if self.press is None:
                    self._press(self.hand_pos, sample.t, "hand")
            elif closed and hand_press:
                self._move(self.hand_pos, sample.t)
            elif self.pinching and hand_press:
                launch = self._release(self.hand_pos, sample.t)
            self.pinching = closed
        return launch

    # ---------- цикл ----------

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
                    elif event.key in (pygame.K_UP, pygame.K_DOWN) and self.games:
                        step = -1 if event.key == pygame.K_UP else 1
                        self.selected = max(0, min(len(self.games) - 1, self.selected + step))
                        self._ensure_visible(self.selected)
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE) and self.games:
                        launch = self.selected
                elif event.type == pygame.MOUSEMOTION:
                    self.mouse_until = now + MOUSE_PRIORITY
                    if self.press and self.press["source"] == "mouse":
                        self._move(event.pos, now)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.press is None:
                    self._press(event.pos, now, "mouse")
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    if self.press and self.press["source"] == "mouse":
                        launch = self._release(event.pos, now)
                elif event.type == pygame.MOUSEWHEEL:
                    self.scroll -= event.y * WHEEL_STEP
                    self.velocity = 0.0
                    self._clamp_scroll()

            hand_launch = self._read_hand()
            if launch is None:
                launch = hand_launch
            mouse_cursor = now < self.mouse_until or not self.hand_detected
            self.cursor = pygame.mouse.get_pos() if mouse_cursor else self.hand_pos

            dragging = self.press is not None and self.press["kind"] == "drag"
            if not dragging and abs(self.velocity) > 1:
                self.scroll += self.velocity * dt           # прокатка по инерции
                self.velocity *= math.exp(-FRICTION * dt)
                self._clamp_scroll()
            if not dragging:
                hovered = self.item_at(self.cursor)
                if hovered is not None:
                    self.selected = hovered

            # удержание щипка на игре — запуск
            p = self.press
            if p and p["kind"] == "item" and p["source"] == "hand":
                if self.item_at(self.hand_pos) == p["item"]:
                    p["hold"] = min(1.0, p["hold"] + dt / HOLD_TIME)
                    if p["hold"] >= 1.0:
                        launch = p["item"]
                else:
                    self.press = None                       # увели руку с игры — отмена

            if self.exit_gesture.update(dt, self.tracker):
                return
            if launch is not None:
                self.press = None
                self.launch(launch)
                continue

            self._draw()
            self.exit_gesture.draw(self.screen)
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
        self.screen.blit(title, title.get_rect(midtop=(CONTENT_X, 30)))
        if self.message:
            self._banner(self.message, (255, 150, 150))
        else:
            hint = "Щипок на игре и удержание — запуск.  Щипок вне игр и движение вверх/вниз — прокрутка"
            txt = self.font.render(hint, True, MUTED)
            self.screen.blit(txt, txt.get_rect(midtop=(CONTENT_X, 86)))

        if not self.games:
            txt = self.font_name.render("В папке games/ не найдено ни одной игры с config.py и main.py", True, TEXT)
            self.screen.blit(txt, txt.get_rect(center=(CONTENT_X, WINDOW_H // 2)))

        self._draw_list()

        footer = "Мышь: клик, колесо   ·   ↑ ↓ + Enter   ·   K — камера   ·   ESC или средний палец — выход"
        txt = self.font.render(footer, True, MUTED)
        self.screen.blit(txt, txt.get_rect(midbottom=(CONTENT_X, WINDOW_H - 14)))
        status = "рука в кадре" if self.hand_detected else "рука не найдена — можно мышью"
        color = (110, 220, 140) if self.hand_detected else (240, 130, 130)
        pygame.draw.circle(self.screen, color, (26, 28), 6)
        self.screen.blit(self.font.render(status, True, color), (38, 18))

        self.preview.draw(self.screen, hand_status(self.hand_detected, self.pinching, self.pinch.ratio))
        self._draw_cursor()

    def _draw_list(self):
        self.screen.set_clip(VIEW)
        hold_item = self.press["item"] if self.press and self.press["kind"] == "item" else None
        hold = self.press.get("hold", 0.0) if hold_item is not None else 0.0
        for i, game in enumerate(self.games):
            r = self.item_rect(i)
            if r.bottom < VIEW.top or r.top > VIEW.bottom:
                continue
            self._draw_item(game, r, i == self.selected, hold if i == hold_item else 0.0)
        self.screen.set_clip(None)

        # затемнение у краёв видимой области — видно, что список продолжается
        for edge, top in ((VIEW.top, True), (VIEW.bottom - 24, False)):
            fade = pygame.Surface((VIEW.width, 24), pygame.SRCALPHA)
            for y in range(24):
                a = int(170 * (1 - y / 24)) if top else int(170 * y / 24)
                pygame.draw.line(fade, (16, 18, 28, a), (0, y), (VIEW.width, y))
            if (top and self.scroll > 1) or (not top and self.scroll < self.max_scroll - 1):
                self.screen.blit(fade, (VIEW.left, edge))

        # полоса прокрутки справа от списка
        if self.max_scroll > 0:
            track = pygame.Rect(VIEW.right + 10, VIEW.top + 8, 6, VIEW.height - 16)
            pygame.draw.rect(self.screen, (50, 55, 78), track, border_radius=3)
            k = VIEW.height / (VIEW.height + self.max_scroll)
            thumb_h = max(40, int(track.height * k))
            thumb_y = track.top + (track.height - thumb_h) * (self.scroll / self.max_scroll)
            dragging = self.press is not None and self.press["kind"] == "drag"
            pygame.draw.rect(self.screen, ACCENT if dragging else (130, 136, 170),
                             (track.left, thumb_y, track.width, thumb_h), border_radius=3)

    def _draw_item(self, game, r, active, hold):
        pygame.draw.rect(self.screen, (8, 9, 14), r.move(0, 5), border_radius=20)
        pygame.draw.rect(self.screen, TILE_HOVER if active else TILE, r, border_radius=20)
        if active:
            pygame.draw.rect(self.screen, ACCENT, r, 3, border_radius=20)

        self.screen.blit(game.icon, (r.left + 14, r.top + 14))
        x = r.left + 14 + ICON_SIZE + 22
        name = self.font_name.render(game.name, True, TEXT)
        self.screen.blit(name, (x, r.top + 20))
        y = r.top + 58
        for line in wrap(game.description, self.font, r.right - x - 24):
            self.screen.blit(self.font.render(line, True, MUTED), (x, y))
            y += 22

        if game.controls:
            badge = self.font_small.render(game.controls, True, TEXT)
            box = badge.get_rect().inflate(16, 8)
            box.topright = (r.right - 14, r.top + 14)
            pygame.draw.rect(self.screen, BADGE_COLORS.get(game.controls, (110, 110, 130)), box, border_radius=10)
            self.screen.blit(badge, badge.get_rect(center=box.center))

        if hold > 0:
            bar = pygame.Rect(x, r.bottom - 16, int((r.right - x - 20) * hold), 6)
            pygame.draw.rect(self.screen, ACCENT, bar, border_radius=3)

    def _draw_cursor(self):
        x, y = self.cursor
        color = (255, 80, 80) if self.pinching else (255, 255, 255)
        pygame.draw.circle(self.screen, (0, 0, 0), (x, y), 15, 5)
        pygame.draw.circle(self.screen, color, (x, y), 14, 3)
        p = self.press
        if p and p["kind"] == "item" and p.get("hold", 0) > 0:
            rect = pygame.Rect(0, 0, 44, 44)
            rect.center = (x, y)
            pygame.draw.arc(self.screen, ACCENT, rect, math.pi / 2, math.pi / 2 + 2 * math.pi * p["hold"], 5)
        elif p and p["kind"] == "drag":
            for d in (-1, 1):                                   # стрелки «вверх-вниз» у курсора
                tip = y + d * 30
                pygame.draw.polygon(self.screen, ACCENT, [(x, tip), (x - 8, tip - d * 10), (x + 8, tip - d * 10)])

    def _banner(self, text, color=TEXT):
        txt = self.font_name.render(text, True, color)
        box = txt.get_rect(center=(CONTENT_X, 98)).inflate(30, 12)
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
