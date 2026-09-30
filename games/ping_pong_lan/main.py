"""
Пинг-понг по сети — Gesture Edition
===================================

Два игрока на двух компьютерах в одной локальной сети (например, в одном
Wi-Fi). Каждый играет своей рукой перед своей камерой.

Лобби:
  «Создать игру»  — этот компьютер становится хостом и ждёт соперника;
  «Игры в сети»   — список найденных игр, выберите — и подключитесь;
  IP вручную      — если игра не появилась в списке (роутер не пропускает
                    широковещательные пакеты): наберите IP хоста и Enter.
Кнопки нажимаются щипком с удержанием (как в меню), мышью или клавишами.

В игре: ракетка — там, где рука, удар — взмах вправо, к сопернику (каждый
видит себя слева). Правый нижний угол — ваша камера, левый нижний — камера
соперника (кадры идут по сети).

Управление:
  H          — создать игру,  J — подключиться к первой найденной
  цифры, точка, Enter — ввести IP хоста и подключиться
  G          — играть мышью (резкий рывок вправо — удар)
  ПРОБЕЛ     — новая партия после окончания / в лобби после разрыва связи
  K          — спрятать / показать окна камер
  ESC        — в игре: выйти в лобби; в лобби: выход
  Средний палец (показать камере и подержать) — выход
"""

import math
import os
import socket
import sys
import time
from collections import deque

# Корень проекта — в sys.path, чтобы найти общий пакет core/ и логику games/ping_pong_2p
# (и при запуске через launcher.py, и при запуске этого файла напрямую).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pygame

from core.camera_preview import CameraPreview, hand_status
from core.display import open_window
from core.exit_gesture import ExitGesture
from core.gesture_tracker import GestureTracker, PinchHysteresis
from core.lan import INPUT, JOIN, STATE, VIDEO, Client, Discovery, Host, RemoteCamera, local_ip
from core.one_euro import OneEuroFilter2D
from game import COLORS, LEFT, OVER, RIGHT, LanPingPong

WINDOW_W, WINDOW_H = 1280, 800
FPS = 60
GAME_ID = "cv-pingpong-lan-1"

# Рука → ракетка: весь кадр камеры → своя (левая на экране) половина
CAM_X = (0.12, 0.88)
CAM_Y = (0.12, 0.88)
PADDLE_X = (30, 600)
PADDLE_Y = (110, 720)
CAM_MIN_CUTOFF, CAM_BETA = 1.0, 20.0     # ракетка должна успевать за взмахом
SWING_WINDOW = 0.08                      # сек — окно для скорости взмаха (по «сырым» кадрам)
FWD_PX = 250

VIDEO_INTERVAL = 0.08                    # ~12 кадров камеры в секунду сопернику
INPUT_STALE = 0.25                       # ввод соперника старше — считаем, что он не машет
JOIN_TIMEOUT = 6.0
HOLD_TIME = 0.6                          # щипок с удержанием — нажать кнопку в лобби
PREVIEW_W, PREVIEW_H = 168, 126

LOBBY, HOSTING, JOINING, PLAYING, LOST = "lobby", "hosting", "joining", "playing", "lost"

BG = (24, 27, 38)
PANEL = (38, 43, 62)
PANEL_HOVER = (56, 63, 92)
ACCENT = (255, 205, 80)
TEXT = (238, 240, 246)
MUTED = (150, 158, 178)
BAD = (255, 130, 120)


def map_range(v, src, dst):
    k = min(max((v - src[0]) / (src[1] - src[0]), 0.0), 1.0)
    return dst[0] + (dst[1] - dst[0]) * k


def measure_swing(trail, now):
    """(скорость вправо — к сопернику, скорость вверх/вниз, сила взмаха), px/с по экрану игрока."""
    pts = [p for p in trail if p[0] >= now - SWING_WINDOW]
    if len(pts) < 2 or now - pts[-1][0] > 0.15:
        return 0.0, 0.0, 0.0
    (t0, x0, y0, s0), (t1, x1, y1, s1) = pts[0], pts[-1]
    dt = t1 - t0
    if dt < 1e-3:
        return 0.0, 0.0, 0.0
    forward = (x1 - x0) / dt
    growth = (s1 - s0) / dt / ((s0 + s1) / 2) if s0 > 0 and s1 > 0 else 0.0
    return forward, (y1 - y0) / dt, max(0.0, forward) + FWD_PX * max(0.0, growth)


class Button:
    def __init__(self, rect, label, action, accent=False):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.action = action
        self.accent = accent


class App:
    def __init__(self):
        pygame.init()
        self.screen = open_window((WINDOW_W, WINDOW_H), "Пинг-понг по сети — Gesture Edition")
        self.clock = pygame.time.Clock()
        self.font_small = pygame.font.SysFont("arial", 16)
        self.font = pygame.font.SysFont("arial", 22, bold=True)
        self.font_big = pygame.font.SysFont("arial", 44, bold=True)
        self.name = socket.gethostname()
        self.my_ip = local_ip()

        self.tracker = GestureTracker(cam_index=0)
        self.tracker.start()
        own_rect = pygame.Rect(WINDOW_W - 8 - PREVIEW_W, WINDOW_H - 8 - PREVIEW_H, PREVIEW_W, PREVIEW_H)
        self.own_preview = CameraPreview(self.tracker, WINDOW_H, rect=own_rect)
        self.remote_cam = RemoteCamera()
        self.remote_preview = CameraPreview(self.remote_cam, WINDOW_H,
                                            rect=pygame.Rect(8, WINDOW_H - 8 - PREVIEW_H, PREVIEW_W, PREVIEW_H))
        self.exit_gesture = ExitGesture()
        self.discovery = Discovery(GAME_ID)

        # рука
        self.pinch = PinchHysteresis()
        self.hand_filter = OneEuroFilter2D(CAM_MIN_CUTOFF, CAM_BETA)
        self.trail = deque(maxlen=40)
        self.last_sample_t = 0.0
        self.hand_detected = False
        self.pinching = False
        self.cursor = (WINDOW_W // 2, WINDOW_H // 2)       # курсор в лобби
        self.paddle = (PADDLE_X[0] + 60, WINDOW_H / 2)     # своя ракетка на своём экране
        self.hold_button = None
        self.hold = 0.0
        self.use_mouse = False
        self.mouse_until = 0.0

        self.state = LOBBY
        self.net = None
        self.game = None
        self.ip_text = ""
        self.message = None
        self.last_video = 0.0
        self.last_input_t = 0.0
        self.remote_input = None
        self.rtt = None
        self.want_restart = 0.0              # клиент нажал ПРОБЕЛ после партии — передать хосту
        self.join_ip = ""
        self.state_since = time.time()

    # ---------- состояния ----------

    def set_state(self, state, message=None):
        self.state = state
        self.state_since = time.time()
        if message is not None:
            self.message = message

    def host_game(self):
        self.close_net()
        try:
            self.net = Host(GAME_ID, self.name)
        except OSError as exc:
            self.set_state(LOBBY, f"Не удалось создать игру: {exc}")
            return
        self.set_state(HOSTING, None)

    def join_game(self, ip):
        self.close_net()
        try:
            self.net = Client(ip, self.name)
        except OSError as exc:
            self.set_state(LOBBY, f"Не удалось подключиться: {exc}")
            return
        self.join_ip = ip
        self.set_state(JOINING, None)

    def start_match(self, me):
        self.game = LanPingPong(self.screen, WINDOW_W, WINDOW_H, me)
        self.own_preview.border = COLORS[me]
        self.remote_preview.border = COLORS[RIGHT if me == LEFT else LEFT]
        self.remote_cam.status = (self.net.peer_name or "СОПЕРНИК")[:14].upper(), COLORS[RIGHT if me == LEFT else LEFT]
        self.remote_input = None
        self.rtt = None
        self.set_state(PLAYING, None)

    def leave_to_lobby(self, message=None):
        self.close_net()
        self.game = None
        self.remote_cam = RemoteCamera()                 # кадры прошлого соперника больше не нужны
        self.remote_preview.set_tracker(self.remote_cam)
        self.set_state(LOBBY, message)

    def close_net(self):
        if self.net is not None:
            self.net.close()
            self.net = None

    # ---------- ввод руки ----------

    def read_hand(self, now):
        """Новые кадры камеры: курсор/ракетка, щипок (для кнопок) и история для скорости взмаха."""
        pressed = released = False
        for s in self.tracker.get_samples_since(self.last_sample_t):
            self.last_sample_t = s.t
            self.hand_detected = s.detected
            if s.detected:
                fx, fy = self.hand_filter(s.x, s.y, s.t)
                self.cursor = (map_range(fx, CAM_X, (0, WINDOW_W - 1)), map_range(fy, CAM_Y, (0, WINDOW_H - 1)))
                if not self.use_mouse:
                    raw = (map_range(s.x, CAM_X, PADDLE_X), map_range(s.y, CAM_Y, PADDLE_Y))
                    self.trail.append((s.t, raw[0], raw[1], s.hand_size))
                    self.paddle = (map_range(fx, CAM_X, PADDLE_X), map_range(fy, CAM_Y, PADDLE_Y))
            closed = self.pinch.update(s)
            pressed |= closed and not self.pinching
            released |= self.pinching and not closed
            self.pinching = closed
        if self.use_mouse:
            mx, my = pygame.mouse.get_pos()
            self.paddle = (min(max(mx, PADDLE_X[0]), PADDLE_X[1]), min(max(my, PADDLE_Y[0]), PADDLE_Y[1]))
            self.trail.append((now, self.paddle[0], self.paddle[1], 0.0))
        return pressed, released

    # ---------- кнопки лобби ----------

    def buttons(self):
        btns = []
        if self.state == LOBBY:
            btns.append(Button((140, 250, 380, 90), "Создать игру", self.host_game, accent=True))
            for i, (ip, port, name) in enumerate(self.discovery.hosts()[:4]):
                btns.append(Button((700, 250 + i * 76, 440, 64), f"{name}  ({ip})", lambda ip=ip: self.join_game(ip)))
            btns.append(Button((940, 560, 200, 56), "Подключиться",
                               lambda: self.join_game(self.ip_text) if self.ip_text else None))
        elif self.state in (HOSTING, JOINING):
            btns.append(Button((WINDOW_W // 2 - 120, 470, 240, 60), "Отмена", lambda: self.leave_to_lobby()))
        elif self.state == LOST:
            btns.append(Button((WINDOW_W // 2 - 140, 470, 280, 60), "В лобби", lambda: self.leave_to_lobby()))
        return btns

    def button_at(self, pos, btns):
        for b in btns:
            if b.rect.collidepoint(pos):
                return b
        return None

    # ---------- главный цикл ----------

    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            now = time.time()
            btns = self.buttons() if self.state != PLAYING else []

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.MOUSEMOTION:
                    self.mouse_until = now + 1.5
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    b = self.button_at(event.pos, btns)
                    if b:
                        b.action()
                elif event.type == pygame.KEYDOWN:
                    if not self.handle_key(event):
                        return

            pressed, released = self.read_hand(now)
            if self.state != PLAYING:
                self.update_menu_hand(dt, now, btns, pressed, released)
            self.update_net(dt, now)
            self.draw()
            if self.exit_gesture.update(dt, self.tracker):
                return
            self.exit_gesture.draw(self.screen)
            pygame.display.flip()

    def handle_key(self, event):
        """Возвращает False, если надо выйти из программы."""
        key = event.key
        if key == pygame.K_ESCAPE:
            if self.state == LOBBY:
                return False
            self.leave_to_lobby()
        elif key == pygame.K_k:
            self.own_preview.toggle()
            self.remote_preview.toggle()
        elif key == pygame.K_g:
            self.use_mouse = not self.use_mouse
            self.trail.clear()
        elif self.state == LOBBY:
            if key == pygame.K_h:
                self.host_game()
            elif key == pygame.K_j and self.discovery.hosts():
                self.join_game(self.discovery.hosts()[0][0])
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.ip_text:
                self.join_game(self.ip_text)
            elif key == pygame.K_BACKSPACE:
                self.ip_text = self.ip_text[:-1]
            elif event.unicode and event.unicode in "0123456789." and len(self.ip_text) < 15:
                self.ip_text += event.unicode
        elif self.state == LOST and key == pygame.K_SPACE:
            self.leave_to_lobby()
        elif self.state == PLAYING and key == pygame.K_SPACE and self.game.phase == OVER:
            if self.game.me == LEFT:
                self.game.reset_match()
            else:
                self.want_restart = time.time()
        return True

    def update_menu_hand(self, dt, now, btns, pressed, released):
        """Кнопки лобби: щипок на кнопке и удержание HOLD_TIME — нажатие."""
        if now < self.mouse_until:
            self.cursor = pygame.mouse.get_pos()
        hovered = self.button_at(self.cursor, btns)
        if pressed:
            self.hold_button, self.hold = (hovered.label if hovered else None), 0.0
        if released or not self.pinching:
            self.hold_button, self.hold = None, 0.0
        if self.hold_button and hovered and hovered.label == self.hold_button:
            self.hold += dt / HOLD_TIME
            if self.hold >= 1.0:
                self.hold_button, self.hold = None, 0.0
                hovered.action()
        elif self.hold_button and (not hovered or hovered.label != self.hold_button):
            self.hold_button, self.hold = None, 0.0

    # ---------- сеть ----------

    def update_net(self, dt, now):
        net = self.net
        if self.state == HOSTING:
            if net.take(JOIN) is not None:
                self.start_match(LEFT)
        elif self.state == JOINING:
            net.handshake()
            if net.accepted:
                self.start_match(RIGHT)
            elif net.busy:
                self.leave_to_lobby("Эта игра уже занята другим игроком")
            elif now - self.state_since > JOIN_TIMEOUT:
                self.leave_to_lobby(f"Не удалось подключиться к {self.join_ip}: хост не отвечает. "
                                    "Проверьте IP, одну сеть и брандмауэр.")
        elif self.state == PLAYING:
            self.play_frame(dt, now)
            # «вышел» — сразу; тишина в сети — после паузы на старте (первые пакеты ещё в пути)
            if net.peer_quit:
                self.set_state(LOST, "Соперник вышел из игры")
            elif not net.connected and now - self.state_since > 2.0:
                self.set_state(LOST, "Связь с соперником потеряна")

    def play_frame(self, dt, now):
        game, net = self.game, self.net
        swing = measure_swing(self.trail, now)
        # своя ракетка: на своём экране слева; в «мире» игры у клиента — зеркально справа
        world_pos = self.paddle if game.me == LEFT else (WINDOW_W - self.paddle[0], self.paddle[1])
        game.set_paddle(game.me, world_pos, swing)

        if game.me == LEFT:
            # хост: ввод соперника → считаем кадр → отдаём состояние
            inp = net.take_json(INPUT)
            if inp:
                self.remote_input, self.last_input_t = inp, now
            if self.remote_input:
                fresh = now - self.last_input_t < INPUT_STALE
                sw = self.remote_input["swing"] if fresh else (0.0, 0.0, 0.0)
                game.set_paddle(RIGHT, tuple(self.remote_input["pad"]), tuple(sw))
                if self.remote_input.get("restart") and game.phase == OVER:
                    game.reset_match()
                    self.remote_input["restart"] = False
            game.update(dt)
            state = game.to_state()
            state["echo"] = self.remote_input.get("t") if self.remote_input else None
            net.send_json(STATE, state)
        else:
            # клиент: отдаём свой ввод → рисуем то, что прислал хост
            restart = now - self.want_restart < 1.0 or (
                game.phase == OVER and swing[2] > 450)
            net.send_json(INPUT, {"pad": [round(v, 1) for v in world_pos],
                                  "swing": [round(v, 1) for v in swing], "t": now, "restart": restart})
            st = net.take_json(STATE)
            if st:
                game.apply_state(st)
                if st.get("echo"):
                    rtt = (now - st["echo"]) * 1000
                    self.rtt = rtt if self.rtt is None else self.rtt * 0.9 + rtt * 0.1
            game.tick(dt)
        game.status_line = f"связь: {self.rtt:.0f} мс" if self.rtt is not None else \
            ("хост" if game.me == LEFT else "")

        # камеры: своя — сопернику, его — в левый нижний угол
        if now - self.last_video > VIDEO_INTERVAL:
            self.last_video = now
            net.send_video(self.tracker.get_preview()[1])
        frame = net.take(VIDEO)
        if frame:
            self.remote_cam.feed(frame)

    # ---------- отрисовка ----------

    def draw(self):
        s = self.screen
        if self.state == PLAYING or (self.state == LOST and self.game):
            self.game.draw()
        else:
            s.fill(BG)
            self.draw_lobby()
        if self.state == LOST:
            overlay = pygame.Surface(s.get_size(), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            s.blit(overlay, (0, 0))
            self.center_text(self.message or "Связь потеряна", self.font_big, 360, BAD)
            self.center_text("ПРОБЕЛ или кнопка — вернуться в лобби", self.font_small, 420, MUTED)
            self.draw_buttons(self.buttons())

        info = "мышь" if self.use_mouse else ("рука в кадре" if self.hand_detected else "рука не найдена")
        self.own_preview.draw(s, hand_status(self.hand_detected, self.pinching) if not self.use_mouse
                              else ("МЫШЬ", (200, 205, 220)))
        if self.state in (PLAYING, LOST):
            self.remote_preview.draw(s)
        if self.state != PLAYING:
            self.draw_cursor()
        txt = self.font_small.render(f"Ввод: {info}   (G — мышь, K — камеры, ESC — "
                                     f"{'в лобби' if self.state != LOBBY else 'выход'})", True, MUTED)
        s.blit(txt, txt.get_rect(midbottom=(WINDOW_W // 2, WINDOW_H - 10)))

    def draw_lobby(self):
        self.center_text("Пинг-понг по сети", self.font_big, 60, TEXT)
        self.center_text(f"Ваш компьютер: {self.name}   ·   IP: {self.my_ip}", self.font_small, 124, MUTED)
        self.center_text("Оба компьютера должны быть в одной сети (например, в одном Wi-Fi)", self.font_small, 150, MUTED)

        if self.state == LOBBY:
            s = self.screen
            s.blit(self.font.render("Создать игру и ждать соперника", True, TEXT), (140, 210))
            s.blit(self.font.render("Игры в сети", True, TEXT), (700, 210))
            if not self.discovery.hosts():
                msg = self.discovery.error or "Ищем игры… (соперник должен нажать «Создать игру»)"
                s.blit(self.font_small.render(msg, True, MUTED), (700, 262))
            s.blit(self.font.render("Или IP хоста вручную:", True, TEXT), (700, 530))
            field = pygame.Rect(700, 560, 226, 56)
            pygame.draw.rect(s, PANEL, field, border_radius=10)
            pygame.draw.rect(s, ACCENT, field, 2, border_radius=10)
            caret = "|" if int(time.time() * 2) % 2 else ""
            s.blit(self.font.render(self.ip_text + caret, True, TEXT), (field.x + 14, field.y + 14))
            s.blit(self.font_small.render("Цифры и точка с клавиатуры, Enter — подключиться", True, MUTED), (700, 626))
            s.blit(self.font_small.render("H — создать,  J — подключиться к первой найденной", True, MUTED), (140, 360))
            if self.message:
                self.center_text(self.message, self.font_small, 700, BAD)
        elif self.state == HOSTING:
            dots = "." * (1 + int(time.time() * 2) % 3)
            self.center_text(f"Ожидаем соперника{dots}", self.font_big, 290, TEXT)
            self.center_text(f"Второй игрок: «Пинг-понг по сети» → в списке «{self.name}» или IP {self.my_ip}",
                             self.font_small, 360, MUTED)
            self.center_text("Если Windows спросит про брандмауэр — разрешите доступ в частных сетях",
                             self.font_small, 390, MUTED)
        elif self.state == JOINING:
            self.center_text(f"Подключаемся к {self.join_ip}…", self.font_big, 320, TEXT)
        self.draw_buttons(self.buttons())

    def draw_buttons(self, btns):
        s = self.screen
        for b in btns:
            hovered = b.rect.collidepoint(self.cursor)
            color = PANEL_HOVER if hovered else PANEL
            pygame.draw.rect(s, color, b.rect, border_radius=14)
            if b.accent or hovered:
                pygame.draw.rect(s, ACCENT, b.rect, 3, border_radius=14)
            txt = self.font.render(b.label, True, TEXT)
            s.blit(txt, txt.get_rect(center=b.rect.center))
            if self.hold_button == b.label and self.hold > 0:
                bar = pygame.Rect(b.rect.left + 12, b.rect.bottom - 10, int((b.rect.width - 24) * self.hold), 5)
                pygame.draw.rect(s, ACCENT, bar, border_radius=3)

    def draw_cursor(self):
        x, y = self.cursor
        color = (255, 80, 80) if self.pinching else (255, 255, 255)
        pygame.draw.circle(self.screen, (0, 0, 0), (x, y), 15, 5)
        pygame.draw.circle(self.screen, color, (x, y), 14, 3)
        if self.hold > 0:
            rect = pygame.Rect(0, 0, 44, 44)
            rect.center = (x, y)
            pygame.draw.arc(self.screen, ACCENT, rect, math.pi / 2, math.pi / 2 + 2 * math.pi * min(1.0, self.hold), 5)

    def center_text(self, text, font, y, color):
        txt = font.render(text, True, color)
        self.screen.blit(txt, txt.get_rect(center=(WINDOW_W // 2, y)))

    def close(self):
        self.close_net()
        self.discovery.close()
        self.tracker.stop()


def main():
    app = App()
    try:
        app.run()
    finally:
        app.close()
        pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
