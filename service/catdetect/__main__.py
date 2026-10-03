"""Запуск сервиса: `python -m catdetect [--host 0.0.0.0] [--port 8000]`."""

from __future__ import annotations

import argparse
import logging

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="catdetect")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("catdetect.main:create_app", factory=True, host=args.host, port=args.port,
                log_level=args.log_level, proxy_headers=True)


if __name__ == "__main__":
    main()
