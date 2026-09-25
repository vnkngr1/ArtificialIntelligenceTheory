"""
Фильтр One Euro (Casiez, Roussel, Vogel, 2012) — сглаживание для курсора
руки и взгляда.

Обычное экспоненциальное сглаживание (EMA) заставляет выбирать: либо
курсор дрожит, либо запаздывает. One Euro подстраивает силу сглаживания
под скорость движения: пока рука (взгляд) почти неподвижна — сглаживает
сильно, и дрожание пропадает; при быстром движении — почти не сглаживает,
и лага нет.

Два параметра:
  min_cutoff — частота среза (Гц) в покое: меньше → меньше дрожания,
               но медленнее «доезжает» при очень плавных движениях;
  beta       — насколько быстро ослабевает сглаживание с ростом скорости:
               больше → меньше запаздывание при резких движениях.
Скорость измеряется в единицах сигнала в секунду (для нормализованных
координат 0..1 — «доли кадра в секунду»).
"""

import math


def _alpha(dt, cutoff):
    tau = 1.0 / (2 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self):
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def __call__(self, x, t):
        """x — новое значение, t — его время в секундах; возвращает сглаженное."""
        if self.t_prev is None:
            self.x_prev, self.t_prev = x, t
            return x
        dt = t - self.t_prev
        if dt <= 0:
            return self.x_prev                       # тот же кадр ещё раз
        dx = (x - self.x_prev) / dt
        dx_hat = self.dx_prev + _alpha(dt, self.d_cutoff) * (dx - self.dx_prev)
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        x_hat = self.x_prev + _alpha(dt, cutoff) * (x - self.x_prev)
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat


class OneEuroFilter2D:
    """Два независимых фильтра — для x и y."""

    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self.fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def reset(self):
        self.fx.reset()
        self.fy.reset()

    def __call__(self, x, y, t):
        return self.fx(x, t), self.fy(y, t)
