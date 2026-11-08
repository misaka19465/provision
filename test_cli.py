"""provision.py 的命令行流程测试（进程内仿真设备）。"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

import client
import protocol
import provision
import session
import totp
from fake_device import FakeDevice, FakeSerial

TEST_SECRET = "JBSWY3DPEHPK3PXP"


class CliTestBase(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.multiple(
            session,
            PREAMBLE_SILENCE_S=0.001,
            TIMEOUT_FAST_S=0.05,
            TIMEOUT_STORAGE_S=0.05,
            POLL_INTERVAL_S=0.0005,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = mock.patch.object(client.time, "time_ns", return_value=1790899200000000000)
        clock.start()
        self.addCleanup(clock.stop)

        self.model = FakeDevice()
        self.ports: list[FakeSerial] = []

        def factory(port: str, trace: object) -> client.Device:
            fake = FakeSerial(self.model)
            self.ports.append(fake)
            return client.Device(session.SerialSession(fake, trace=trace))  # type: ignore[arg-type]

        self.factory = factory

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = provision.main(["--port", "fake", *argv], make_device=self.factory)
        return code, stdout.getvalue(), stderr.getvalue()


class BasicCommandTests(CliTestBase):
    def test_default_serial_factory_matches_verified_firmware_baud(self) -> None:
        fake = FakeSerial(self.model)
        with mock.patch.object(session, "open_serial", return_value=fake) as opened:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = provision.main(["--port", "fake", "--no-retry", "ping"])
        self.assertEqual(code, 0)
        opened.assert_called_once_with("fake")
        self.assertTrue(fake.closed)

    def test_baud_override_is_not_supported(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("--baud", "38400", "ping")
        self.assertEqual(ctx.exception.code, 2)

    def test_ping(self) -> None:
        code, out, err = self.run_cli("ping")
        self.assertEqual(code, 0)
        self.assertIn("协议版本：v1", out)
        self.assertIn("固件版本：1.0", out)
        self.assertNotIn("IWDG_ENABLED", out)
        self.assertEqual(err, "")

    def test_get_time_shows_never_set(self) -> None:
        code, out, _ = self.run_cli("get-time")
        self.assertEqual(code, 0)
        self.assertIn("NEVER_SET", out)

    def test_set_time_syncs_device(self) -> None:
        code, out, _ = self.run_cli("set-time")
        self.assertEqual(code, 0)
        self.assertIn("对时完成", out)
        self.assertEqual(self.model.time_flags, int(protocol.TimeFlag.TRUSTED))

    def test_set_calib_for_supporting_device(self) -> None:
        code, out, _ = self.run_cli("set-calib", "-500")
        self.assertEqual(code, 0)
        self.assertIn("-500", out)
        self.assertEqual(self.model.calib_centi_ppm, -500)

    def test_set_calib_reports_unsupported_device_response(self) -> None:
        self.model.fail_status[protocol.Cmd.SET_CALIB] = protocol.Status.ERR_UNSUPPORTED
        code, _, err = self.run_cli("set-calib", "-500")
        self.assertEqual(code, 1)
        self.assertIn("ERR_UNSUPPORTED", err)
        self.assertEqual(self.model.calib_centi_ppm, 0)

    def test_status(self) -> None:
        self.model.battery_level = 2
        self.model.battery_mv = 2900
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("电量：    2900 mV（低电）", out)
        self.assertIn("从未对时", out)

class SeedCommandTests(CliTestBase):
    def test_write_seed_and_read_meta(self) -> None:
        code, out, _ = self.run_cli("write-seed", TEST_SECRET)
        self.assertEqual(code, 0)
        self.assertIn("config_crc16=0x7A9D", out)
        self.assertEqual(self.model.secret, TEST_SECRET)
        self.assertEqual(self.model.digits, 6)

        code, out, _ = self.run_cli("read-meta")
        self.assertEqual(code, 0)
        self.assertIn("config_crc16：0x7A9D", out)
        self.assertIn("16 个 Base32 字符", out)

    def test_write_seed_normalizes_input(self) -> None:
        code, _, _ = self.run_cli("write-seed", " jbswy3dpehpk3pxp= ", "--digits", "4", "--period", "15")
        self.assertEqual(code, 0)
        self.assertEqual(self.model.secret, TEST_SECRET)
        self.assertEqual(self.model.digits, 4)
        self.assertEqual(self.model.period, 15)

    def test_write_seed_without_secret_generates_random(self) -> None:
        code, out, _ = self.run_cli("write-seed")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"随机种子（Base32）：[A-Z2-7]{32}")
        self.assertIsNotNone(self.model.secret)
        self.assertIn(self.model.secret, out)
        self.assertEqual(self.model.digits, 6)

    def test_write_seed_random_bytes_option(self) -> None:
        code, out, _ = self.run_cli("write-seed", "--random-bytes", "10")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"随机种子（Base32）：[A-Z2-7]{16}")
        self.assertEqual(self.model.secret, out.split("随机种子（Base32）：")[1].splitlines()[0])

    def test_given_secret_does_not_print_random_notice(self) -> None:
        code, out, _ = self.run_cli("write-seed", TEST_SECRET)
        self.assertEqual(code, 0)
        self.assertNotIn("随机种子", out)

    def test_read_meta_empty(self) -> None:
        code, out, _ = self.run_cli("read-meta")
        self.assertEqual(code, 0)
        self.assertIn("未写入种子", out)

    def test_erase(self) -> None:
        self.run_cli("write-seed", TEST_SECRET)
        code, out, _ = self.run_cli("erase")
        self.assertEqual(code, 0)
        self.assertIn("已擦除", out)
        self.assertFalse(self.model.seed_present)

    def test_erase_verification_failure_exits_1(self) -> None:
        self.run_cli("write-seed", TEST_SECRET)
        # 擦除失败注入：设备端无动作，READ_META 仍报告配置存在。
        with mock.patch.object(provision.Device, "erase_slot", side_effect=lambda: None):
            code, _, err = self.run_cli("erase")
        self.assertEqual(code, 1)
        self.assertIn("仍报告配置存在", err)

    def test_device_error_exits_1(self) -> None:
        self.model.fail_status[protocol.Cmd.ERASE_SLOT] = protocol.Status.ERR_EEPROM
        code, _, err = self.run_cli("erase")
        self.assertEqual(code, 1)
        self.assertIn("ERR_EEPROM", err)


class ArgumentTests(CliTestBase):
    def test_invalid_secret_is_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("write-seed", "ABC1")
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_digits_is_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("write-seed", TEST_SECRET, "--digits", "0")
        self.assertEqual(ctx.exception.code, 2)

    def test_device_period_rejection_leaves_seed_unchanged(self) -> None:
        self.model.fail_status[protocol.Cmd.WRITE_SLOT] = protocol.Status.ERR_PARAM
        code, _, err = self.run_cli("setup", TEST_SECRET, "--period", "15")
        self.assertEqual(code, 1)
        self.assertIn("ERR_PARAM", err)
        self.assertFalse(self.model.seed_present)
        self.assertEqual([r.cmd for r in self.model.requests],
                         [protocol.Cmd.PING, protocol.Cmd.WRITE_SLOT])

    def test_supporting_device_allows_15_second_period(self) -> None:
        code, _, _ = self.run_cli("write-seed", TEST_SECRET, "--period", "15")
        self.assertEqual(code, 0)
        self.assertEqual(self.model.period, 15)

    def test_period_outside_protocol_range_is_usage_error(self) -> None:
        for period in ("14", "301"):
            with self.subTest(period=period), self.assertRaises(SystemExit) as ctx:
                self.run_cli("setup", TEST_SECRET, "--period", period)
            self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.ports, [])

    def test_invalid_calibration_is_rejected_before_opening_port(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("set-calib", "30001")
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.ports, [])

    def test_random_bytes_out_of_range_is_usage_error(self) -> None:
        for value in ("0", str(totp.MAX_SECRET_BYTES + 1), "abc"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit) as ctx:
                    self.run_cli("write-seed", "--random-bytes", value)
                self.assertEqual(ctx.exception.code, 2)

class SetupTests(CliTestBase):
    def test_setup_full_flow(self) -> None:
        with mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            code, out, _ = self.run_cli("setup", TEST_SECRET)
        self.assertEqual(code, 0)
        for step in ("[1/4]", "[2/4]", "[3/4]", "[4/4]"):
            self.assertIn(step, out)
        self.assertIn("READ_META 命令回读", out)
        self.assertNotIn("听码", out)
        self.assertIn("配置完成", out)
        self.assertEqual(self.model.secret, TEST_SECRET)
        self.assertEqual(self.model.time_flags, int(protocol.TimeFlag.TRUSTED))

    def test_setup_without_secret_generates_random(self) -> None:
        code, out, _ = self.run_cli("setup")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"随机种子（Base32）：[A-Z2-7]{32}")
        self.assertIn(self.model.secret, out)
        self.assertEqual(self.model.time_flags, int(protocol.TimeFlag.TRUSTED))

    def test_same_setup_sequence_with_and_without_watchdog(self) -> None:
        for watchdog in (0, int(protocol.DeviceFlag.IWDG_ENABLED)):
            with self.subTest(watchdog=watchdog):
                self.model = FakeDevice()
                original_flags = self.model._flags
                with mock.patch.object(self.model, "_flags", side_effect=lambda: original_flags() | watchdog):
                    code, out, err = self.run_cli("setup", TEST_SECRET)
                self.assertEqual(code, 0)
                self.assertEqual(err, "")
                self.assertEqual(self.model.period, 60)
                self.assertEqual([r.cmd for r in self.model.requests], [
                    protocol.Cmd.PING, protocol.Cmd.WRITE_SLOT, protocol.Cmd.READ_META,
                    protocol.Cmd.SET_TIME, protocol.Cmd.GET_STATUS,
                ])
                self.assertIn("READ_META 命令回读", out)
                self.assertNotIn("人工", out)
                self.assertNotIn("CW", out)

    def test_explicit_period_overrides_common_default(self) -> None:
        code, _, err = self.run_cli("setup", TEST_SECRET, "--period", "30")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(self.model.period, 30)

    def test_setup_readback_mismatch_exits_1(self) -> None:
        self.model.meta_crc_xor = 0x0100
        code, _, err = self.run_cli("setup", TEST_SECRET)
        self.assertEqual(code, 1)
        self.assertIn("读回配置与写入不一致", err)


class AutodetectTests(CliTestBase):
    def test_no_ports_reports_hint(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(provision, "_candidate_ports", return_value=[]):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                code = provision.main(["ping"], make_device=self.factory)
        self.assertEqual(code, 1)
        self.assertIn("--port", stderr.getvalue())

    def test_autodetect_picks_first_responder(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(provision, "_candidate_ports", return_value=["fake0", "fake1"]):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = provision.main(["ping"], make_device=self.factory)
        self.assertEqual(code, 0)
        self.assertIn("已自动选择串口：fake0", stderr.getvalue())

    def test_autodetect_skips_dead_port(self) -> None:
        self.model.silent = True
        with mock.patch.object(provision, "_candidate_ports", return_value=["dead"]):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = provision.main(["ping"], make_device=self.factory)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
