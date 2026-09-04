import base64
import json
import unittest

from hermes_link.protocol import (
    MAX_TOKEN_BYTES,
    LaunchRequest,
    ProtocolError,
    decode_request,
    encode_request,
    parse_forced_command,
)


def encode_raw(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ProtocolTests(unittest.TestCase):
    def test_round_trip_all_actions(self) -> None:
        requests = [
            LaunchRequest("/home/omarchy-user/project"),
            LaunchRequest("/home/omarchy-user/project", "continue"),
            LaunchRequest("/home/omarchy-user/project", "continue", "named session"),
            LaunchRequest("/home/omarchy-user/project", "resume", "20260904_abcd"),
            LaunchRequest("/home/omarchy-user/project", "doctor"),
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(decode_request(encode_request(request)), request)

    def test_encoding_is_urlsafe_and_unpadded(self) -> None:
        token = encode_request(LaunchRequest("/home/u/project with spaces"))
        self.assertNotIn("=", token)
        self.assertRegex(token, r"^[A-Za-z0-9_-]+$")

    def test_parse_forced_command(self) -> None:
        request = LaunchRequest("/home/u/project", "resume", "session name")
        self.assertEqual(
            parse_forced_command(f"hlink-v1 {encode_request(request)}"), request
        )

    def test_rejects_bad_forced_commands(self) -> None:
        bad = [None, "", "hlink-v2 abc", "hlink-v1", "hlink-v1 abc extra"]
        for command in bad:
            with self.subTest(command=command), self.assertRaises(ProtocolError):
                parse_forced_command(command)

    def test_rejects_non_mapping_or_unknown_fields(self) -> None:
        payloads = [
            [],
            {"v": 1, "cwd": "/tmp", "action": "new", "extra": True},
            {"v": 1, "cwd": "/tmp"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                decode_request(encode_raw(payload))

    def test_rejects_wrong_version(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "version"):
            decode_request(encode_raw({"v": 2, "cwd": "/tmp", "action": "new"}))

    def test_rejects_non_absolute_or_unsafe_cwd(self) -> None:
        values = ["relative", "", "/tmp\nnext", "/tmp\x00next", 123]
        for cwd in values:
            payload = {"v": 1, "cwd": cwd, "action": "new"}
            with self.subTest(cwd=cwd), self.assertRaises(ProtocolError):
                decode_request(encode_raw(payload))

    def test_rejects_invalid_actions_and_session_combinations(self) -> None:
        payloads = [
            {"v": 1, "cwd": "/tmp", "action": "shell"},
            {"v": 1, "cwd": "/tmp", "action": "new", "session": "x"},
            {"v": 1, "cwd": "/tmp", "action": "doctor", "session": "x"},
            {"v": 1, "cwd": "/tmp", "action": "resume"},
            {"v": 1, "cwd": "/tmp", "action": "resume", "session": ""},
            {"v": 1, "cwd": "/tmp", "action": "continue", "session": 3},
            {"v": 1, "cwd": "/tmp", "action": "continue", "session": "x\n"},
            {"v": 1, "cwd": "/tmp", "action": "continue", "session": "--yolo"},
            {"v": 1, "cwd": "/tmp", "action": "resume", "session": "-p"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                decode_request(encode_raw(payload))

    def test_rejects_malformed_base64_and_json(self) -> None:
        for token in ["not+urlsafe", "_w", encode_raw("not a mapping")]:
            with self.subTest(token=token), self.assertRaises(ProtocolError):
                decode_request(token)

    def test_rejects_oversized_token(self) -> None:
        token = "A" * (MAX_TOKEN_BYTES + 1)
        with self.assertRaisesRegex(ProtocolError, "large"):
            decode_request(token)

    def test_rejects_pathologically_deep_json_without_traceback(self) -> None:
        raw = ("[" * 1100 + "]" * 1100).encode("utf-8")
        token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        with self.assertRaises(ProtocolError):
            decode_request(token)

    def test_constructor_validation_runs_during_encoding(self) -> None:
        invalid = LaunchRequest("relative", "new")
        with self.assertRaises(ProtocolError):
            encode_request(invalid)


if __name__ == "__main__":
    unittest.main()
