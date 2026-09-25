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
"""

import math
import os
import threading
import time
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

# Официальная модель от Google (лёгкая, float16)
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")

# Индексы landmark'ов в HandLandmarker те же, что были в старом API
THUMB_TIP = 4
INDEX_TIP = 8

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


def ensure_model():
    """Скачивает hand_landmarker.task рядом со скриптом, если его ещё нет."""
    if not os.path.exists(MODEL_PATH):
        print("[GestureTracker] Скачиваю модель hand_landmarker.task (один раз)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("[GestureTracker] Модель сохранена:", MODEL_PATH)


class GestureTracker:
    def __init__(self, cam_index=0, smoothing=0.5, pinch_threshold=0.055, show_debug=True):
        self.cam_index = cam_index
        self.smoothing = smoothing          # 0..1, чем выше — тем быстрее реакция, но больше дрожания
        self.pinch_threshold = pinch_threshold
        self.show_debug = show_debug

        self._lock = threading.Lock()
        self._cursor_x = 0.5
        self._cursor_y = 0.5
        self._pinching = False
        self._hand_detected = False

        self._smooth_x = 0.5
        self._smooth_y = 0.5

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

    def _run(self):
        try:
            ensure_model()
        except Exception as exc:
            print("[GestureTracker] Не удалось скачать модель:", exc)
            print("[GestureTracker] Проверьте интернет-соединение и повторите запуск.")
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
            self._running = False
            return

        start_time = time.time()

        while self._running:
            ok, frame = cap.read()
            if not ok:
                continue

            frame = cv2.flip(frame, 1)  # зеркалим, чтобы было интуитивно
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            timestamp_ms = int((time.time() - start_time) * 1000)
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            hand_detected = False
            pinching = False
            raw_x, raw_y = self._smooth_x, self._smooth_y

            if result.hand_landmarks:
                hand_detected = True
                lm = result.hand_landmarks[0]  # список из 21 точки текущей руки

                thumb_tip = lm[THUMB_TIP]
                index_tip = lm[INDEX_TIP]

                raw_x = index_tip.x
                raw_y = index_tip.y

                dist = math.hypot(thumb_tip.x - index_tip.x, thumb_tip.y - index_tip.y)
                pinching = dist < self.pinch_threshold

                if self.show_debug:
                    h, w, _ = frame.shape
                    pts = [(int(p.x * w), int(p.y * h)) for p in lm]
                    for a, b in HAND_CONNECTIONS:
                        cv2.line(frame, pts[a], pts[b], (0, 200, 0), 2)
                    for p in pts:
                        cv2.circle(frame, p, 4, (0, 120, 255), -1)

            # экспоненциальное сглаживание, чтобы курсор не дёргался
            self._smooth_x += (raw_x - self._smooth_x) * self.smoothing
            self._smooth_y += (raw_y - self._smooth_y) * self.smoothing

            with self._lock:
                self._cursor_x = self._smooth_x
                self._cursor_y = self._smooth_y
                self._pinching = pinching
                self._hand_detected = hand_detected

            if self.show_debug:
                status = "PINCH" if pinching else ("HAND" if hand_detected else "NO HAND")
                color = (0, 255, 0) if pinching else (0, 200, 255) if hand_detected else (0, 0, 255)
                cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("Gesture debug (закроется вместе с игрой)", frame)
                cv2.waitKey(1)

        landmarker.close()
        cap.release()
        if self.show_debug:
            cv2.destroyAllWindows()
