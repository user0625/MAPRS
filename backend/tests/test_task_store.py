from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect, text

from backend.api.task_store import APITaskStatus, DatabaseTaskStore


def make_store(tmp_path):
    store = DatabaseTaskStore(f"sqlite:///{tmp_path / 'tasks.db'}")
    store.create_tables()
    return store


def test_create_read_update_and_metadata_merge(tmp_path):
    store = make_store(tmp_path)
    store.create_task("one", "/tmp/one.pdf", {"language": "zh", "kept": 1})
    store.mark_running("one")
    store.mark_completed("one", "/tmp/report.md", metadata={"kept": 2, "pages": 3})

    record = store.get_task("one")
    assert record is not None
    assert record.status == APITaskStatus.COMPLETED
    assert record.task_metadata == {"language": "zh", "kept": 2, "pages": 3}


def test_store_is_persistent_across_instances(tmp_path):
    first = make_store(tmp_path)
    first.create_task("persistent", "/tmp/paper.pdf")
    second = make_store(tmp_path)
    assert second.get_task("persistent") is not None


def test_sqlite_startup_adds_ask_page_scope_columns(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE paper_messages (id VARCHAR PRIMARY KEY)")
        )

    DatabaseTaskStore(database_url).create_tables()

    columns = {
        column["name"] for column in inspect(engine).get_columns("paper_messages")
    }
    assert {"page_start", "page_end"} <= columns


def test_list_is_newest_first_and_paginated(tmp_path):
    store = make_store(tmp_path)
    for task_id in ("old", "middle", "new"):
        store.create_task(task_id, f"/tmp/{task_id}.pdf")
    # Ensure deterministic timestamps without relying on wall-clock resolution.
    store._update("old", created_at=datetime.utcnow() - timedelta(days=2))
    store._update("middle", created_at=datetime.utcnow() - timedelta(days=1))

    items, total = store.list_tasks(limit=2, offset=1)
    assert total == 3
    assert [item.task_id for item in items] == ["middle", "old"]
    assert store.list_tasks(limit=10, offset=10)[0] == []


def test_recovery_only_fails_non_terminal_tasks(tmp_path):
    store = make_store(tmp_path)
    for task_id in ("pending", "running", "completed", "failed"):
        store.create_task(task_id, f"/tmp/{task_id}.pdf")
    store.mark_running("running")
    store.mark_completed("completed", "/tmp/report.md")
    store.mark_failed("failed", "original failure")

    assert store.recover_interrupted_tasks() == 2
    assert store.get_task("pending").status == APITaskStatus.FAILED
    assert "restarted" in store.get_task("running").error_message
    assert store.get_task("completed").status == APITaskStatus.COMPLETED
    assert store.get_task("failed").error_message == "original failure"
