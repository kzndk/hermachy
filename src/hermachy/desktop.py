"""Desktop stdio entrypoint; preserves the CLI's compatibility and SSH gates."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .client import ClientError, _config_path, check_remote, load_client_config
from .protocol import LaunchRequest, ProtocolError, encode_request


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermachy desktop transport")
    parser.add_argument("--cwd", required=True)
    args = parser.parse_args()
    try:
        cwd = Path(args.cwd).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise ClientError("workspace must be a directory")
        config_path = _config_path().resolve()
        config = load_client_config(config_path)
        if not config.identity_file.is_file():
            raise ClientError("Hermachy SSH identity is missing; run hermachy --doctor")
        report = check_remote(config, str(cwd))
        if report.get("persistent_gateway") != 1 or report.get("execution_targets") != ["omarchy", "hermes"]:
            raise ClientError("Upgrade both Hermachy endpoints to 0.4.0 or newer")
        for key in list(os.environ):
            if key.startswith(("HERMES_", "HERMACHY_REQUEST", "HERMACHY_REASONING")):
                del os.environ[key]
        request = LaunchRequest(str(cwd), "gateway", connection=uuid.uuid4().hex)
        os.environ["HERMACHY_CLIENT_CONFIG"] = str(config_path)
        os.environ["HERMACHY_REQUEST"] = encode_request(request)
        print(json.dumps({"jsonrpc": "2.0", "method": "desktop.ready", "params": {
            "cwd": str(cwd), "hostname": report.get("studio", {}).get("hostname", "Hermes"),
            "version": report.get("version", ""),
        }}), flush=True)
        from .gateway import main as gateway_main
        sys.argv = ["hermachy-gateway", "-m", "tui_gateway.entry"]
        return gateway_main()
    except (ClientError, ProtocolError, OSError, subprocess.SubprocessError) as exc:
        print(f"Hermachy: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
