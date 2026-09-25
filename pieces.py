"""
Описание фигур (блоков), которые появляются в лотке игрока.
Каждая фигура задаётся списком координат (row, col) относительно
левого верхнего угла своего "бокса".
"""

import random

SHAPES = {
    "dot": [(0, 0)],
    "domino_h": [(0, 0), (0, 1)],
    "domino_v": [(0, 0), (1, 0)],
    "line3_h": [(0, 0), (0, 1), (0, 2)],
    "line3_v": [(0, 0), (1, 0), (2, 0)],
    "line4_h": [(0, 0), (0, 1), (0, 2), (0, 3)],
    "line4_v": [(0, 0), (1, 0), (2, 0), (3, 0)],
    "line5_h": [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)],
    "square2": [(0, 0), (0, 1), (1, 0), (1, 1)],
    "square3": [(r, c) for r in range(3) for c in range(3)],
    "l_shape1": [(0, 0), (1, 0), (2, 0), (2, 1)],
    "l_shape2": [(0, 0), (0, 1), (1, 0), (2, 0)],
    "l_shape3": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "l_shape4": [(2, 0), (2, 1), (1, 1), (0, 1)],
    "t_shape": [(0, 0), (0, 1), (0, 2), (1, 1)],
    "s_shape": [(0, 1), (0, 2), (1, 0), (1, 1)],
    "z_shape": [(0, 0), (0, 1), (1, 1), (1, 2)],
    "corner3": [(0, 0), (1, 0), (1, 1)],
}

COLORS = [
    (231, 76, 60),    # красный
    (46, 204, 113),   # зелёный
    (52, 152, 219),   # синий
    (241, 196, 15),   # жёлтый
    (155, 89, 182),   # фиолетовый
    (26, 188, 156),   # бирюзовый
    (230, 126, 34),   # оранжевый
]


class Piece:
    """Одна фигура: набор клеток + цвет."""

    def __init__(self, shape_name=None):
        self.name = shape_name or random.choice(list(SHAPES.keys()))
        self.cells = SHAPES[self.name]
        self.color = random.choice(COLORS)

    @property
    def height(self):
        return max(r for r, _ in self.cells) + 1

    @property
    def width(self):
        return max(c for _, c in self.cells) + 1


def random_piece():
    return Piece()
