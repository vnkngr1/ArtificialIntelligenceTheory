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

Для окна камеры в интерфейсе игры (core/camera_preview.py) трекер готовит
кадр, обрезанный вокруг лица, с отмеченными глазами и зрачками — get_preview().

Чтобы жест выхода «средний палец» работал и в играх глазами, трекер
дополнительно раз в несколько кадров ищет руку моделью HandLandmarker —
exit_gesture().

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

from .gesture_tracker import MODEL_PATH as HAND_MODEL_PATH
from .gesture_tracker import MODEL_URL as HAND_MODEL_URL
from .gesture_tracker import ensure_model, middle_finger_up

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
)
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")

# Контуры глаз (индексы точек FaceLandmarker) — только для отладочного окна
EYE_POINTS = [33, 160, 158, 133, 153, 144, 362, 385, 387, 263, 373, 380]

EYE_CORNERS = [(33, 133), (362, 263)]   # уголки правого и левого глаза
IRIS_CENTERS = [468, 473]

PREVIEW_SIZE = (320, 240)   # кадр для окна камеры в интерфейсе (4:3, обрезан вокруг лица)
HAND_EVERY = 3              # руку (жест выхода) ищем в каждом 3-м кадре — это дешевле


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
                 min_closed_time=0.0, preview=True, exit_gesture=True):
        """
        close_threshold — глаза считаются закрытыми выше этого значения
                          (не ниже «открытого» уровня + 0.25);
        open_threshold  — и снова открытыми ниже этого значения;
        min_closed_time — сколько секунд глаза должны быть закрыты, чтобы
                          засчитать моргание. 0 — мгновенно. ~0.25 — только
                          нарочно долгое моргание, случайные не срабатывают;
        preview         — готовить кадры для окна камеры в интерфейсе;
        exit_gesture    — искать руку и жест выхода «средний палец».
        """
        self.cam_index = cam_index
        self.close_threshold = close_threshold
        self.open_threshold = open_threshold
        self.min_closed_time = min_closed_time
        self.preview = preview
        self.detect_exit = exit_gesture
        self.error = None            # текст ошибки, если камера или модель недоступны

        self._lock = threading.Lock()
        self._face_detected = False
        self._score = 0.0
        self._closed = False
        self._blink_count = 0
        self._gaze = None
        self._preview = None
        self._preview_id = 0
        self._crop = None            # сглаженная рамка обрезки вокруг лица: (cx, cy, ширина)
        self._exit_gesture = False
        self._frames = 0

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

    def exit_gesture(self):
        """Показан ли сейчас жест выхода «средний палец»."""
        with self._lock:
            return self._exit_gesture

    def camera_ready(self):
        """Трекер уже обработал хотя бы один кадр камеры."""
        with self._lock:
            return self._frames > 0

    def get_preview(self):
        """(номер кадра, RGB-кадр numpy HxWx3 вокруг лица); (0, None), пока кадров нет."""
        with self._lock:
            return self._preview_id, self._preview

    def preview_status(self):
        """Подпись для окна камеры: (текст, цвет RGB)."""
        with self._lock:
            if not self._face_detected:
                return "НЕТ ЛИЦА", (255, 110, 110)
            return ("ГЛАЗА ЗАКРЫТЫ", (255, 200, 90)) if self._closed else ("ГЛАЗА ОТКРЫТЫ", (90, 230, 120))

    def _store_preview(self, frame, lm):
        """Кадр для интерфейса: обрезка вокруг лица (чтобы глаза были крупнее) + точки глаз."""
        h, w = frame.shape[:2]
        if lm is not None:
            xs, ys = [p.x for p in lm], [p.y for p in lm]
            target = ((min(xs) + max(xs)) / 2 * w, (min(ys) + max(ys)) / 2 * h,
                      max((max(xs) - min(xs)) * w, (max(ys) - min(ys)) * h * 4 / 3) * 1.5)
        else:
            target = (w / 2, h / 2, w)                  # лица нет — показываем весь кадр
        if self._crop is None:
            self._crop = target
        else:                                           # плавно, чтобы картинка не дёргалась
            self._crop = tuple(c + (t - c) * 0.25 for c, t in zip(self._crop, target))
        cx, cy, cw = self._crop
        cw = max(40.0, min(cw, w, h * 4 / 3))
        ch = cw * 3 / 4
        x0 = min(max(cx - cw / 2, 0), w - cw)
        y0 = min(max(cy - ch / 2, 0), h - ch)
        crop = frame[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
        pw, ph = PREVIEW_SIZE
        small = cv2.resize(crop, (pw, ph), interpolation=cv2.INTER_AREA)
        if lm is not None:
            k = pw / cw

            def to_px(p):
                return int((p.x * w - x0) * k), int((p.y * h - y0) * k)

            for i in EYE_POINTS:
                cv2.circle(small, to_px(lm[i]), 2, (0, 200, 255), -1, cv2.LINE_AA)
            if len(lm) > max(IRIS_CENTERS):
                for i in IRIS_CENTERS:
                    cv2.circle(small, to_px(lm[i]), 4, (255, 0, 255), -1, cv2.LINE_AA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        with self._lock:
            self._preview = rgb
            self._preview_id += 1

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
            self.error = "Нет модели лица (нужен интернет)"
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
        hand_landmarker = None
        if self.detect_exit:
            try:
                ensure_model(HAND_MODEL_URL, HAND_MODEL_PATH)
                hand_landmarker = mp_vision.HandLandmarker.create_from_options(mp_vision.HandLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=HAND_MODEL_PATH),
                    num_hands=1, running_mode=mp_vision.RunningMode.VIDEO,
                    min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.6))
            except Exception as exc:
                print("[BlinkTracker] Жест выхода недоступен — не удалось загрузить модель руки:", exc)

        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            print("[BlinkTracker] Не удалось открыть камеру индекс", self.cam_index)
            self.error = "Камера недоступна"
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
            timestamp_ms = int((frame_t - start_time) * 1000)
            try:
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                exit_gesture = None
                if hand_landmarker is not None and self._frames % HAND_EVERY == 0:
                    hands = hand_landmarker.detect_for_video(mp_image, timestamp_ms)
                    exit_gesture = bool(hands.hand_landmarks) and middle_finger_up(hands.hand_landmarks[0])
            except RuntimeError:
                # интерпретатор уже завершается (игра упала или закрылась без stop())
                break

            face_detected = bool(result.face_blendshapes)
            with self._lock:
                self._face_detected = face_detected
                self._frames += 1
                if exit_gesture is not None:
                    self._exit_gesture = exit_gesture
                if face_detected:
                    shapes = {c.category_name: c.score for c in result.face_blendshapes[0]}
                    self._score = (shapes.get("eyeBlinkLeft", 0.0) + shapes.get("eyeBlinkRight", 0.0)) / 2
                    self._update_blink(self._score, frame_t)
                    gaze = gaze_ratio(result.face_landmarks[0]) if result.face_landmarks else None
                    if gaze is not None and not self._closed and self._score < self.open_threshold:
                        self._gaze = gaze
                else:
                    self._closed = False

            if self.preview:
                self._store_preview(frame, result.face_landmarks[0] if result.face_landmarks else None)

        landmarker.close()
        if hand_landmarker is not None:
            hand_landmarker.close()
        cap.release()
