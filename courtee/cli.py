import argparse
import os

from courtee.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Courtee database setup")
    parser.add_argument("command", choices=["init-db"])
    parser.add_argument("--db", default=os.environ.get("COURTEE_DB", "data/courtee.sqlite3"))
    args = parser.parse_args()
    database = Database(args.db)
    database.migrate()
    database.seed()
    print(f"Migrated and seeded {database.path}")


if __name__ == "__main__":
    main()
