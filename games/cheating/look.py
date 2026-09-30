"""
«Смотрит ли человек вниз» — по признакам лица из BlinkTracker.get_look().

У каждого человека и камеры признаки разные (кто-то опускает глаза, кто-то
наклоняет голову; знак наклона по точкам лица зависит от посадки), поэтому
сначала калибровка: 2 секунды смотреть на экран, 2 секунды — вниз, как на
шпаргалку. По каждому признаку считаем, насколько он различает «прямо» и
«вниз» относительно своего дрожания, и берём его с таким весом. Признаки,
которые почти не меняются, в оценку не входят.

score(features) — 0 «смотрит прямо», 1 «смотрит вниз» (может выходить за
пределы); update(...) — то же с гистерезисом: вниз — выше DOWN_ENTER,
обратно — ниже DOWN_EXIT, чтобы состояние не «мигало».
"""

import statistics

DOWN_ENTER = 0.55
DOWN_EXIT = 0.40
MIN_SEPARATION = 1.5        # признак берём, если «вниз» отличается от «прямо» на 1.5 его разброса


class LookDownDetector:
    PHASES = (("up", "Смотрите на экран — как обычно во время контрольной"),
              ("down", "Посмотрите вниз — на колени, где обычно прячут шпаргалку"))
    SETTLE = 0.9                # сек — перевести взгляд
    COLLECT = 1.6               # сек — сбор значений

    def __init__(self):
        self.restart()

    def restart(self):
        self.phase = 0
        self.t = 0.0
        self.samples = {"up": [], "down": []}
        self.model = None           # [(номер признака, «прямо», «вниз», вес)]
        self.message = None
        self.down = False

    @property
    def done(self):
        return self.model is not None

    @property
    def instruction(self):
        return self.PHASES[min(self.phase, len(self.PHASES) - 1)][1]

    @property
    def progress(self):
        return max(0.0, min(1.0, (self.t - self.SETTLE) / self.COLLECT))

    def calibrate(self, dt, features):
        """Шаг калибровки; features — кортеж признаков или None (лица нет)."""
        if self.done or features is None:
            return
        self.t += dt
        key = self.PHASES[self.phase][0]
        if self.t > self.SETTLE:
            self.samples[key].append(features)
        if self.t >= self.SETTLE + self.COLLECT and len(self.samples[key]) >= 8:
            self.phase += 1
            self.t = 0.0
            if self.phase == len(self.PHASES):
                self._fit()

    def _fit(self):
        up, down = self.samples["up"], self.samples["down"]
        model = []
        for i in range(len(up[0])):
            a = [f[i] for f in up if f[i] is not None]
            b = [f[i] for f in down if f[i] is not None]
            if len(a) < 5 or len(b) < 5:
                continue
            mu_a, mu_b = statistics.fmean(a), statistics.fmean(b)
            spread = (statistics.pstdev(a) + statistics.pstdev(b)) / 2 + 1e-6
            separation = abs(mu_b - mu_a) / spread
            if separation >= MIN_SEPARATION:
                model.append((i, mu_a, mu_b, separation))
        if model:
            self.model = model
            self.message = None
        else:
            self.restart()
            self.message = ("Не получилось отличить взгляд вниз от взгляда на экран — повторим. "
                            "Опускайте глаза и голову заметнее.")

    def score(self, features):
        """0 — смотрит на экран, 1 — вниз. Каждый признак нормируется своей калибровкой."""
        total = weight = 0.0
        for i, up, down, w in self.model:
            if features[i] is None:
                continue
            total += w * (features[i] - up) / (down - up)
            weight += w
        return total / weight if weight else 0.0

    def update(self, features):
        """Смотрит ли вниз сейчас (с гистерезисом). Лица не видно — считаем, что смотрит вниз:
        при сильном наклоне головы камера теряет лицо, а учитель лица тоже не видит."""
        if features is None:
            self.down = True
            return True
        s = self.score(features)
        self.down = s > (DOWN_EXIT if self.down else DOWN_ENTER)
        return self.down
