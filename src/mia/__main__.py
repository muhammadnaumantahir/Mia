"""Run the local Mia application server."""

import os


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv()

    import uvicorn

    host = os.getenv("MIA_HOST", "127.0.0.1")
    port = int(os.getenv("MIA_PORT", "8765"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            "Remote binding is disabled in this foundation build. "
            "Keep MIA_HOST on localhost until authentication is implemented."
        )
    uvicorn.run("mia.observatory.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
