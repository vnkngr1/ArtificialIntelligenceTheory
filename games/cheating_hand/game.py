"""
Симулятор списывания — версия «рукой».

Всё как в games/cheating (класс, учитель, задания, подозрение, оценки), но
шпаргалку достают не взглядом, а рукой: щипок — «вытащить шпаргалку из-под
парты». Пока пальцы сжаты, вы смотрите на шпаргалку в руке (учителя не
видно), разжали — шпаргалка спрятана, снова виден класс.

Шпаргалку держит нарисованная рука — за левый край, большой палец спереди, —
и листок следует за вашей рукой: его можно подвинуть, чтобы удобнее читать.
"""

import pygame

from games.cheating.game import CHEAT, CheatingGame

SKIN = (236, 196, 164)
SKIN_DARK = (206, 164, 132)
NAIL = (246, 216, 196)
SLEEVE = (40, 56, 104)
SLEEVE_DARK = (30, 42, 80)
SHEET_SCALE = 0.9           # в руке листок чуть меньше, чтобы рядом поместилась сама рука
PALM_ROOM = 170             # сколько места слева от листка занимает ладонь


class HandCheatingGame(CheatingGame):
    def __init__(self, screen, width, height, reserved=None):
        self.hand = (width / 2, height * 0.75)      # где рука игрока (координаты экрана)
        super().__init__(screen, width, height, reserved)

    def set_hand(self, pos):
        self.hand = pos

    def _render_cheat(self):
        sheet = super()._render_cheat()
        w, h = sheet.get_size()
        return pygame.transform.smoothscale(sheet, (int(w * SHEET_SCALE), int(h * SHEET_SCALE)))

    def _draw_cheat_sheet(self, oy):
        """Шпаргалка в руке: листок следует за рукой, ладонь слева, большой палец на листке."""
        s = self.screen
        sheet = self.cheat_surface
        half = sheet.get_width() / 2
        # листок не уезжает за край экрана, и слева всегда остаётся место для руки
        cx = min(max(self.hand[0], PALM_ROOM + half), self.width - 10 - half)
        dy = min(max((self.hand[1] - self.height * 0.7) * 0.3, -30), 30)
        rect = sheet.get_rect(midtop=(cx, CHEAT.top + oy + 10 + dy))
        gx, gy = rect.left + 16, rect.bottom - 20        # держит за нижний левый угол — там нет строк

        # рукав и ладонь — слева от листка, пальцы — за ним
        pygame.draw.polygon(s, SLEEVE, [(gx - 150, gy + 10), (gx - 70, gy + 60),
                                        (gx - 40, self.height + 40), (gx - 230, self.height + 40)])
        pygame.draw.line(s, SLEEVE_DARK, (gx - 150, gy + 10), (gx - 70, gy + 60), 6)
        pygame.draw.ellipse(s, SKIN_DARK, (gx - 158, gy - 62, 176, 124))
        pygame.draw.ellipse(s, SKIN, (gx - 154, gy - 66, 170, 118))

        s.blit(sheet, rect)

        # большой палец прижимает листок спереди
        thumb = pygame.Rect(gx - 28, gy - 58, 76, 40)
        pygame.draw.ellipse(s, SKIN_DARK, thumb.inflate(4, 4))
        pygame.draw.ellipse(s, SKIN, thumb)
        pygame.draw.ellipse(s, NAIL, (thumb.right - 30, thumb.y + 8, 22, 22))
