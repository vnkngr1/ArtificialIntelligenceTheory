"""
Игра по локальной сети (один Wi-Fi / роутер) — UDP.

Один игрок создаёт игру (Host), второй присоединяется (Client). Хост раз в
полсекунды рассылает по сети широковещательное объявление «здесь идёт игра»;
Discovery собирает такие объявления, чтобы показать список игр. Если роутер
не пропускает широковещательные пакеты (бывает в гостевых и публичных Wi-Fi),
можно подключиться, введя IP хоста вручную.

Все пакеты — UDP: MAGIC + тип (1 байт) + данные. Для игры в реальном времени
важно последнее состояние, а не каждый пакет, поэтому потерянный пакет просто
пропускается: take(kind) отдаёт самое свежее сообщение данного типа.

Типы пакетов:
  A — объявление игры (широковещательно)   J / W — «хочу играть» / «принят»
  S — состояние игры (хост → клиент)        I — ввод игрока (клиент → хост)
  V — кадр камеры (JPEG)                    Q — игрок вышел

Windows при первом запуске может спросить разрешение для Python в брандмауэре —
надо разрешить доступ в частных сетях, иначе второй компьютер нас не увидит.
"""

import json
import socket
import threading
import time

import cv2
import numpy as np

GAME_PORT = 50555
DISCOVERY_PORT = 50556
MAGIC = b"CVG1"
TIMEOUT = 3.0               # сек без пакетов — собеседник пропал

ANNOUNCE, JOIN, WELCOME, BUSY = b"A", b"J", b"W", b"B"
STATE, INPUT, VIDEO, QUIT = b"S", b"I", b"V", b"Q"

VIDEO_SIZE = (160, 120)
VIDEO_QUALITY = 55


def local_ip():
    """IP этого компьютера в локальной сети (пакет при этом никуда не отправляется)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _pack(kind, payload=b""):
    return MAGIC + kind + payload


def _json(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


class Peer:
    """UDP-сокет с приёмом в фоновом потоке. Общая часть хоста и клиента."""

    def __init__(self, bind_addr):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(bind_addr)
        self.sock.settimeout(0.2)
        self.peer = None            # адрес собеседника (ip, port)
        self.peer_name = ""
        self.last_heard = 0.0
        self.peer_quit = False
        self._latest = {}
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    # ---------- приём ----------

    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break                   # сокет закрыт или сеть пропала
            if data.startswith(MAGIC) and len(data) > len(MAGIC):
                self._on_packet(data[4:5], data[5:], addr)

    def _on_packet(self, kind, payload, addr):
        if addr == self.peer:
            self._store(kind, payload)

    def _store(self, kind, payload):
        with self._lock:
            self._latest[kind] = payload
            self.last_heard = time.time()
            if kind == QUIT:
                self.peer_quit = True

    def take(self, kind):
        """Самое свежее сообщение типа kind (bytes) или None; повторно не отдаётся."""
        with self._lock:
            return self._latest.pop(kind, None)

    def take_json(self, kind):
        data = self.take(kind)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ---------- отправка ----------

    def send(self, kind, payload=b""):
        if self.peer is None:
            return
        try:
            self.sock.sendto(_pack(kind, payload), self.peer)
        except OSError:
            pass                        # сеть на мгновение пропала — следующий кадр дойдёт

    def send_json(self, kind, obj):
        self.send(kind, _json(obj))

    def send_video(self, rgb_frame):
        """Кадр камеры собеседнику: уменьшаем и сжимаем в JPEG (несколько килобайт)."""
        if rgb_frame is None:
            return
        small = cv2.resize(rgb_frame, VIDEO_SIZE, interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR),
                                [cv2.IMWRITE_JPEG_QUALITY, VIDEO_QUALITY])
        if ok:
            self.send(VIDEO, jpeg.tobytes())

    # ---------- состояние ----------

    @property
    def connected(self):
        return self.peer is not None and not self.peer_quit and time.time() - self.last_heard < TIMEOUT

    def close(self):
        if self.peer is not None:
            for _ in range(3):              # UDP может потерять пакет — шлём «вышел» несколько раз
                self.send(QUIT)
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass


class Host(Peer):
    """Создатель игры: ждёт одного игрока и объявляет о себе в сети."""

    def __init__(self, game_id, name, port=GAME_PORT, bind_ip="",
                 announce_to=("255.255.255.255", DISCOVERY_PORT)):
        super().__init__((bind_ip, port))
        self.game_id = game_id
        self.name = name
        self.port = port
        self.announce_to = announce_to
        self._announcer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._announcer.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        threading.Thread(target=self._announce_loop, daemon=True).start()

    def _announce_loop(self):
        packet = _pack(ANNOUNCE, _json({"game": self.game_id, "name": self.name, "port": self.port}))
        while self._running:
            if self.peer is None:           # пока соперника нет — зовём
                try:
                    self._announcer.sendto(packet, self.announce_to)
                except OSError:
                    pass
            time.sleep(0.5)
        self._announcer.close()

    def _on_packet(self, kind, payload, addr):
        if kind == JOIN:
            if self.peer is None or self.peer == addr or not self.connected:
                self.peer = addr
                self.peer_quit = False
                try:
                    self.peer_name = json.loads(payload.decode("utf-8")).get("name", "")
                except (ValueError, UnicodeDecodeError):
                    self.peer_name = ""
                self._store(JOIN, payload)
                self.send_json(WELCOME, {"name": self.name})
            else:
                try:
                    self.sock.sendto(_pack(BUSY), addr)     # уже играем с другим
                except OSError:
                    pass
            return
        super()._on_packet(kind, payload, addr)


class Client(Peer):
    """Игрок, который подключается к хосту по его IP."""

    def __init__(self, host_ip, name, port=GAME_PORT, bind_ip=""):
        super().__init__((bind_ip, 0))
        self.peer = (host_ip, port)
        self.name = name
        self.accepted = False
        self.busy = False
        self._last_join = 0.0

    def handshake(self):
        """Вызывать каждый кадр, пока не accepted: повторяет запрос (UDP может потеряться)."""
        if not self.accepted and time.time() - self._last_join > 0.3:
            self._last_join = time.time()
            self.send_json(JOIN, {"name": self.name})

    def _on_packet(self, kind, payload, addr):
        if addr[0] != self.peer[0]:
            return
        if kind == WELCOME:
            self.accepted = True
            try:
                self.peer_name = json.loads(payload.decode("utf-8")).get("name", "")
            except (ValueError, UnicodeDecodeError):
                pass
        elif kind == BUSY:
            self.busy = True
        self.peer = addr
        self._store(kind, payload)


class Discovery:
    """Слушает объявления хостов в сети: hosts() — список найденных игр."""

    def __init__(self, game_id, port=DISCOVERY_PORT, bind_ip=""):
        self.game_id = game_id
        self._hosts = {}
        self._lock = threading.Lock()
        self.error = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((bind_ip, port))
        except OSError as exc:
            self.error = f"Поиск игр недоступен: {exc}"
        self.sock.settimeout(0.2)
        self._running = self.error is None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(MAGIC + ANNOUNCE):
                continue
            try:
                info = json.loads(data[5:].decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if info.get("game") == self.game_id:
                with self._lock:
                    self._hosts[(addr[0], int(info.get("port", GAME_PORT)))] = (info.get("name", "?"), time.time())

    def hosts(self):
        """[(ip, port, имя)] — игры, объявлявшиеся в последние 2 секунды."""
        now = time.time()
        with self._lock:
            return sorted((ip, port, name) for (ip, port), (name, seen) in self._hosts.items() if now - seen < 2.0)

    def close(self):
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass


class RemoteCamera:
    """Кадры камеры соперника, пришедшие по сети. Годится как источник для
    core.camera_preview.CameraPreview — у него те же get_preview() и error."""

    def __init__(self):
        self.error = None
        self._frame = None
        self._frame_id = 0
        self.status = ("СОПЕРНИК", (200, 205, 220))

    def feed(self, jpeg_bytes):
        if not jpeg_bytes:
            return
        img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            self._frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            self._frame_id += 1

    def get_preview(self):
        return self._frame_id, self._frame

    def preview_status(self):
        return self.status
