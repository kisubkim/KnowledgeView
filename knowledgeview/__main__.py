"""python -m knowledgeview [--host 0.0.0.0] [--port 8770]      plain server
python -m knowledgeview --desktop [--tray]                       tray app (as the installed exe runs)"""

import argparse
import sys

import uvicorn


def main() -> None:
    if "--desktop" in sys.argv:
        from .desktop.entry import main as desktop_main

        sys.exit(desktop_main([a for a in sys.argv[1:] if a != "--desktop"] + ["--no-install"]))
    from .config import server_address

    host, port = server_address()  # KV_HOST/KV_PORT, else knowledgeview.json (설정 > 서버), else 127.0.0.1:8770
    ap = argparse.ArgumentParser(prog="knowledgeview")
    ap.add_argument("--host", default=host)
    ap.add_argument("--port", type=int, default=port)
    args = ap.parse_args()
    uvicorn.run("knowledgeview.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
