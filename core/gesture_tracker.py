"""
Трекер жестов на базе OpenCV + MediaPipe Tasks (HandLandmarker).

ВАЖНО: начиная с mediapipe 1.0 старый API mp.solutions.hands удалён
разработчиками полностью. Вместо него используется новый Tasks API:
mediapipe.tasks.python.vision.HandLandmarker. Он требует отдельный файл
модели hand_landmarker.task, который этот скрипт скачивает автоматически
при первом запуске (нужен интернет один раз).

Работает в отдельном потоке, чтобы не тормозить игровой цикл Pygame.
Наружу отдаёт три вещи через get_state():
    x, y          — нормализованные (0..1) координаты кончика указательного пальца
    pinching      — True, если большой и указательный пальцы сомкнуты ("щипок")
    hand_detected — видна ли рука в кадре вообще
Курсор сглаживается фильтром One Euro (core/one_euro.py).

exit_gesture() — показан ли жест выхода «средний палец» (см. core/exit_gesture.py).

Дополнительно трекер хранит буфер последних кадров (HandSample) с точным
временем съёмки — get_samples_since(t). Он нужен играм, которым важна
скорость движения руки (например, бросок в дартсе).

Для окна камеры в интерфейсе игры (core/camera_preview.py) трекер готовит
уменьшенный кадр с нарисованной рукой — get_preview().
"""

import math
import os
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from .one_euro import OneEuroFilter2D

# Официальная модель от Google (лёгкая, float16)
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")

# Индексы landmark'ов в HandLandmarker те же, что были в старом API
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9
# пары (кончик, средний сустав) для указательного, среднего, безымянного пальцев и мизинца
FINGERS = {"index": (8, 6), "middle": (12, 10), "ring": (16, 14), "pinky": (20, 18)}

PREVIEW_WIDTH = 320     # ширина кадра для окна камеры в интерфейсе (высота — по пропорциям камеры)

# Пары точек для отрисовки скелета руки в отладочном окне
# (тот же набор связей, что был в mp.solutions.hands.HAND_CONNECTIONS)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # большой палец
    (0, 5), (5, 6), (6, 7), (7, 8),          # указательный
    (5, 9), (9, 10), (10, 11), (11, 12),     # средний
    (9, 13), (13, 14), (14, 15), (15, 16),   # безымянный
    (13, 17), (17, 18), (18, 19), (19, 20),  # мизинец
    (0, 17),
]


def ensure_model(url=MODEL_URL, path=MODEL_PATH):
    """Скачивает файл модели MediaPipe рядом со скриптом, если его ещё нет."""
    if not os.path.exists(path):
        print(f"[MediaPipe] Скачиваю модель {os.path.basename(path)} (один раз)...")
        urllib.request.urlretrieve(url, path)
        print("[MediaPipe] Модель сохранена:", path)


@dataclass
class HandSample:
    """Один обработанный кадр камеры (координаты нормализованы 0..1, без сглаживания)."""
    t: float              # время съёмки кадра, time.time()
    detected: bool        # видна ли рука
    x: float = 0.5        # точка щипка — середина между кончиками большого и указательного
    y: float = 0.5
    pinch_dist: float = 1.0   # расстояние между кончиками большого и указательного
    hand_size: float = 0.0    # запястье → основание среднего пальца (масштаб руки:
                              # растёт, когда рука приближается к камере)


def middle_finger_up(lm):
    """Жест «средний палец»: средний выпрямлен, указательный, безымянный и мизинец согнуты.

    Палец выпрямлен, если его кончик заметно дальше от запястья, чем средний
    сустав, и согнут, если кончик не дальше сустава. Сравниваются расстояния,
    поэтому поворот руки в кадре не мешает. Большой палец не учитывается."""
    wrist = lm[WRIST]

    def reach(i):
        return math.hypot(lm[i].x - wrist.x, lm[i].y - wrist.y)

    def extended(name):
        tip, pip = FINGERS[name]
        return reach(tip) > reach(pip) * 1.15

    def folded(name):
        tip, pip = FINGERS[name]
        return reach(tip) < reach(pip) * 1.05

    return extended("middle") and folded("index") and folded("ring") and folded("pinky")


class PinchHysteresis:
    """Щипок по отношению «расстояние между пальцами / размер руки» с двумя порогами.

    В отличие от абсолютного порога pinch_threshold, отношение не меняется,
    когда рука приближается к камере или отдаляется от неё, а гистерезис
    (сжать — ниже close_ratio, разжать — выше open_ratio) не даёт щипку
    «мигать» на границе."""

    def __init__(self, close_ratio=0.30, open_ratio=0.45):
        self.close_ratio = close_ratio
        self.open_ratio = open_ratio
        self.closed = False

    def update(self, sample):
        """sample — HandSample; возвращает, сжаты ли пальцы."""
        if not sample.detected or sample.hand_size < 1e-6:
            self.closed = False
        else:
            ratio = sample.pinch_dist / sample.hand_size
            self.closed = ratio < (self.open_ratio if self.closed else self.close_ratio)
        return self.closed


class GestureTracker:
    def __init__(self, cam_index=0, pinch_threshold=0.055, preview=True, min_cutoff=0.5, beta=10.0):
        """min_cutoff / beta — параметры фильтра One Euro для курсора: меньше min_cutoff —
        меньше дрожания в покое, больше beta — меньше запаздывания при быстром движении."""
        self.cam_index = cam_index
        self._filter = OneEuroFilter2D(min_cutoff, beta)
        self.pinch_threshold = pinch_threshold
        self.preview = preview              # готовить кадры для окна камеры в интерфейсе
        self.error = None                   # текст ошибки, если камера или модель недоступны

        self._lock = threading.Lock()
        self._cursor_x = 0.5
        self._cursor_y = 0.5
        self._pinching = False
        self._hand_detected = False
        self._samples = deque(maxlen=120)  # ~4 секунды истории при 30 FPS
        self._preview = None
        self._preview_id = 0

        self._smooth_x = 0.5
        self._smooth_y = 0.5
        self._exit_gesture = False
        self._frames = 0

        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def get_state(self):
        """Возвращает (x, y, pinching, hand_detected). x,y в диапазоне 0..1."""
        with self._lock:
            return self._cursor_x, self._cursor_y, self._pinching, self._hand_detected

    def exit_gesture(self):
        """Показан ли сейчас жест выхода «средний палец»."""
        with self._lock:
            return self._exit_gesture

    def camera_ready(self):
        """Трекер уже обработал хотя бы один кадр камеры."""
        with self._lock:
            return self._frames > 0

    def get_samples_since(self, t):
        """Возвращает кадры (HandSample), снятые строго позже момента t, по порядку."""
        with self._lock:
            return [s for s in self._samples if s.t > t]

    def get_preview(self):
        """(номер кадра, RGB-кадр numpy HxWx3 с нарисованной рукой); (0, None), пока кадров нет."""
        with self._lock:
            return self._preview_id, self._preview

    def preview_status(self):
        """Подпись для окна камеры: (текст, цвет RGB)."""
        with self._lock:
            if self._pinching:
                return "ЩИПОК", (90, 230, 120)
            return ("РУКА", (90, 200, 255)) if self._hand_detected else ("НЕТ РУКИ", (255, 110, 110))

    def _store_preview(self, frame, lm):
        """Уменьшает кадр и рисует на нём скелет руки — для окна камеры в интерфейсе."""
        h, w = frame.shape[:2]
        pw, ph = PREVIEW_WIDTH, int(PREVIEW_WIDTH * h / w)
        small = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        if lm is not None:
            pts = [(int(p.x * pw), int(p.y * ph)) for p in lm]
            for a, b in HAND_CONNECTIONS:
                cv2.line(small, pts[a], pts[b], (0, 200, 0), 2, cv2.LINE_AA)
            for p in pts:
                cv2.circle(small, p, 3, (0, 120, 255), -1, cv2.LINE_AA)
            for i in (THUMB_TIP, INDEX_TIP):
                cv2.circle(small, pts[i], 5, (255, 255, 255), 2, cv2.LINE_AA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        with self._lock:
            self._preview = rgb
            self._preview_id += 1

    def _run(self):
        try:
            ensure_model()
        except Exception as exc:
            print("[GestureTracker] Не удалось скачать модель:", exc)
            print("[GestureTracker] Проверьте интернет-соединение и повторите запуск.")
            self.error = "Нет модели руки (нужен интернет)"
            self._running = False
            return

        base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=1,
            running_mode=mp_vision.RunningMode.VIDEO,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        landmarker = mp_vision.HandLandmarker.create_from_options(options)

        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            print("[GestureTracker] Не удалось открыть камеру индекс", self.cam_index)
            self.error = "Камера недоступна"
            self._running = False
            return

        start_time = time.time()

        while self._running:
            ok, frame = cap.read()
            if not ok:
                continue
            frame_t = time.time()

            frame = cv2.flip(frame, 1)  # зеркалим, чтобы было интуитивно
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            timestamp_ms = int((time.time() - start_time) * 1000)
            try:
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
            except RuntimeError:
                # интерпретатор уже завершается (игра упала или закрылась без stop())
                break

            hand_detected = False
            pinching = False
            raw_x, raw_y = self._smooth_x, self._smooth_y
            sample = HandSample(t=frame_t, detected=False)
            lm = None
            exit_gesture = False

            if result.hand_landmarks:
                hand_detected = True
                lm = result.hand_landmarks[0]  # список из 21 точки текущей руки

                thumb_tip = lm[THUMB_TIP]
                index_tip = lm[INDEX_TIP]

                raw_x = index_tip.x
                raw_y = index_tip.y

                dist = math.hypot(thumb_tip.x - index_tip.x, thumb_tip.y - index_tip.y)
                pinching = dist < self.pinch_threshold
                exit_gesture = middle_finger_up(lm)

                wrist, middle_mcp = lm[WRIST], lm[MIDDLE_MCP]
                sample = HandSample(
                    t=frame_t,
                    detected=True,
                    x=(thumb_tip.x + index_tip.x) / 2,
                    y=(thumb_tip.y + index_tip.y) / 2,
                    pinch_dist=dist,
                    hand_size=math.hypot(wrist.x - middle_mcp.x, wrist.y - middle_mcp.y),
                )

            # сглаживание One Euro: в покое курсор не дрожит, при быстром движении не отстаёт
            self._smooth_x, self._smooth_y = self._filter(raw_x, raw_y, frame_t)

            with self._lock:
                self._cursor_x = self._smooth_x
                self._cursor_y = self._smooth_y
                self._pinching = pinching
                self._hand_detected = hand_detected
                self._exit_gesture = exit_gesture
                self._frames += 1
                self._samples.append(sample)

            if self.preview:
                self._store_preview(frame, lm)

        landmarker.close()
        cap.release()
