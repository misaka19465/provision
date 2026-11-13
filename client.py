"""设备命令层：字段校验、类型化封装与写后读回确认。

NOTE: 本层把帧级会话（session.py）包装成有类型、有长度校验的命令调用；
      所有"设备回了什么才算成功"的判断都在这里，CLI 只负责展示与流程编排。
"""

from __future__ import annotations

from i18n import _ as tr

import dataclasses
import struct
import time
from typing import Callable

import protocol
import session as session_mod
import totp
from errors import DeviceError, DeviceNotFoundError, ProtocolError, VerificationError

PING_DATA_LEN = 10
TIME_DATA_LEN = 9
SET_TIME_DATA_LEN = 8
STATUS_DATA_LEN = 10
META_DATA_LEN = 8
CALIB_DATA_LEN = 2
CW_DATA_LEN = 5
MIN_MAX_PAYLOAD = 61
SEED_PRESENT_BIT = 1 << 0
RECORD_VALID_BIT = 1 << 1
NEVER_SYNCED_AGE_S = 0xFFFFFFFF

# NOTE: 沿用原协议的 ±100 ms 回读容差；UV-K5 的实际漂移仍须台架测量。
TIME_SYNC_TOLERANCE_MS = 100
TIME_SYNC_ATTEMPTS = 3


def _u16(data: bytes) -> int:
    return struct.unpack_from("<H", data)[0]


def _u32(data: bytes) -> int:
    return struct.unpack_from("<I", data)[0]


def _u64(data: bytes) -> int:
    return struct.unpack_from("<Q", data)[0]


def _i16(data: bytes) -> int:
    return struct.unpack_from("<h", data)[0]


@dataclasses.dataclass(frozen=True)
class DeviceInfo:
    """PING 响应。"""

    proto_ver: int
    fw_major: int
    fw_minor: int
    max_payload: int
    flags: int
    uptime_s: int

    @property
    def fw_version(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}"

    @property
    def seed_present(self) -> bool:
        return bool(self.flags & protocol.DeviceFlag.SEED_PRESENT)


@dataclasses.dataclass(frozen=True)
class TimeReading:
    """GET_TIME / SET_TIME 回读。"""

    unix_ms: int
    flags: int

    @property
    def trusted(self) -> bool:
        return bool(self.flags & protocol.TimeFlag.TRUSTED)


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    """GET_STATUS 响应。"""

    flags: int
    reset_cause: int
    battery_mv: int
    battery_level: int
    last_set_age_s: int

    @property
    def never_synced(self) -> bool:
        return self.last_set_age_s == NEVER_SYNCED_AGE_S


@dataclasses.dataclass(frozen=True)
class Meta:
    """READ_META 响应。"""

    flags: int
    algo: int
    digits: int
    period: int
    secret_len: int
    config_crc16: int

    @property
    def seed_present(self) -> bool:
        return bool(self.flags & SEED_PRESENT_BIT)

    @property
    def record_valid(self) -> bool:
        return bool(self.flags & RECORD_VALID_BIT)


@dataclasses.dataclass(frozen=True)
class CwConfig:
    """可选 GET_CW / SET_CW 扩展的当前生效参数。"""

    wpm: int
    tone_hz: int
    dash_dits: int
    char_space_dits: int


def check_compatible(info: DeviceInfo) -> None:
    """proto_ver == 1 且 max_payload ≥ 61，否则拒绝继续。"""
    if info.proto_ver != protocol.PROTO_VERSION:
        raise ProtocolError(
            tr("设备协议版本为 v{v0}，本工具只支持 v{v1}").format(v0=info.proto_ver, v1=protocol.PROTO_VERSION)
        )
    if info.max_payload < MIN_MAX_PAYLOAD:
        raise ProtocolError(
            tr("设备最大载荷 {v0} B 小于 {v1} B，无法写入最长种子").format(v0=info.max_payload, v1=MIN_MAX_PAYLOAD)
        )


def _require_len(cmd: protocol.Cmd, data: bytes, expected: int) -> None:
    if len(data) != expected:
        raise ProtocolError(tr("命令 0x{v0:02X} 响应数据 {v1} B，期望 {v2} B").format(v0=cmd, v1=len(data), v2=expected))


class Device:
    """面向单台设备的命令客户端；每次 CLI 调用对应一个实例。"""

    def __init__(self, port_session: session_mod.SerialSession) -> None:
        self._session = port_session
        self._info: DeviceInfo | None = None

    @property
    def info(self) -> DeviceInfo | None:
        return self._info

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "Device":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request_ok(self, frame: protocol.Frame, *, storage: bool = False,
                    refresh: Callable[[protocol.Frame], protocol.Frame] | None = None) -> bytes:
        status, data = self._session.request(frame, storage=storage, refresh=refresh)
        if status != protocol.Status.OK:
            raise DeviceError(frame.cmd, status)
        return data

    def open(self, *, quick: bool = False) -> DeviceInfo:
        """前导码 + PING 握手；quick 供自动探测用（1 轮 2 次，减少等待）。"""
        if quick:
            response = self._session.wake(rounds=1, attempts=2)
        else:
            response = self._session.wake()
        if response is None:
            raise DeviceNotFoundError(tr("设备无响应（已发送前导码与 PING，请检查接线、串口号与设备电源）"))
        info = self._parse_ping(*response)
        self._info = info
        return info

    def ping(self) -> DeviceInfo:
        frame = protocol.ping(self._session.next_seq())
        return self._parse_ping(*self._session.request(frame))

    @staticmethod
    def _parse_ping(status: int, data: bytes) -> DeviceInfo:
        if status != protocol.Status.OK:
            raise DeviceError(protocol.Cmd.PING, status)
        _require_len(protocol.Cmd.PING, data, PING_DATA_LEN)
        fw_ver = _u16(data[1:3])
        return DeviceInfo(
            proto_ver=data[0],
            fw_major=fw_ver >> 8,
            fw_minor=fw_ver & 0xFF,
            max_payload=data[3],
            flags=_u16(data[4:6]),
            uptime_s=_u32(data[6:10]),
        )

    def get_time(self) -> TimeReading:
        frame = protocol.get_time(self._session.next_seq())
        data = self._request_ok(frame)
        _require_len(protocol.Cmd.GET_TIME, data, TIME_DATA_LEN)
        return TimeReading(unix_ms=_u64(data), flags=data[8])

    def set_time(self, *, max_attempts: int | None = None) -> tuple[int, int]:
        """发送前采样 UTC 毫秒并回读确认（容差 ±100 ms）。

        NOTE: 每次重试都重新采样时间；重传旧时间戳只会把设备时间写回过去。
        """
        max_attempts = TIME_SYNC_ATTEMPTS if max_attempts is None else max_attempts
        last_delta: int | None = None
        sent_ms = 0
        sent_tick = 0.0

        def refresh(frame: protocol.Frame) -> protocol.Frame:
            nonlocal sent_ms, sent_tick
            sent_ms = time.time_ns() // 1_000_000
            sent_tick = time.monotonic()
            return protocol.set_time(frame.seq, unix_ms=sent_ms)

        for _ in range(max_attempts):
            frame = protocol.set_time(self._session.next_seq(), unix_ms=0)
            data = self._request_ok(frame, refresh=refresh)
            _require_len(protocol.Cmd.SET_TIME, data, SET_TIME_DATA_LEN)
            readback_ms = _u64(data)
            elapsed_ms = (time.monotonic() - sent_tick) * 1000
            if (abs(readback_ms - sent_ms) <= TIME_SYNC_TOLERANCE_MS and
                    elapsed_ms <= TIME_SYNC_TOLERANCE_MS):
                return sent_ms, readback_ms
            last_delta = round(max(abs(readback_ms - sent_ms), elapsed_ms))
        raise VerificationError(
            tr("对时回读误差 {v0:+d} ms，超过 ±{v1} ms").format(v0=last_delta, v1=TIME_SYNC_TOLERANCE_MS)
        )

    def status(self) -> StatusSnapshot:
        frame = protocol.get_status(self._session.next_seq())
        data = self._request_ok(frame)
        _require_len(protocol.Cmd.GET_STATUS, data, STATUS_DATA_LEN)
        return StatusSnapshot(
            flags=_u16(data[0:2]),
            reset_cause=data[2],
            battery_mv=_u16(data[3:5]),
            battery_level=data[5],
            last_set_age_s=_u32(data[6:10]),
        )

    def write_slot(
        self,
        *,
        secret: str,
        digits: int = protocol.DIGITS_MAX,
        period: int = protocol.DEFAULT_PERIOD,
    ) -> int:
        """写种子并对响应里的 config_crc16 做本地复算比对。"""
        secret = totp.normalize_secret(secret)
        expected_crc = protocol.config_crc16(
            algo=protocol.ALGO_SHA1, digits=digits, period=period, secret=secret
        )
        frame = protocol.write_slot(
            self._session.next_seq(), secret=secret, digits=digits, period=period
        )
        data = self._request_ok(frame, storage=True)
        _require_len(protocol.Cmd.WRITE_SLOT, data, 2)
        readback_crc = _u16(data)
        if readback_crc != expected_crc:
            raise VerificationError(
                tr("WRITE_SLOT 回读 config_crc16=0x{v0:04X}，本地复算=0x{v1:04X}").format(v0=readback_crc, v1=expected_crc)
            )
        return readback_crc

    def read_meta(self) -> Meta:
        frame = protocol.read_meta(self._session.next_seq())
        data = self._request_ok(frame)
        _require_len(protocol.Cmd.READ_META, data, META_DATA_LEN)
        return Meta(
            flags=data[0],
            algo=data[1],
            digits=data[2],
            period=_u16(data[3:5]),
            secret_len=data[5],
            config_crc16=_u16(data[6:8]),
        )

    def erase_slot(self) -> None:
        frame = protocol.erase_slot(self._session.next_seq())
        data = self._request_ok(frame, storage=True)
        _require_len(protocol.Cmd.ERASE_SLOT, data, 0)

    def set_calib(self, centi_ppm: int) -> int:
        """以 0.01 ppm 写入 RTC 校准并返回实际档位；UV-K5 返回不支持。"""
        frame = protocol.set_calib(self._session.next_seq(), cal_centi_ppm=centi_ppm)
        data = self._request_ok(frame, storage=True)
        _require_len(protocol.Cmd.SET_CALIB, data, CALIB_DATA_LEN)
        return _i16(data)

    @staticmethod
    def _parse_cw(cmd: protocol.Cmd, data: bytes) -> CwConfig:
        _require_len(cmd, data, CW_DATA_LEN)
        config = CwConfig(*struct.unpack("<BHBB", data))
        try:
            protocol.validate_cw(wpm=config.wpm, tone_hz=config.tone_hz,
                                 dash_dits=config.dash_dits, char_space_dits=config.char_space_dits)
        except ValueError as exc:
            raise ProtocolError(tr("设备返回的 CW 参数超出支持范围")) from exc
        return config

    def get_cw(self) -> CwConfig:
        """读取当前 CW 参数；默认流程不调用，不支持的设备直接报告命令错误。"""
        frame = protocol.get_cw(self._session.next_seq())
        return self._parse_cw(protocol.Cmd.GET_CW, self._request_ok(frame))

    def set_cw(self, *, wpm: int | None = None, tone_hz: int | None = None,
               dash_dits: int | None = None, char_space_dits: int | None = None) -> CwConfig:
        """持久化并核验 CW 参数；省略项先通过 GET_CW 读取并保留，时序为整数 dit。"""
        protocol.validate_cw(wpm=wpm, tone_hz=tone_hz,
                             dash_dits=dash_dits, char_space_dits=char_space_dits)
        if any(value is None for value in (wpm, tone_hz, dash_dits, char_space_dits)):
            current = self.get_cw()
            wpm = current.wpm if wpm is None else wpm
            tone_hz = current.tone_hz if tone_hz is None else tone_hz
            dash_dits = current.dash_dits if dash_dits is None else dash_dits
            char_space_dits = current.char_space_dits if char_space_dits is None else char_space_dits
        frame = protocol.set_cw(self._session.next_seq(), wpm=wpm, tone_hz=tone_hz,
                                dash_dits=dash_dits, char_space_dits=char_space_dits)
        applied = self._parse_cw(protocol.Cmd.SET_CW, self._request_ok(frame, storage=True))
        if applied != CwConfig(wpm, tone_hz, dash_dits, char_space_dits):
            raise VerificationError(tr("SET_CW 回读的 CW 参数与请求不一致"))
        return applied

