"""
Пинг-понг на двоих по локальной сети — игровая логика.

Физика и правила — те же, что в «Пинг-понге на двоих» (games/ping_pong_2p):
класс наследуется от PingPong2PGame. Добавлено только сетевое:

  - считает игру один хост (игрок слева в «мире» игры): он двигает мяч,
    засчитывает удары и очки и каждый кадр отдаёт состояние — to_state();
  - клиент (игрок справа) ничего не считает: применяет пришедшее состояние —
    apply_state(), а свою ракетку рисует сразу по своей руке, без задержки сети;
  - каждый видит себя слева и бьёт вправо: у клиента картинка стола зеркальна,
    поэтому взмах одинаковый у обоих. Надписи — с точки зрения игрока:
    «Вы» и «Соперник».
"""

import pygame

from games.ping_pong_2p.game import (COLORS, LEFT, NET_X, OVER, RALLY, RIGHT, SERVE, SIDES, TABLE,
                                     TABLE_BLUE, TABLE_EDGE, TEXT, MUTED, WHITE, PingPong2PGame, other)


class LanPingPong(PingPong2PGame):
    def __init__(self, screen, width, height, me):
        """me — сторона этого игрока в «мире» игры: LEFT у хоста, RIGHT у клиента."""
        self.me = me
        self.mirror = me == RIGHT
        self.world = pygame.Surface((width, height))
        self.point_seq = 0
        self.last_point = None          # (номер, кто выиграл очко, причина) — уходит клиенту
        self.shown_point = 0
        self.status_line = ""           # «Связь: 12 мс» — задаёт main.py
        super().__init__(screen, width, height)

    def reset_match(self):
        super().reset_match()
        self.point_seq = self.shown_point = 0
        self.last_point = None

    # ---------- хост ----------

    def _point(self, winner, reason):
        if self.phase != RALLY:
            return
        super()._point(winner, reason)
        self.popups.pop()               # у родителя подпись «Игрок 1/2», у нас — «Вы / Соперник»
        self.point_seq += 1
        self.last_point = (self.point_seq, winner, reason)
        self._show_point()

    def to_state(self):
        """Состояние для клиента — всё, что нужно, чтобы нарисовать кадр."""
        b = self.ball

        def hit(side):
            h = self.last_hit[side]
            return [round(v) for v in h] if h else None

        return {
            "phase": self.phase,
            "score": [self.score[LEFT], self.score[RIGHT]],
            "wins": [self.wins[LEFT], self.wins[RIGHT]],
            "ball": [round(b.x, 1), round(b.y, 1), round(b.z, 1)],
            "pads": [[round(v) for v in self.paddle[LEFT]], [round(v) for v in self.paddle[RIGHT]]],
            "power": [round(self.swing[LEFT][2]), round(self.swing[RIGHT][2])],
            "hitter": self.hitter,
            "hits": [hit(LEFT), hit(RIGHT)],
            "winner": self.winner,
            "point": self.last_point,
        }

    # ---------- клиент ----------

    def apply_state(self, st):
        self.phase = st["phase"]
        self.score = {LEFT: st["score"][0], RIGHT: st["score"][1]}
        self.wins = {LEFT: st["wins"][0], RIGHT: st["wins"][1]}
        self.ball.x, self.ball.y, self.ball.z = st["ball"]
        opp = other(self.me)
        i = 0 if opp == LEFT else 1
        self.prev_paddle[opp] = self.paddle[opp]
        self.paddle[opp] = tuple(st["pads"][i])
        self.swing[opp] = (0.0, 0.0, float(st["power"][i]))
        self.hitter = st["hitter"]
        self.last_hit = {LEFT: tuple(st["hits"][0]) if st["hits"][0] else None,
                         RIGHT: tuple(st["hits"][1]) if st["hits"][1] else None}
        self.winner = st["winner"]
        if st["point"] and st["point"][0] != self.shown_point:
            self.last_point = tuple(st["point"])
            self._show_point()
        if self.phase == SERVE:
            self.ball.trail.clear()

    def tick(self, dt):
        """Клиент: только время для всплывающих надписей — остальное считает хост."""
        self.now += dt
        self.popups = [p for p in self.popups if self.now - p["t0"] < 1.4]

    # ---------- отрисовка ----------

    def _show_point(self):
        seq, winner, reason = self.last_point
        self.shown_point = seq
        mine = winner == self.me
        self.popups.append({"text": "+1 вам" if mine else "+1 сопернику",
                            "sub": ("Соперник" if mine else "Вы") + f": {reason}",
                            "color": COLORS[winner], "t0": self.now, "pos": (TABLE.centerx, TABLE.top - 70)})

    def draw(self):
        # стол, мяч и ракетки — в отдельную поверхность; у клиента она зеркальна, текст — нет
        screen = self.screen
        self.screen = self.world
        try:
            self.world.blit(self.background, (0, 0))
            self._draw_table()
            self._draw_ball_shadow()
            for s in SIDES:
                self._draw_paddle(s)
            self._draw_ball()
        finally:
            self.screen = screen
        screen.blit(pygame.transform.flip(self.world, True, False) if self.mirror else self.world, (0, 0))

        for label, x in (("Вы", TABLE.left + TABLE.width // 4), ("Соперник", TABLE.right - TABLE.width // 4)):
            txt = self.font_small.render(label, True, (255, 255, 255))
            txt.set_alpha(70)
            screen.blit(txt, txt.get_rect(center=(x, TABLE.bottom - 22)))
        self._draw_hud()
        for p in self.popups:
            age = (self.now - p["t0"]) / 1.4
            alpha = int(255 * min(1.0, 2 * (1 - age)))
            x, y = p["pos"]
            for text, font, color, dy in ((p["text"], self.font_big, p["color"], 0), (p["sub"], self.font, TEXT, 44)):
                txt = font.render(text, True, color)
                txt.set_alpha(alpha)
                screen.blit(txt, txt.get_rect(center=(x, y + dy - 20 * age)))
        if self.phase == OVER:
            self._draw_over()

    def _draw_table(self):
        """Как у родителя, но без подписей половин: у клиента стол зеркальный, текст перевернулся бы."""
        pygame.draw.rect(self.screen, (20, 22, 28), TABLE.move(8, 10), border_radius=4)
        pygame.draw.rect(self.screen, TABLE_BLUE, TABLE)
        pygame.draw.rect(self.screen, TABLE_EDGE, TABLE, 3)
        inner = TABLE.inflate(-10, -10)
        pygame.draw.rect(self.screen, WHITE, inner, 4)
        pygame.draw.line(self.screen, WHITE, (inner.left, TABLE.centery), (inner.right, TABLE.centery), 2)
        pygame.draw.line(self.screen, (10, 10, 14), (NET_X + 5, TABLE.top - 18), (NET_X + 5, TABLE.bottom + 18), 6)
        pygame.draw.line(self.screen, (225, 225, 230), (NET_X, TABLE.top - 18), (NET_X, TABLE.bottom + 18), 5)
        for y in range(TABLE.top - 12, TABLE.bottom + 14, 12):
            pygame.draw.line(self.screen, (160, 160, 170), (NET_X - 2, y), (NET_X + 2, y), 1)
        for y in (TABLE.top - 20, TABLE.bottom + 20):
            pygame.draw.circle(self.screen, (60, 60, 66), (NET_X, y), 7)

    def _draw_hud(self):
        me, opp = self.me, other(self.me)
        cx = self.width // 2
        mine = self.font_score.render(str(self.score[me]), True, COLORS[me])
        theirs = self.font_score.render(str(self.score[opp]), True, COLORS[opp])
        colon = self.font_score.render(":", True, TEXT)
        self.screen.blit(colon, colon.get_rect(midtop=(cx, 14)))
        self.screen.blit(mine, mine.get_rect(topright=(cx - 22, 14)))
        self.screen.blit(theirs, theirs.get_rect(topleft=(cx + 22, 14)))

        for side, label, x, align in ((me, "Вы", 24, "left"), (opp, "Соперник", self.width - 24, "right")):
            if self.phase == SERVE and self.server == side:
                label += "  · подача"
            txt = self.font.render(label, True, COLORS[side])
            rect = txt.get_rect(topleft=(x, 16)) if align == "left" else txt.get_rect(topright=(x, 16))
            self.screen.blit(txt, rect)
            self._draw_meter(side, x if align == "left" else x - 240, 52)

        info = f"Партии: {self.wins[me]} : {self.wins[opp]}"
        if self.status_line:
            info += f"   ·   {self.status_line}"
        txt = self.font_small.render(info, True, MUTED)
        self.screen.blit(txt, txt.get_rect(midtop=(cx, 76)))
        hint = "Взмах вправо, к сопернику — удар; сильнее взмах — сильнее удар. Подача — тоже взмахом."
        txt = self.font_small.render(hint, True, MUTED)
        self.screen.blit(txt, txt.get_rect(midtop=(cx, 104)))

    def _draw_over(self):
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))
        me, opp = self.me, other(self.me)
        won = self.winner == me
        lines = [("Вы победили!" if won else "Победил соперник", self.font_big, COLORS[self.winner]),
                 (f"{self.score[me]} : {self.score[opp]}", self.font_score, TEXT),
                 (f"Партии: {self.wins[me]} : {self.wins[opp]}", self.font, TEXT),
                 ("Сильный взмах или ПРОБЕЛ — новая партия", self.font_small, MUTED)]
        y = self.height // 2 - 110
        for text, font, color in lines:
            txt = font.render(text, True, color)
            self.screen.blit(txt, txt.get_rect(center=(self.width // 2, y)))
            y += 66
