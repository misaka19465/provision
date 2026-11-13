"""配置命令的输出、写后核对和 setup 流程。"""

from __future__ import annotations

import argparse
import datetime
import enum
import time

from i18n import _ as tr
import protocol
from client import TIME_SYNC_TOLERANCE_MS, Device, DeviceInfo, Meta, StatusSnapshot, check_compatible
from errors import VerificationError

RESET_CAUSES = {
    0: "POR/BOR",
    1: tr("IWDG（看门狗）"),
    2: tr("软件复位"),
    3: tr("其它"),
    4: tr("未知"),
}

BATTERY_LEVELS = {
    0: tr("未知"),
    1: tr("正常"),
    2: tr("低电"),
    3: tr("极低"),
}


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _fmt_time(ms: int, tz: datetime.tzinfo) -> str:
    moment = datetime.datetime.fromtimestamp(ms / 1000, tz=tz)
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _fmt_local_ms(ms: int) -> str:
    return _fmt_time(ms, datetime.datetime.now().astimezone().tzinfo)


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return tr("{v0} 秒").format(v0=seconds)
    if seconds < 3600:
        return tr("{v0} 分 {v1} 秒").format(v0=seconds // 60, v1=seconds % 60)
    if seconds < 86400:
        return tr("{v0} 小时 {v1} 分").format(v0=seconds // 3600, v1=seconds % 3600 // 60)
    return tr("{v0} 天 {v1} 小时").format(v0=seconds // 86400, v1=seconds % 86400 // 3600)


def _decode_flags(flags: int, enum_cls: type[enum.IntFlag]) -> str:
    names: list[str] = []
    known = 0
    for member in enum_cls:
        if flags & int(member):
            names.append(member.name)
            known |= int(member)
    unknown = flags & ~known
    if unknown:
        names.append(f"UNKNOWN(0x{unknown:04X})")
    return " | ".join(names) if names else tr("无")


def _connect(device: Device) -> DeviceInfo:
    info = device.info if device.info is not None else device.open()
    check_compatible(info)
    return info


def _assert_meta_matches(meta: Meta, *, secret: str, digits: int, period: int) -> None:
    """写后核对：READ_META 的字段与 config_crc16 必须与写入一致。"""
    expected_crc = protocol.config_crc16(
        algo=protocol.ALGO_SHA1, digits=digits, period=period, secret=secret
    )
    problems: list[str] = []
    if not meta.seed_present:
        problems.append(tr("READ_META 报告未写入种子"))
    else:
        if not meta.record_valid:
            problems.append(tr("记录校验位未通过"))
        if meta.algo != protocol.ALGO_SHA1:
            problems.append(tr("algo={v0}，期望 {v1}").format(v0=meta.algo, v1=protocol.ALGO_SHA1))
        if meta.digits != digits:
            problems.append(tr("digits={v0}，期望 {v1}").format(v0=meta.digits, v1=digits))
        if meta.period != period:
            problems.append(tr("period={v0}，期望 {v1}").format(v0=meta.period, v1=period))
        if meta.secret_len != len(secret):
            problems.append(tr("secret_len={v0}，期望 {v1}").format(v0=meta.secret_len, v1=len(secret)))
        if meta.config_crc16 != expected_crc:
            problems.append(
                tr("config_crc16=0x{v0:04X}，期望 0x{v1:04X}").format(v0=meta.config_crc16, v1=expected_crc)
            )
    if problems:
        raise VerificationError(tr("读回配置与写入不一致：") + "；".join(problems))


def _print_generated_seed(secret: str) -> None:
    """随机种子只在写入前打印一次，供操作员登记到服务器。"""
    print(tr("随机种子（Base32）：{v0}").format(v0=secret))
    print(tr("注意：请立即记录并登记到服务器；写入后本工具不再回显种子。"))


def _print_status(snapshot: StatusSnapshot) -> None:
    print(tr("状态位：  {v0}").format(v0=_decode_flags(snapshot.flags, protocol.DeviceFlag)))
    print(tr("复位原因：{v0}").format(v0=RESET_CAUSES.get(snapshot.reset_cause, f'未知({snapshot.reset_cause})')))
    print(tr("电量：    {v0} mV（{v1}）").format(v0=snapshot.battery_mv, v1=BATTERY_LEVELS.get(snapshot.battery_level, '未知')))
    if snapshot.never_synced:
        print(tr("距上次对时：从未对时"))
    else:
        print(tr("距上次对时：{v0}").format(v0=_fmt_duration(snapshot.last_set_age_s)))


# ---- 各子命令 ----


def cmd_ping(device: Device, args: argparse.Namespace) -> int:
    """握手并显示版本；不兼容的协议由异常拒绝，成功返回 0。"""
    info = device.open()
    print(tr("协议版本：v{v0}").format(v0=info.proto_ver))
    print(tr("固件版本：{v0}").format(v0=info.fw_version))
    print(tr("最大载荷：{v0} B").format(v0=info.max_payload))
    print(tr("运行时长：{v0}").format(v0=_fmt_duration(info.uptime_s)))
    print(tr("状态位：  {v0}").format(v0=_decode_flags(info.flags, protocol.DeviceFlag)))
    check_compatible(info)
    return 0


def cmd_get_time(device: Device, args: argparse.Namespace) -> int:
    """显示设备 UTC 时间和可信标志，成功返回 0。"""
    _connect(device)
    reading = device.get_time()
    if not reading.trusted:
        print(tr("设备时间不可用：{flags}；每次上电或复位后须重新对时。").format(
            flags=_decode_flags(reading.flags, protocol.TimeFlag)))
        return 0
    print(tr("设备时间：{v0} UTC").format(v0=_fmt_time(reading.unix_ms, datetime.timezone.utc)))
    print(tr("本地时间：{v0}").format(v0=_fmt_local_ms(reading.unix_ms)))
    print(tr("时间标志：{v0}").format(v0=_decode_flags(reading.flags, protocol.TimeFlag)))
    print(tr("与主机差值：{v0:+d} ms（粗略，含传输延迟）").format(v0=reading.unix_ms - _now_ms()))
    return 0


def cmd_set_time(device: Device, args: argparse.Namespace) -> int:
    """以主机 UTC 对时并显示经容差核验的回读结果，成功返回 0。"""
    _connect(device)
    sent_ms, readback_ms = device.set_time()
    print(tr("对时完成：{v0} UTC").format(v0=_fmt_time(readback_ms, datetime.timezone.utc)))
    print(tr("回读误差：{v0:+d} ms（容差 ±{v1} ms）").format(v0=readback_ms - sent_ms, v1=TIME_SYNC_TOLERANCE_MS))
    return 0


def cmd_status(device: Device, args: argparse.Namespace) -> int:
    """显示设备电量、对时和故障快照，成功返回 0。"""
    _connect(device)
    _print_status(device.status())
    return 0


def cmd_read_meta(device: Device, args: argparse.Namespace) -> int:
    """显示配置元数据，不回显种子；成功返回 0。"""
    _connect(device)
    meta = device.read_meta()
    if not meta.seed_present:
        print(tr("未写入种子（READ_META flags=0x00）"))
        return 0
    algo = "HMAC-SHA1" if meta.algo == protocol.ALGO_SHA1 else tr("未知（algo={v0}）").format(v0=meta.algo)
    print(tr("种子：已写入，{v0} 个 Base32 字符").format(v0=meta.secret_len))
    print(tr("算法：{v0}").format(v0=algo))
    print(tr("位数：{v0}（周期 {v1} s）").format(v0=meta.digits, v1=meta.period))
    print(f"config_crc16：0x{meta.config_crc16:04X}")
    if not meta.record_valid:
        print(tr("警告：记录校验位未通过"))
    return 0


def cmd_write_seed(device: Device, args: argparse.Namespace) -> int:
    """写入已校验的 args.secret/digits/period 并核验元数据，成功返回 0。"""
    _connect(device)
    if args.seed_generated:
        _print_generated_seed(args.secret)
    crc = device.write_slot(secret=args.secret, digits=args.digits, period=args.period)
    meta = device.read_meta()
    _assert_meta_matches(meta, secret=args.secret, digits=args.digits, period=args.period)
    print(
        tr("写入并读回确认：digits={v0}，period={v1} s，种子 {v2} 字符，config_crc16=0x{v3:04X}").format(v0=meta.digits, v1=meta.period, v2=meta.secret_len, v3=crc)
    )
    return 0


def cmd_erase(device: Device, args: argparse.Namespace) -> int:
    """删除种子并确认元数据为空，成功返回 0。"""
    _connect(device)
    device.erase_slot()
    meta = device.read_meta()
    if meta.flags != 0:
        raise VerificationError(tr("擦除后 READ_META flags=0x{v0:02X}，仍报告配置存在").format(v0=meta.flags))
    print(tr("已擦除：READ_META 确认配置为空"))
    return 0


def cmd_set_calib(device: Device, args: argparse.Namespace) -> int:
    """写入已校验的 args.centi_ppm 并显示实际 RTC 档位，成功返回 0。"""
    _connect(device)
    applied = device.set_calib(args.centi_ppm)
    if applied == args.centi_ppm:
        print(tr("校准已写入并生效：{v0} ×0.01 ppm").format(v0=applied))
    else:
        print(tr("校准已写入：请求 {v0} ×0.01 ppm，实际生效 {v1} ×0.01 ppm（最接近档位）")
              .format(v0=args.centi_ppm, v1=applied))
    return 0


def cmd_get_cw(device: Device, args: argparse.Namespace) -> int:
    """读取独立 CW 扩展参数；不支持时由设备错误或超时报告失败。"""
    _connect(device)
    config = device.get_cw()
    print(tr("CW 配置：WPM={wpm}，TONE={tone} Hz，DASH={dash} dit，Character Space={char_space} dit").format(
        wpm=config.wpm, tone=config.tone_hz, dash=config.dash_dits, char_space=config.char_space_dits))
    return 0


def cmd_set_cw(device: Device, args: argparse.Namespace) -> int:
    """保存至少一项 CW 参数并展示核验后的当前值，成功返回 0。"""
    _connect(device)
    config = device.set_cw(wpm=args.wpm, tone_hz=args.tone_hz,
                           dash_dits=args.dash_dits, char_space_dits=args.char_space_dits)
    print(tr("CW 配置已保存并读回确认：WPM={wpm}，TONE={tone} Hz，DASH={dash} dit，Character Space={char_space} dit").format(
        wpm=config.wpm, tone=config.tone_hz, dash=config.dash_dits, char_space=config.char_space_dits))
    return 0


def cmd_setup(device: Device, args: argparse.Namespace) -> int:
    """写入已校验的配置并用设备命令回读核验，成功返回 0。"""
    info = _connect(device)
    print(tr("[1/4] 握手：OK（协议 v{v0}，固件 {v1}）").format(v0=info.proto_ver, v1=info.fw_version))

    if args.seed_generated:
        _print_generated_seed(args.secret)
    crc = device.write_slot(secret=args.secret, digits=args.digits, period=args.period)
    print(tr("[2/4] 写入 TOTP 配置：OK（config_crc16=0x{v0:04X}）").format(v0=crc))

    meta = device.read_meta()
    _assert_meta_matches(meta, secret=args.secret, digits=args.digits, period=args.period)
    print(tr("[3/4] READ_META 命令回读：OK（digits={v0}，period={v1} s，种子 {v2} 字符，CRC 一致）")
          .format(v0=meta.digits, v1=meta.period, v2=meta.secret_len))

    sent_ms, readback_ms = device.set_time()
    print(tr("[4/4] 对时命令回读：OK（误差 {v0:+d} ms）").format(v0=readback_ms - sent_ms))

    _print_status(device.status())
    print(tr("配置完成。"))
    return 0
