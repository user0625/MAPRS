from __future__ import annotations
import argparse
import json
from sqlmodel import Session, select
from backend.api.task_store import APITaskRecord, DatabaseTaskStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import legacy SQLite api_tasks into PostgreSQL."
    )
    parser.add_argument("--sqlite-url", default="sqlite:///backend/data/tasks.db")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    source = DatabaseTaskStore(args.sqlite_url)
    target = DatabaseTaskStore(args.database_url)
    target.create_tables()
    imported = skipped = failed = 0
    with Session(source.engine) as source_session:
        for old in source_session.exec(select(APITaskRecord)).all():
            try:
                if target.get_task(old.task_id, include_deleted=True):
                    skipped += 1
                    continue
                data = old.model_dump()
                data["task_metadata"] = json.loads(
                    json.dumps(data.get("task_metadata") or {})
                )
                with Session(target.engine) as session:
                    session.add(APITaskRecord.model_validate(data))
                    session.commit()
                imported += 1
            except Exception:
                failed += 1
    print(json.dumps({"imported": imported, "skipped": skipped, "failed": failed}))


if __name__ == "__main__":
    main()
