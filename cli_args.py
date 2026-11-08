"""共用的命令行参数、默认值和连接前校验。"""

from __future__ import annotations

import argparse
from typing import Sequence

import commands
from i18n import _ as tr
import protocol
import totp


def _validate_write_seed(args: argparse.Namespace) -> None:
    if args.secret is None:
        args.secret = totp.generate_secret(args.random_bytes)
        args.seed_generated = True
    else:
        args.secret = totp.normalize_secret(args.secret)
        args.seed_generated = False
    if not protocol.DIGITS_MIN <= args.digits <= protocol.DIGITS_MAX:
        raise ValueError(tr("--digits 必须在 {v0}..{v1}").format(v0=protocol.DIGITS_MIN, v1=protocol.DIGITS_MAX))
    if not protocol.PERIOD_MIN <= args.period <= protocol.PERIOD_MAX:
        raise ValueError(tr("--period 必须在 {v0}..{v1} 秒").format(v0=protocol.PERIOD_MIN, v1=protocol.PERIOD_MAX))


def _random_bytes_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(tr("必须是整数"))
    if not totp.MIN_SECRET_BYTES <= value <= totp.MAX_SECRET_BYTES:
        raise argparse.ArgumentTypeError(
            tr("必须在 {v0}..{v1} 字节").format(v0=totp.MIN_SECRET_BYTES, v1=totp.MAX_SECRET_BYTES)
        )
    return value


def _add_seed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "secret",
        nargs="?",
        help=tr("Base32 种子（A-Z 与 2-7；可带空格与填充，写入前规范化）；缺省时生成随机种子"),
    )
    parser.add_argument(
        "--random-bytes",
        type=_random_bytes_arg,
        default=totp.DEFAULT_SECRET_BYTES,
        metavar="N",
        help=(
            tr("随机种子字节数 {v0}..{v1}（默认 {v2}，仅未给出种子时生效）").format(v0=totp.MIN_SECRET_BYTES, v1=totp.MAX_SECRET_BYTES, v2=totp.DEFAULT_SECRET_BYTES)
        ),
    )
    parser.add_argument(
        "--digits",
        type=int,
        default=protocol.DIGITS_MAX,
        help=tr("口令位数 {v0}..{v1}（默认 {v2}）").format(v0=protocol.DIGITS_MIN, v1=protocol.DIGITS_MAX, v2=protocol.DIGITS_MAX),
    )
    parser.add_argument(
        "--period",
        type=int,
        default=protocol.DEFAULT_PERIOD,
        help=tr("口令周期秒数 {v0}..{v1}（默认 {v2}）(AI)").format(
            v0=protocol.PERIOD_MIN, v1=protocol.PERIOD_MAX, v2=protocol.DEFAULT_PERIOD),
    )


def _validate_set_calib(args: argparse.Namespace) -> None:
    limit = protocol.CAL_CENTI_PPM_MAX
    if not -limit <= args.centi_ppm <= limit:
        raise ValueError(tr("校准值必须在 ±{v0}（0.01 ppm）内(AI)").format(v0=limit))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provision.py",
        description=tr("TOTP 点位配置工具（固定 38400 8N1）(AI)"),
        epilog=(
            tr("示例：\n  provision.py -p /dev/ttyUSB0 setup\n  provision.py -p /dev/ttyUSB0 setup --period 30\n  provision.py -p /dev/ttyUSB0 set-calib -500\n(AI)")
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-p",
        "--port",
        help=tr("串口设备；缺省时自动探测 USB 串口（也可用 TOTP_PORT 环境变量）"),
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help=tr("打印串口收发帧（可重复）"))
    parser.add_argument("--no-retry", action="store_true",
                        help=tr("关闭串口重传和会话重建；PING 握手仅尝试一次"))

    sub = parser.add_subparsers(dest="command", required=True, metavar=tr("命令"))

    ping = sub.add_parser("ping", help=tr("连通性与版本握手"))
    ping.set_defaults(handler=commands.cmd_ping)

    get_time = sub.add_parser("get-time", help=tr("读取设备 UTC 时间与可信标志"))
    get_time.set_defaults(handler=commands.cmd_get_time)

    set_time = sub.add_parser("set-time", help=tr("以主机 UTC 时间对时（回读误差 ≤100 ms）"))
    set_time.set_defaults(handler=commands.cmd_set_time)

    status = sub.add_parser("status", help=tr("读取电量/对时/复位/故障快照"))
    status.set_defaults(handler=commands.cmd_status)

    read_meta = sub.add_parser("read-meta", help=tr("读取配置元信息（不回显种子）"))
    read_meta.set_defaults(handler=commands.cmd_read_meta)

    write_seed = sub.add_parser("write-seed", help=tr("写入种子并读回比对（不给种子则随机生成）"))
    _add_seed_arguments(write_seed)
    write_seed.set_defaults(handler=commands.cmd_write_seed, validate=_validate_write_seed)

    erase = sub.add_parser("erase", help=tr("删除种子并确认记录为空"))
    erase.set_defaults(handler=commands.cmd_erase)

    calib = sub.add_parser("set-calib", help=tr("写 RTC 校准（0.01 ppm，0 = 关闭；支持情况由设备响应）(AI)"))
    calib.add_argument(
        "centi_ppm",
        type=int,
        help=tr("校准值，{v0}..{v1}（0.01 ppm）(AI)").format(
            v0=-protocol.CAL_CENTI_PPM_MAX, v1=protocol.CAL_CENTI_PPM_MAX),
    )
    calib.set_defaults(handler=commands.cmd_set_calib, validate=_validate_set_calib)

    setup = sub.add_parser(
        "setup", help=tr("写入种子、对时并通过设备命令回读核验（不给种子则随机生成）(AI)")
    )
    _add_seed_arguments(setup)
    setup.set_defaults(handler=commands.cmd_setup, validate=_validate_write_seed)

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """校验协议共有的参数约束，设备额外限制由命令响应报告；用法错误退出 2。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    validate = getattr(args, "validate", None)
    if validate is not None:
        try:
            validate(args)
        except ValueError as exc:
            parser.error(str(exc))
    return args
