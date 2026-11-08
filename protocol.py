"""TOTP 码牌与 UV-K5 点位共用的串口协议编解码。

NOTE: 本模块只做帧与命令编解码，不做串口 I/O 与会话管理，
      便于固件移植、上位机工具与测试共用同一份线格式定义。
"""

from __future__ import annotations

from i18n import _ as tr

import dataclasses
import enum
import string
import struct

SOF = 0xAA
MAX_PAYLOAD = 64
PROTO_VERSION = 1
RESPONSE_BIT = 0x80

ALGO_SHA1 = 1
DIGITS_MIN = 1
DIGITS_MAX = 6
PERIOD_MIN = 15
PERIOD_MAX = 300
DEFAULT_PERIOD = 60
MAX_SECRET_CHARS = 56
BASE32_ALPHABET = frozenset(string.ascii_uppercase + "234567")

CAL_CENTI_PPM_MAX = 30000

CW_DEFAULT_WPM = 8
CW_DEFAULT_TONE_HZ = 700
CW_WPM_MIN = 5
CW_WPM_MAX = 60
CW_TONE_MIN_HZ = 300
CW_TONE_MAX_HZ = 1200


class Cmd(enum.IntEnum):
    PING = 0x01
    GET_TIME = 0x02
    SET_TIME = 0x03
    GET_STATUS = 0x04
    WRITE_SLOT = 0x05
    READ_META = 0x06
    ERASE_SLOT = 0x07
    SET_CALIB = 0x08
    GET_CW = 0x09
    SET_CW = 0x0A


class Status(enum.IntEnum):
    OK = 0x00
    ERR_LEN = 0x01
    # NOTE: 0x02 保留给本地 CRC 失败，设备静默丢弃、从不发送。
    ERR_UNKNOWN_CMD = 0x03
    ERR_PARAM = 0x04
    ERR_STATE = 0x05
    ERR_EEPROM = 0x06
    ERR_BUSY = 0x07
    ERR_UNSUPPORTED = 0x08
    ERR_INTERNAL = 0xFF


class TimeFlag(enum.IntFlag):
    TRUSTED = 1 << 0
    NEVER_SET = 1 << 1
    STALE = 1 << 2


class DeviceFlag(enum.IntFlag):
    SEED_PRESENT = 1 << 0
    RTC_NEVER_SET = 1 << 1
    RTC_STALE = 1 << 2
    LOW_BATTERY = 1 << 3
    CRITICAL_BATTERY = 1 << 4
    FAULT_LATCHED = 1 << 5
    CALIB_ACTIVE = 1 << 6
    IWDG_ENABLED = 1 << 7


@dataclasses.dataclass(frozen=True)
class Frame:
    """一帧协议数据：CMD/SEQ/PAYLOAD，不含 SOF 与 CRC。"""

    cmd: int
    seq: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.cmd <= 0xFF:
            raise ValueError(tr("cmd 超出单字节范围"))
        if not 0 <= self.seq <= 0xFF:
            raise ValueError(tr("seq 超出单字节范围"))
        if not 0 <= len(self.payload) <= 0xFF:
            raise ValueError(tr("payload 超出单字节 LEN 范围"))

    @property
    def is_response(self) -> bool:
        return bool(self.cmd & RESPONSE_BIT)

    def response(self, status: int, data: bytes = b"") -> "Frame":
        """构造本帧的响应：CMD|0x80 + SEQ 回显 + [status] + data。"""
        return Frame(self.cmd | RESPONSE_BIT, self.seq, bytes([status]) + data)


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE：poly=0x1021, init=0xFFFF, refin/refout=false, xorout=0。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode(frame: Frame) -> bytes:
    """帧 → 线字节。PAYLOAD 超过 MAX_PAYLOAD 时拒绝。"""
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError(tr("payload {v0} B 超过协议上限 {v1} B").format(v0=len(frame.payload), v1=MAX_PAYLOAD))
    body = bytes([frame.cmd, frame.seq, len(frame.payload)]) + frame.payload
    crc = crc16_ccitt_false(body)
    return bytes([SOF]) + body + bytes([crc & 0xFF, crc >> 8])


class FrameDecoder:
    """增量帧解码器。

    NOTE: CRC 错误的帧静默丢弃并计入 dropped；收到部分帧后若
          超过 T_frame_rx，调用方必须调用 reset() 丢弃残帧。
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        frames: list[Frame] = []
        while True:
            start = self._buf.find(SOF)
            if start < 0:
                if self._buf:
                    self.dropped += len(self._buf)
                    self._buf.clear()
                break
            if start > 0:
                self.dropped += start
                del self._buf[:start]
            if len(self._buf) < 4:
                break
            length = self._buf[3]
            total = 4 + length + 2
            if len(self._buf) < total:
                break
            body = bytes(self._buf[1 : 4 + length])
            crc_rx = self._buf[4 + length] | (self._buf[5 + length] << 8)
            if crc16_ccitt_false(body) == crc_rx:
                frames.append(Frame(self._buf[1], self._buf[2], bytes(self._buf[4 : 4 + length])))
                del self._buf[:total]
            else:
                # 丢掉一字节后重扫：真正的 SOF 可能藏在伪帧内部。
                self.dropped += 1
                del self._buf[:1]
        return frames

    def reset(self) -> None:
        """帧内超时后清空残帧（T_frame_rx = 100 ms）。"""
        if self._buf:
            self.dropped += len(self._buf)
            self._buf.clear()


def parse_response(frame: Frame) -> tuple[int, bytes]:
    """校验响应帧并拆出 (status, data)。"""
    if not frame.is_response:
        raise ValueError(tr("不是响应帧"))
    if not frame.payload:
        raise ValueError(tr("响应帧缺少状态字节"))
    return frame.payload[0], frame.payload[1:]


def status_name(status: int) -> str:
    try:
        return Status(status).name
    except ValueError:
        return f"UNKNOWN(0x{status:02X})"


def _pack_u64(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError(tr("u64 超出范围"))
    return struct.pack("<Q", value)


def _pack_i16(value: int) -> bytes:
    if not -0x8000 <= value <= 0x7FFF:
        raise ValueError(tr("i16 超出范围"))
    return struct.pack("<h", value)


def ping(seq: int = 0) -> Frame:
    return Frame(Cmd.PING, seq)


def get_time(seq: int = 0) -> Frame:
    return Frame(Cmd.GET_TIME, seq)


def set_time(seq: int = 0, *, unix_ms: int) -> Frame:
    """SET_TIME：载荷为 UTC Unix 毫秒时间戳（u64 小端）。"""
    return Frame(Cmd.SET_TIME, seq, _pack_u64(unix_ms))


def get_status(seq: int = 0) -> Frame:
    return Frame(Cmd.GET_STATUS, seq)


def write_slot(
    seq: int = 0,
    *,
    secret: str,
    algo: int = ALGO_SHA1,
    digits: int = DIGITS_MAX,
    period: int = DEFAULT_PERIOD,
) -> Frame:
    """构造 WRITE_SLOT；参数不合规直接拒绝，避免发出必然被 NACK 的帧。"""
    if algo != ALGO_SHA1:
        raise ValueError(tr("algo 只支持 1 (HMAC-SHA1)"))
    if not DIGITS_MIN <= digits <= DIGITS_MAX:
        raise ValueError(tr("digits 必须在 {v0}..{v1}").format(v0=DIGITS_MIN, v1=DIGITS_MAX))
    if not PERIOD_MIN <= period <= PERIOD_MAX:
        raise ValueError(tr("period 必须在 {v0}..{v1} 秒").format(v0=PERIOD_MIN, v1=PERIOD_MAX))
    if not 1 <= len(secret) <= MAX_SECRET_CHARS or any(c not in BASE32_ALPHABET for c in secret):
        raise ValueError(tr("secret 必须是 1..56 个 A-Z2-7 字符（Base32，无填充）"))
    payload = (
        bytes([algo, digits])
        + struct.pack("<H", period)
        + bytes([len(secret)])
        + secret.encode("ascii")
    )
    return Frame(Cmd.WRITE_SLOT, seq, payload)


def read_meta(seq: int = 0) -> Frame:
    return Frame(Cmd.READ_META, seq)


def erase_slot(seq: int = 0) -> Frame:
    return Frame(Cmd.ERASE_SLOT, seq)


def set_calib(seq: int = 0, *, cal_centi_ppm: int) -> Frame:
    if not -CAL_CENTI_PPM_MAX <= cal_centi_ppm <= CAL_CENTI_PPM_MAX:
        raise ValueError(tr("cal_centi_ppm 超出 ±{v0}").format(v0=CAL_CENTI_PPM_MAX))
    return Frame(Cmd.SET_CALIB, seq, _pack_i16(cal_centi_ppm))


def get_cw(seq: int = 0) -> Frame:
    """读取可选 CW 扩展的当前 WPM 和音调；数显设备不支持。"""
    return Frame(Cmd.GET_CW, seq)


def validate_cw(*, wpm: int | None, tone_hz: int | None) -> None:
    """校验至少一项 CW 参数的范围；None 表示保留设备当前值。"""
    if wpm is None and tone_hz is None:
        raise ValueError(tr("至少指定 --wpm 或 --tone 中的一项"))
    if wpm is not None and not CW_WPM_MIN <= wpm <= CW_WPM_MAX:
        raise ValueError(tr("WPM 必须在 {min}..{max}").format(min=CW_WPM_MIN, max=CW_WPM_MAX))
    if tone_hz is not None and not CW_TONE_MIN_HZ <= tone_hz <= CW_TONE_MAX_HZ:
        raise ValueError(tr("TONE 必须在 {min}..{max} Hz").format(
            min=CW_TONE_MIN_HZ, max=CW_TONE_MAX_HZ))


def set_cw(seq: int = 0, *, wpm: int, tone_hz: int) -> Frame:
    """构造独立 SET_CW 扩展请求；不改变种子载荷或配置 CRC。"""
    validate_cw(wpm=wpm, tone_hz=tone_hz)
    return Frame(Cmd.SET_CW, seq, struct.pack("<BH", wpm, tone_hz))


def config_crc16(*, algo: int, digits: int, period: int, secret: str) -> int:
    """计算 config_crc16，须与设备存储记录保持一致。"""
    blob = bytes([algo, digits]) + struct.pack("<H", period) + bytes([len(secret)]) + secret.encode("ascii")
    return crc16_ccitt_false(blob)
