"""
Простая реализация игры Block Blast на Pygame.

Игра ничего не знает про мышь или камеру — она получает только
события on_press / on_drag / on_release с координатами (x, y) в пикселях
окна. Это позволяет одинаково легко управлять как мышью, так и жестами.
"""

import pygame
from pieces import random_piece

GRID_SIZE = 8
CELL = 56
GRID_ORIGIN = (40, 40)
TRAY_CELL = 28

BG_COLOR = (245, 245, 250)
GRID_BG = (225, 225, 235)
EMPTY_CELL = (255, 255, 255)
TEXT_COLOR = (40, 40, 40)


class BlockBlastGame:
    def __init__(self, screen, width, height, reserved=None):
        """reserved — занятая область окна (окно камеры): лоток с фигурами начинается правее неё."""
        self.screen = screen
        self.width = width
        self.height = height
        self.tray_left = reserved.right + 10 if reserved else 0
        self.grid_origin = GRID_ORIGIN
        self.cell = CELL
        self.tray_y = self.grid_origin[1] + GRID_SIZE * CELL + 50

        self.font = pygame.font.SysFont("arial", 28, bold=True)
        self.font_big = pygame.font.SysFont("arial", 48, bold=True)

        self.reset()

    def reset(self):
        self.grid = [[None for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.tray = [random_piece() for _ in range(3)]
        self.score = 0
        self.game_over = False

        self.dragging_index = None
        self.drag_pos = None
        self.preview_cell = None
        self.preview_valid = False

    # ---------- координаты ----------

    def tray_slot_center(self, index):
        slot_w = (self.width - self.tray_left) // 3
        x = self.tray_left + slot_w * index + slot_w // 2
        y = self.tray_y + 50
        return x, y

    def grid_pixel_to_cell(self, px, py):
        gx, gy = self.grid_origin
        col = (px - gx) // self.cell
        row = (py - gy) // self.cell
        return int(row), int(col)

    def cell_to_pixel(self, row, col):
        gx, gy = self.grid_origin
        return gx + col * self.cell, gy + row * self.cell

    # ---------- логика размещения ----------

    def can_place(self, piece, row0, col0):
        for r, c in piece.cells:
            rr, cc = row0 + r, col0 + c
            if rr < 0 or rr >= GRID_SIZE or cc < 0 or cc >= GRID_SIZE:
                return False
            if self.grid[rr][cc] is not None:
                return False
        return True

    def any_placement_possible(self, piece):
        for r0 in range(GRID_SIZE):
            for c0 in range(GRID_SIZE):
                if self.can_place(piece, r0, c0):
                    return True
        return False

    def place_piece(self, piece, row0, col0):
        for r, c in piece.cells:
            self.grid[row0 + r][col0 + c] = piece.color
        self.score += len(piece.cells)
        self.clear_full_lines()

    def clear_full_lines(self):
        full_rows = [r for r in range(GRID_SIZE)
                     if all(self.grid[r][c] is not None for c in range(GRID_SIZE))]
        full_cols = [c for c in range(GRID_SIZE)
                     if all(self.grid[r][c] is not None for r in range(GRID_SIZE))]

        for r in full_rows:
            for c in range(GRID_SIZE):
                self.grid[r][c] = None
        for c in full_cols:
            for r in range(GRID_SIZE):
                self.grid[r][c] = None

        cleared = len(full_rows) + len(full_cols)
        if cleared:
            self.score += cleared * GRID_SIZE * 2

    def check_game_over(self):
        for piece in self.tray:
            if piece is not None and self.any_placement_possible(piece):
                return False
        return True

    # ---------- ввод (общий для мыши и жестов) ----------

    def on_press(self, pos):
        if self.game_over:
            return
        px, py = pos
        for i, piece in enumerate(self.tray):
            if piece is None:
                continue
            sx, sy = self.tray_slot_center(i)
            w, h = piece.width * TRAY_CELL, piece.height * TRAY_CELL
            rect = pygame.Rect(sx - w // 2 - 12, sy - h // 2 - 12, w + 24, h + 24)
            if rect.collidepoint(px, py):
                self.dragging_index = i
                self.drag_pos = (px, py)
                return

    def on_drag(self, pos):
        if self.dragging_index is None:
            return
        self.drag_pos = pos
        piece = self.tray[self.dragging_index]

        px, py = pos
        w, h = piece.width * self.cell, piece.height * self.cell
        anchor_x = px - w // 2
        anchor_y = py - h // 2

        row0, col0 = self.grid_pixel_to_cell(anchor_x + self.cell // 2, anchor_y + self.cell // 2)
        self.preview_cell = (row0, col0)
        self.preview_valid = self.can_place(piece, row0, col0)

    def on_release(self, pos):
        if self.dragging_index is None:
            return
        piece = self.tray[self.dragging_index]
        if self.preview_cell is not None and self.preview_valid:
            row0, col0 = self.preview_cell
            self.place_piece(piece, row0, col0)
            self.tray[self.dragging_index] = None
            if all(p is None for p in self.tray):
                self.tray = [random_piece() for _ in range(3)]

        self.dragging_index = None
        self.drag_pos = None
        self.preview_cell = None
        self.preview_valid = False

    def update(self):
        if not self.game_over and self.check_game_over():
            self.game_over = True

    # ---------- отрисовка ----------

    def draw(self, cursor_pos):
        self.screen.fill(BG_COLOR)
        self._draw_grid()
        self._draw_tray()
        self._draw_dragging_piece()
        self._draw_score()
        if self.game_over:
            self._draw_game_over()

    def _draw_grid(self):
        gx, gy = self.grid_origin
        rect = pygame.Rect(gx - 4, gy - 4, GRID_SIZE * self.cell + 8, GRID_SIZE * self.cell + 8)
        pygame.draw.rect(self.screen, GRID_BG, rect, border_radius=8)

        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                x, y = self.cell_to_pixel(r, c)
                color = self.grid[r][c] or EMPTY_CELL
                pygame.draw.rect(self.screen, color, (x + 2, y + 2, self.cell - 4, self.cell - 4), border_radius=6)

        if self.preview_cell is not None and self.dragging_index is not None:
            piece = self.tray[self.dragging_index]
            row0, col0 = self.preview_cell
            color = (120, 220, 140) if self.preview_valid else (230, 120, 120)
            for r, c in piece.cells:
                rr, cc = row0 + r, col0 + c
                if 0 <= rr < GRID_SIZE and 0 <= cc < GRID_SIZE:
                    x, y = self.cell_to_pixel(rr, cc)
                    pygame.draw.rect(self.screen, color,
                                      (x + 2, y + 2, self.cell - 4, self.cell - 4),
                                      border_radius=6, width=4)

    def _draw_tray(self):
        for i, piece in enumerate(self.tray):
            if piece is None or i == self.dragging_index:
                continue
            sx, sy = self.tray_slot_center(i)
            w, h = piece.width * TRAY_CELL, piece.height * TRAY_CELL
            ox, oy = sx - w // 2, sy - h // 2
            for r, c in piece.cells:
                x, y = ox + c * TRAY_CELL, oy + r * TRAY_CELL
                pygame.draw.rect(self.screen, piece.color,
                                  (x + 2, y + 2, TRAY_CELL - 4, TRAY_CELL - 4), border_radius=5)

    def _draw_dragging_piece(self):
        if self.dragging_index is None or self.drag_pos is None:
            return
        piece = self.tray[self.dragging_index]
        px, py = self.drag_pos
        w, h = piece.width * self.cell, piece.height * self.cell
        ox, oy = px - w // 2, py - h // 2
        for r, c in piece.cells:
            x, y = ox + c * self.cell, oy + r * self.cell
            pygame.draw.rect(self.screen, piece.color,
                              (x + 3, y + 3, self.cell - 6, self.cell - 6), border_radius=8)

    def _draw_score(self):
        txt = self.font.render(f"Счёт: {self.score}", True, TEXT_COLOR)
        self.screen.blit(txt, (self.width - txt.get_width() - 20, 20))

    def _draw_game_over(self):
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        txt = self.font_big.render("ИГРА ОКОНЧЕНА", True, (255, 255, 255))
        self.screen.blit(txt, (self.width // 2 - txt.get_width() // 2, self.height // 2 - 60))
        txt2 = self.font.render(f"Счёт: {self.score}   (R — заново)", True, (255, 255, 255))
        self.screen.blit(txt2, (self.width // 2 - txt2.get_width() // 2, self.height // 2 + 10))
