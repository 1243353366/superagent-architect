"""Application entry point: python run.py"""
import os

from app.main import make_server


def main():
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))
    db_path = os.environ.get("APP_DB", "app.db")
    server = make_server(host, port, db_path)
    print(f"A personal task tracker for daily planning serving on http://{host}:{port} (db: {db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
