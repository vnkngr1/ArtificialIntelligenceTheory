"""
Окно веб-камеры внутри интерфейса игры — в левом нижнем углу.

Показывает то, что видит трекер (GestureTracker или BlinkTracker): кадр с
нарисованной рукой или с отмеченными глазами, и подпись-статус («ЩИПОК»,
«НЕТ РУКИ», «ГЛАЗА ЗАКРЫТЫ»…). Пока камера запускается — заглушка
«Запуск камеры…», если камера недоступна — текст ошибки.

    preview = CameraPreview(tracker, WINDOW_H)
    ...
    preview.draw(screen)          # после отрисовки игры, каждый кадр
    preview.toggle()              # спрятать / показать (клавиша K)

preview_rect(высота_окна) — где окно будет нарисовано; игры передают этот
прямоугольник в свою логику, чтобы не ставить в этот угол важные объекты.
"""

import pygame

PREVIEW_SIZE = (168, 126)   # 4:3
MARGIN = 8

PINCH_COLOR = (90, 230, 120)
HAND_COLOR = (90, 200, 255)
NO_HAND_COLOR = (255, 110, 110)


def preview_rect(screen_height):
    """Прямоугольник окна камеры в левом нижнем углу."""
    w, h = PREVIEW_SIZE
    return pygame.Rect(MARGIN, screen_height - h - MARGIN, w, h)


def hand_status(detected, pinching, ratio=None):
    """Подпись окна камеры для рук. ratio — отношение «расстояние между пальцами /
    размер руки» (PinchHysteresis.ratio): по нему удобно подбирать пороги щипка."""
    suffix = f"  {ratio:.2f}" if ratio is not None and detected else ""
    if pinching:
        return "ЩИПОК" + suffix, PINCH_COLOR
    return ("РУКА" + suffix, HAND_COLOR) if detected else ("НЕТ РУКИ", NO_HAND_COLOR)


class CameraPreview:
    def __init__(self, tracker, screen_height, visible=True):
        self.tracker = tracker
        self.rect = preview_rect(screen_height)
        self.visible = visible
        self.font = pygame.font.SysFont("arial", 12, bold=True)
        self.font_msg = pygame.font.SysFont("arial", 14)
        self._frame_id = None
        self._surface = None
        self._mask = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        pygame.draw.rect(self._mask, (255, 255, 255, 255), self._mask.get_rect(), border_radius=10)

    def set_tracker(self, tracker):
        """Трекер пересоздан (например, лаунчер вернулся из игры) — показываем кадры нового."""
        self.tracker = tracker
        self._frame_id = None
        self._surface = None

    def toggle(self):
        self.visible = not self.visible

    def draw(self, screen, status=None):
        """status — (текст, цвет) подписи; None — подпись по состоянию трекера."""
        if not self.visible:
            return
        r = self.rect
        pygame.draw.rect(screen, (10, 10, 14), r.inflate(6, 6), border_radius=13)

        frame_id, frame = self.tracker.get_preview()
        if frame is not None and frame_id != self._frame_id:
            self._frame_id = frame_id
            self._surface = self._to_surface(frame)

        if self._surface is not None:
            screen.blit(self._surface, r)
        else:
            error = getattr(self.tracker, "error", None)
            text = error or "Запуск камеры…"
            txt = self.font_msg.render(text, True, (255, 130, 130) if error else (200, 205, 215))
            screen.blit(txt, txt.get_rect(center=r.center))

        pygame.draw.rect(screen, (90, 96, 120), r.inflate(2, 2), 2, border_radius=11)
        if self._surface is None:
            return                                  # пока кадров нет — без подписи
        if status is None:
            status = self.tracker.preview_status()
        if status:
            text, color = status
            label = self.font.render(text, True, (15, 15, 20))
            box = label.get_rect().inflate(10, 4)
            box.topleft = (r.left + 5, r.top + 5)
            pygame.draw.rect(screen, color, box, border_radius=6)
            screen.blit(label, label.get_rect(center=box.center))

    def _to_surface(self, frame):
        """numpy RGB → поверхность размера окна: заполнить с обрезкой по центру, скруглить углы."""
        h, w = frame.shape[:2]
        image = pygame.image.frombuffer(frame.tobytes(), (w, h), "RGB")
        pw, ph = self.rect.size
        k = max(pw / w, ph / h)
        scaled = pygame.transform.smoothscale(image, (max(pw, round(w * k)), max(ph, round(h * k))))
        surf = pygame.Surface((pw, ph), pygame.SRCALPHA)
        surf.blit(scaled, ((pw - scaled.get_width()) // 2, (ph - scaled.get_height()) // 2))
        surf.blit(self._mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        return surf
