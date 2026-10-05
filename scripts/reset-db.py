from pathlib import Path

from switchsign.config import settings
from switchsign.db import init_db


def main() -> None:
    db_path: Path = settings.switchsign_db_path
    answer = input(f"Delete and recreate {db_path}? Type 'yes' to continue: ")
    if answer != "yes":
        print("Aborted.")
        return
    if db_path.exists():
        db_path.unlink()
        print(f"Deleted {db_path}")
    init_db()
    print("Database schema recreated.")


if __name__ == "__main__":
    main()
