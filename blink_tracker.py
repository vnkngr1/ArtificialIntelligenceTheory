"""
Трекер глаз (моргание + направление взгляда) на базе OpenCV + MediaPipe
Tasks (FaceLandmarker).

FaceLandmarker кроме 478 точек лица умеет выдавать «blendshapes» —
коэффициенты мимики 0..1. Два из них, eyeBlinkLeft и eyeBlinkRight,
показывают, насколько закрыт каждый глаз. Их среднее и используется:
закрытие обоих глаз — моргание (подмигивание одним глазом не считается).

Чтобы не зависеть от формы глаз конкретного человека, порог закрытия
подстраивается под «открытый» уровень, который трекер измеряет сам,
пока глаза открыты. Плюс гистерезис: закрыть — выше одного порога,
открыть — ниже другого, чтобы одно моргание не считалось дважды.

Работает в отдельном потоке, как и GestureTracker. Наружу отдаёт get_state():
    face_detected — видно ли лицо
    closed_score  — насколько закрыты глаза, 0..1
    eyes_closed   — глаза сейчас закрыты (с учётом гистерезиса)
    blink_count   — сколько морганий насчитано с запуска (игра сравнивает
                    с прошлым значением и так узнаёт о новых морганиях)

Направление взгляда по горизонтали — get_gaze(). Последние 10 из 478 точек
FaceLandmarker — радужки (468 и 473 — центры зрачков). Положение зрачка
между уголками глаза даёт число ~0..1 (меньше — взгляд влево, больше —
вправо, в зеркальном кадре), среднее по двум глазам. Абсолютные значения
у всех людей разные и зависят от посадки перед камерой, поэтому игре
нужна калибровка (посмотреть на пару известных точек на экране).
"""

import os
import threading
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from gesture_tracker import ensure_model

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
)
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")

# Контуры глаз (индексы точек FaceLandmarker) — только для отладочного окна
EYE_POINTS = [33, 160, 158, 133, 153, 144, 362, 385, 387, 263, 373, 380]

EYE_CORNERS = [(33, 133), (362, 263)]   # уголки правого и левого глаза
IRIS_CENTERS = [468, 473]


def gaze_ratio(lm):
    """Положение зрачков между уголками глаз по горизонтали (среднее по двум глазам)."""
    if len(lm) <= max(IRIS_CENTERS):
        return None
    ratios = []
    for a, b in EYE_CORNERS:
        left, right = sorted((lm[a].x, lm[b].x))
        if right - left < 1e-4:
            continue
        mid_x, mid_y = (lm[a].x + lm[b].x) / 2, (lm[a].y + lm[b].y) / 2
        # какой зрачок относится к этому глазу — берём ближайший к его центру
        iris = min(IRIS_CENTERS, key=lambda i: (lm[i].x - mid_x) ** 2 + (lm[i].y - mid_y) ** 2)
        ratios.append((lm[iris].x - left) / (right - left))
    return sum(ratios) / len(ratios) if ratios else None


class BlinkTracker:
    def __init__(self, cam_index=0, close_threshold=0.5, open_threshold=0.35,
                 min_closed_time=0.0, show_debug=True):
        """
        close_threshold — глаза считаются закрытыми выше этого значения
                          (не ниже «открытого» уровня + 0.25);
        open_threshold  — и снова открытыми ниже этого значения;
        min_closed_time — сколько секунд глаза должны быть закрыты, чтобы
                          засчитать моргание. 0 — мгновенно. ~0.25 — только
                          нарочно долгое моргание, случайные не срабатывают.
        """
        self.cam_index = cam_index
        self.close_threshold = close_threshold
        self.open_threshold = open_threshold
        self.min_closed_time = min_closed_time
        self.show_debug = show_debug

        self._lock = threading.Lock()
        self._face_detected = False
        self._score = 0.0
        self._closed = False
        self._blink_count = 0
        self._gaze = None

        self._open_level = 0.1       # типичное значение при открытых глазах
        self._closed_since = None
        self._counted = False        # текущее закрытие уже засчитано

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
        """Возвращает (face_detected, closed_score, eyes_closed, blink_count)."""
        with self._lock:
            return self._face_detected, self._score, self._closed, self._blink_count

    def get_gaze(self):
        """Последнее положение зрачков (см. gaze_ratio) или None, если лица нет.
        Пока глаза закрыты, возвращается значение до моргания: при закрытых
        веках точки радужки недостоверны."""
        with self._lock:
            return self._gaze if self._face_detected else None

    def _update_blink(self, score, t):
        close_at = max(self.close_threshold, self._open_level + 0.25)
        if not self._closed:
            # пока глаза открыты, медленно подстраиваем «открытый» уровень
            self._open_level += (score - self._open_level) * 0.02
            if score > close_at:
                self._closed = True
                self._closed_since = t
                self._counted = False
        elif score < min(self.open_threshold, close_at - 0.1):
            self._closed = False

        if self._closed and not self._counted and t - self._closed_since >= self.min_closed_time:
            self._counted = True
            self._blink_count += 1

    def _run(self):
        try:
            ensure_model(MODEL_URL, MODEL_PATH)
        except Exception as exc:
            print("[BlinkTracker] Не удалось скачать модель:", exc)
            print("[BlinkTracker] Проверьте интернет-соединение и повторите запуск.")
            self._running = False
            return

        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = mp_vision.FaceLandmarker.create_from_options(options)

        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            print("[BlinkTracker] Не удалось открыть камеру индекс", self.cam_index)
            self._running = False
            return

        start_time = time.time()

        while self._running:
            ok, frame = cap.read()
            if not ok:
                continue
            frame_t = time.time()

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(mp_image, int((frame_t - start_time) * 1000))

            face_detected = bool(result.face_blendshapes)
            with self._lock:
                self._face_detected = face_detected
                if face_detected:
                    shapes = {c.category_name: c.score for c in result.face_blendshapes[0]}
                    self._score = (shapes.get("eyeBlinkLeft", 0.0) + shapes.get("eyeBlinkRight", 0.0)) / 2
                    self._update_blink(self._score, frame_t)
                    gaze = gaze_ratio(result.face_landmarks[0]) if result.face_landmarks else None
                    if gaze is not None and not self._closed and self._score < self.open_threshold:
                        self._gaze = gaze
                else:
                    self._closed = False
                score, closed, count, gaze = self._score, self._closed, self._blink_count, self._gaze

            if self.show_debug:
                h, w, _ = frame.shape
                if result.face_landmarks:
                    lm = result.face_landmarks[0]
                    for i in EYE_POINTS:
                        cv2.circle(frame, (int(lm[i].x * w), int(lm[i].y * h)), 2, (0, 200, 255), -1)
                    if len(lm) > max(IRIS_CENTERS):
                        for i in IRIS_CENTERS:
                            cv2.circle(frame, (int(lm[i].x * w), int(lm[i].y * h)), 3, (255, 0, 255), -1)
                if face_detected:
                    status = f"{'CLOSED' if closed else 'OPEN'}  {score:.2f}  blinks: {count}"
                    if gaze is not None:
                        status += f"  gaze: {gaze:.3f}"
                    color = (0, 0, 255) if closed else (0, 220, 0)
                else:
                    status, color = "NO FACE", (0, 0, 255)
                cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("Blink debug (закроется вместе с игрой)", frame)
                cv2.waitKey(1)

        landmarker.close()
        cap.release()
        if self.show_debug:
            cv2.destroyAllWindows()
