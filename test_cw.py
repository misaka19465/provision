"""可选 CW 扩展的上位机协议、局部更新和错误处理测试。"""
from __future__ import annotations

import struct
from unittest import mock

import client
from errors import DeviceError, DeviceTimeoutError, ProtocolError, VerificationError
import protocol
import test_cli
import test_client


class CwClientTests(test_client.ClientTestBase):
    def test_get_defaults(self):
        self.assertEqual(self.device.get_cw(), client.CwConfig(8, 700, 3, 3))
        self.assertEqual(self.model.requests, [protocol.Frame(0x09, 0)])

    def test_all_parameters_are_written_independently_of_seed(self):
        self.write_test_seed()
        meta = self.device.read_meta()
        self.model.requests.clear()
        self.assertEqual(self.device.set_cw(wpm=12, tone_hz=900, dash_dits=4, char_space_dits=7),
                         client.CwConfig(12, 900, 4, 7))
        self.assertEqual(self.model.requests, [protocol.Frame(0x0a, 2, b"\x0c\x84\x03\x04\x07")])
        self.assertEqual(self.device.read_meta(), meta)

    def test_partial_updates_preserve_current_values(self):
        self.model.cw_wpm, self.model.cw_tone_hz = 20, 950
        self.model.cw_dash_dits, self.model.cw_char_space_dits = 4, 7
        self.assertEqual(self.device.set_cw(wpm=12), client.CwConfig(12, 950, 4, 7))
        self.assertEqual(self.device.set_cw(tone_hz=700), client.CwConfig(12, 700, 4, 7))
        self.assertEqual(self.device.set_cw(dash_dits=5), client.CwConfig(12, 700, 5, 7))
        self.assertEqual(self.device.set_cw(char_space_dits=9), client.CwConfig(12, 700, 5, 9))
        self.assertEqual([r.cmd for r in self.model.requests], [
            protocol.Cmd.GET_CW, protocol.Cmd.SET_CW] * 4)

    def test_integer_dit_boundaries(self):
        self.assertEqual(self.device.set_cw(dash_dits=1, char_space_dits=255),
                         client.CwConfig(8, 700, 1, 255))
        self.assertEqual(self.device.set_cw(dash_dits=255, char_space_dits=1),
                         client.CwConfig(8, 700, 255, 1))

    def test_invalid_values_are_rejected_before_io(self):
        for params in ({}, {"wpm": 0}, {"wpm": 61}, {"tone_hz": 299}, {"tone_hz": 1201},
                       {"dash_dits": 0}, {"dash_dits": 256}, {"dash_dits": 2.5},
                       {"char_space_dits": -1}, {"char_space_dits": 256},
                       {"char_space_dits": 3.0}, {"char_space_dits": True}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.device.set_cw(**params)
        self.assertEqual(self.model.requests, [])

    def test_bad_response_is_rejected(self):
        for data in (b"", b"\x08\xbc", b"\x08\xbc\x02", b"\x08\xbc\x02\x03",
                     b"\x08\xbc\x02\x03\x03\0", b"\0\xbc\x02\x03\x03", b"\x08\0\0\x03\x03",
                     b"\x08\xbc\x02\0\x03", b"\x08\xbc\x02\x03\0"):
            with self.subTest(data=data), mock.patch.object(self.model, "_data", return_value=data):
                with self.assertRaises(ProtocolError):
                    self.device.get_cw()
        for values in ((8, 700, 3, 3), (12, 900, 3, 7), (12, 900, 4, 3)):
            with self.subTest(values=values), mock.patch.object(
                    self.model, "_data", return_value=struct.pack("<BHBB", *values)):
                with self.assertRaises(VerificationError):
                    self.device.set_cw(wpm=12, tone_hz=900, dash_dits=4, char_space_dits=7)

    def test_unsupported_and_old_devices_report_errors(self):
        for status in (protocol.Status.ERR_UNSUPPORTED, protocol.Status.ERR_UNKNOWN_CMD,
                       protocol.Status.ERR_LEN):
            self.model.fail_status[protocol.Cmd.GET_CW] = status
            self.model.fail_status[protocol.Cmd.SET_CW] = status
            for operation in (self.device.get_cw, lambda: self.device.set_cw(wpm=12),
                              lambda: self.device.set_cw(wpm=12, tone_hz=900, dash_dits=4, char_space_dits=7)):
                with self.subTest(status=status), self.assertRaises(DeviceError) as error:
                    operation()
                self.assertEqual(error.exception.status, status)
        self.assertEqual((self.model.cw_wpm, self.model.cw_tone_hz), (8, 700))

    def test_silent_extension_does_not_write_guessed_defaults(self):
        self.model.drop_commands.add(protocol.Cmd.GET_CW)
        self.device._session._retry = False
        with self.assertRaises(DeviceTimeoutError):
            self.device.set_cw(wpm=12)
        self.assertEqual([r.cmd for r in self.model.requests], [protocol.Cmd.GET_CW])

    def test_extension_wire_vectors(self):
        self.assertEqual(protocol.encode(protocol.get_cw()).hex(), "aa0900000d52")
        self.assertEqual(protocol.encode(protocol.set_cw(
            wpm=12, tone_hz=900, dash_dits=4, char_space_dits=7)).hex(), "aa0a00050c84030407d29a")


class CwCliTests(test_cli.CliTestBase):
    def test_get_and_set_commands(self):
        code, out, err = self.run_cli("get-cw")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("WPM=8，TONE=700 Hz", out)
        self.assertIn("DASH=3 dit，Character Space=3 dit", out)
        code, out, err = self.run_cli("set-cw", "--wpm", "12", "--tone", "950",
                                      "--dash", "4", "--character-space", "7")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("WPM=12，TONE=950 Hz", out)
        self.assertIn("DASH=4 dit，Character Space=7 dit", out)
        code, out, err = self.run_cli("set-cw", "--tone", "700")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("WPM=12，TONE=700 Hz", out)
        self.assertIn("DASH=4 dit，Character Space=7 dit", out)
        code, out, err = self.run_cli("set-cw", "--dash", "5")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("DASH=5 dit，Character Space=7 dit", out)
        code, out, err = self.run_cli("set-cw", "--char-space", "9")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("DASH=5 dit，Character Space=9 dit", out)

    def test_invalid_arguments_never_open_port(self):
        for argv in ((), ("--wpm", "4"), ("--wpm", "61"), ("--tone", "299"), ("--tone", "1201"),
                     ("--dash", "0"), ("--dash", "256"), ("--dash", "2.5"),
                     ("--character-space", "0"), ("--char-space", "256"), ("--char-space", "3.0")):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as error:
                self.run_cli("set-cw", *argv)
            self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.ports, [])

    def test_unsupported_extension_is_optional_for_setup(self):
        self.model.fail_status[protocol.Cmd.GET_CW] = protocol.Status.ERR_UNSUPPORTED
        self.model.fail_status[protocol.Cmd.SET_CW] = protocol.Status.ERR_UNSUPPORTED
        self.model.cw_wpm, self.model.cw_tone_hz = 20, 950
        code, out, err = self.run_cli("setup", test_cli.TEST_SECRET)
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("CW", out)
        self.assertEqual((self.model.cw_wpm, self.model.cw_tone_hz), (20, 950))
        self.assertFalse(any(r.cmd in (protocol.Cmd.GET_CW, protocol.Cmd.SET_CW) for r in self.model.requests))
        for argv in (("get-cw",), ("set-cw", "--wpm", "12", "--tone", "900")):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 1)
            self.assertIn("ERR_UNSUPPORTED", err)
            self.assertNotIn("读回确认", out)
