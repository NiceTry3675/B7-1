"""Run only the B7-1 internal API adapter; llama-server is managed separately."""

import argparse

import uvicorn

from rpi_llm.main import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
