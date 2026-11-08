#!/usr/bin/env python3
"""TOTP 码牌与 UV-K5 点位共用的 38400 串口配置入口。"""

from __future__ import annotations

import argparse
from functools import partial
import os
import sys
from typing import Callable, Sequence

from cli_args import parse_args
from client import Device
from errors import ProvisionError
from i18n import _ as tr
import session as session_mod


def _candidate_ports() -> list[str]:
    """自动探测候选：只认 USB 类串口，避免误试内置 ttyS*。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    markers = ("ttyusb", "ttyacm", "usbmodem", "usbserial", "wchusbserial")
    candidates: list[str] = []
    for info in list_ports.comports():
        name = info.device.lower()
        hwid = (info.hwid or "").lower()
        if "usb" in hwid or any(marker in name for marker in markers):
            candidates.append(info.device)
    return candidates


def _make_serial_device(port: str, trace: Callable[[str], None] | None, *,
                        retry: bool = True) -> Device:
    return Device(session_mod.SerialSession(session_mod.open_serial(port),
                                          trace=trace, retry=retry))


def _open_device(
    args: argparse.Namespace,
    make_device: Callable[[str, Callable[[str], None] | None], Device],
    trace: Callable[[str], None] | None,
) -> Device:
    port = args.port or os.environ.get("TOTP_PORT")
    if port:
        return make_device(port, trace)

    candidates = _candidate_ports()
    if not candidates:
        raise ProvisionError(tr("未发现 USB 串口：请用 --port 指定设备，或设置 TOTP_PORT 环境变量"))

    failures: list[str] = []
    for candidate in candidates:
        try:
            device = make_device(candidate, trace)
        except ProvisionError as exc:
            failures.append(f"{candidate}（{exc}）")
            continue
        try:
            device.open(quick=True)
        except ProvisionError:
            failures.append(tr("{v0}（无响应）").format(v0=candidate))
            device.close()
            continue
        print(tr("已自动选择串口：{v0}").format(v0=candidate), file=sys.stderr)
        return device
    raise ProvisionError(tr("自动探测失败：") + "；".join(failures) + tr("（请用 --port 指定）"))


def _make_trace(verbosity: int) -> Callable[[str], None] | None:
    if verbosity <= 0:
        return None

    def trace(message: str) -> None:
        print(tr("[调试] {v0}").format(v0=message), file=sys.stderr)

    return trace


def main(
    argv: Sequence[str] | None = None,
    *,
    make_device: Callable[[str, Callable[[str], None] | None], Device] | None = None,
) -> int:
    """执行一次 CLI 调用并返回退出码；make_device 可注入串口设备工厂。"""
    args = parse_args(argv)

    trace = _make_trace(args.verbose)
    factory = make_device or partial(_make_serial_device, retry=not args.no_retry)
    try:
        device = _open_device(args, factory, trace)
    except ProvisionError as exc:
        print(tr("错误：{v0}").format(v0=exc), file=sys.stderr)
        return 1

    try:
        with device:
            return args.handler(device, args)
    except (ProvisionError, ValueError) as exc:
        print(tr("错误：{v0}").format(v0=exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(tr("已中断"), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
