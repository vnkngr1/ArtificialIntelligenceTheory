"""
Окно игры на весь экран устройства.

Каждая игра рисует в своём «логическом» разрешении (например, 1100×760 —
вся её раскладка рассчитана на него), а pygame сам масштабирует картинку
на весь экран с сохранением пропорций (флаг SCALED; по краям — чёрные
полосы, если пропорции экрана другие). Координаты мыши pygame тоже
пересчитывает в логические, так что в коде игр ничего менять не нужно.

Переменная окружения CV_GAMES_WINDOWED=1 — открыть обычным окном
(удобно при отладке).
"""

import os
import sys

import pygame


def _dpi_aware():
    """Windows: без этого при масштабе 125–150% экран «виден» меньшим и картинка мылится."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def open_window(size, caption):
    """Открывает окно на весь экран; size — логическое разрешение игры. Возвращает поверхность."""
    _dpi_aware()
    os.environ.setdefault("SDL_RENDER_SCALE_QUALITY", "1")     # сглаженное, а не «пиксельное» увеличение
    pygame.display.set_caption(caption)
    flags = pygame.SCALED
    if os.environ.get("CV_GAMES_WINDOWED") != "1":
        flags |= pygame.FULLSCREEN
    try:
        return pygame.display.set_mode(size, flags)
    except pygame.error as exc:                                # видеодрайвер не умеет масштабировать
        print("[display] Полноэкранный режим недоступен, открываю обычное окно:", exc)
        return pygame.display.set_mode(size)
