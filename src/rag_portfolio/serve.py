import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5052)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "rag_portfolio.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop="rag_portfolio.event_loop:loop_factory",
    )


if __name__ == "__main__":
    main()
