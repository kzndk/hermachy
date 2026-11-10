import base64
import json
import unittest

from hermachy.protocol import (
    FORCED_COMMAND, PROTOCOL_VERSION, LaunchRequest, ProtocolError, decode_request, encode_request,
    parse_forced_command,
)


def encode_raw(payload) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class ProtocolTests(unittest.TestCase):
    def test_version_two_and_forced_command_name(self) -> None:
        self.assertEqual(PROTOCOL_VERSION, 2)
        self.assertEqual(FORCED_COMMAND, "hermachy-v2")

    def test_round_trip_all_actions(self) -> None:
        requests = [
            LaunchRequest("/home/omarchy-user/project"),
            LaunchRequest("/home/omarchy-user/project", "resolve"),
            LaunchRequest("/home/omarchy-user/project", "resolve", "named session"),
            LaunchRequest("/home/omarchy-user/project", "attach", connection="a" * 32),
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(decode_request(encode_request(request)), request)

    def test_attach_requires_a_connection_and_only_attach_accepts_one(self):
        with self.assertRaises(ProtocolError):
            encode_request(LaunchRequest("/tmp", "attach"))
        for action in ("doctor", "resolve"):
            with self.subTest(action=action), self.assertRaises(ProtocolError):
                decode_request(encode_raw({"v": 2, "cwd": "/tmp", "action": action, "connection": "a" * 32}))
        for bad in ("A" * 32, "a" * 31, "g" * 32):
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                decode_request(encode_raw({"v": 2, "cwd": "/tmp", "action": "attach", "connection": bad}))

    def test_encoding_is_urlsafe_and_unpadded(self) -> None:
        token = encode_request(LaunchRequest("/home/u/project with spaces"))
        self.assertNotIn("=", token)
        self.assertRegex(token, r"^[A-Za-z0-9_-]+$")

    def test_parse_forced_command(self) -> None:
        request = LaunchRequest("/home/u/project", "resolve", "session name")
        self.assertEqual(parse_forced_command(f"hermachy-v2 {encode_request(request)}"), request)

    def test_rejects_bad_forced_commands_including_the_0_5_client(self) -> None:
        token = encode_request(LaunchRequest("/tmp"))
        for bad in [None, "", "hermachy-v2", f"hermachy-v1 {token}", f"hermachy-v2 {token} extra"]:
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                parse_forced_command(bad)

    def test_rejects_non_mapping_or_unknown_fields(self) -> None:
        for payload in ([], {"v": 2, "cwd": "/tmp", "action": "doctor", "extra": True}, {"v": 2, "cwd": "/tmp"},
                        {"v": 2, "cwd": "/tmp", "action": "attach", "connection": "a" * 32, "env": {}}):
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                decode_request(encode_raw(payload))

    def test_rejects_wrong_version(self) -> None:
        for version in (1, 3, "2", True):
            with self.subTest(version=version), self.assertRaises(ProtocolError):
                decode_request(encode_raw({"v": version, "cwd": "/tmp", "action": "doctor"}))

    def test_rejects_non_absolute_or_unsafe_cwd(self) -> None:
        for cwd in ("relative", "", "/tmp\n", "/tmp\x00", "/" + "x" * 5000):
            with self.subTest(cwd=cwd), self.assertRaises(ProtocolError):
                decode_request(encode_raw({"v": 2, "cwd": cwd, "action": "doctor"}))

    def test_rejects_invalid_actions_and_session_combinations(self) -> None:
        for payload in (
            {"v": 2, "cwd": "/tmp", "action": "shell"},
            {"v": 2, "cwd": "/tmp", "action": "new"},
            {"v": 2, "cwd": "/tmp", "action": "gateway"},
            {"v": 2, "cwd": "/tmp", "action": "doctor", "session": "x"},
            {"v": 2, "cwd": "/tmp", "action": "attach", "session": "x", "connection": "a" * 32},
            {"v": 2, "cwd": "/tmp", "action": "resolve", "session": ""},
            {"v": 2, "cwd": "/tmp", "action": "resolve", "session": 3},
            {"v": 2, "cwd": "/tmp", "action": "resolve", "session": "x\n"},
            {"v": 2, "cwd": "/tmp", "action": "resolve", "session": "--yolo"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                decode_request(encode_raw(payload))

    def test_rejects_malformed_base64_and_json(self) -> None:
        for token in ("", "not base64!", "%%%", base64.urlsafe_b64encode(b"{nope").decode().rstrip("=")):
            with self.subTest(token=token), self.assertRaises(ProtocolError):
                decode_request(token)

    def test_rejects_oversized_token(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_request("A" * 9000)

    def test_rejects_pathologically_deep_json_without_traceback(self) -> None:
        token = base64.urlsafe_b64encode(b"[" * 100000).decode().rstrip("=")
        with self.assertRaises(ProtocolError):
            decode_request(token)

    def test_constructor_validation_runs_during_encoding(self) -> None:
        with self.assertRaises(ProtocolError):
            encode_request(LaunchRequest("relative", "doctor"))


if __name__ == "__main__":
    unittest.main()
