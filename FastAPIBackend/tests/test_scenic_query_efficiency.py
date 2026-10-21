import datetime

from sqlalchemy import event


def _create_project(client, user, name):
    response = client.post(
        "/api/v1/scenic/projects",
        headers=user["headers"],
        json={"name": name, "description": "query regression"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _count_selects(engine):
    statements = []

    def before_cursor_execute(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    return statements, before_cursor_execute


def test_scenic_audit_filters_project_scope_before_limit(client, register_scenic_user):
    import scenic_database as database

    user = register_scenic_user("audit_scope_user", "audit-scope@example.com")
    project_id = _create_project(client, user, "Audit target")
    noise_project_id = _create_project(client, user, "Audit noise")
    now = datetime.datetime.now(datetime.UTC)
    with database.SessionLocal() as db:
        db.add(
            database.AuditLog(
                username=user["username"],
                action="target.old_but_relevant",
                resource_type="project",
                resource_id=str(project_id),
                detail="must survive unrelated global noise",
                created_at=now - datetime.timedelta(days=1),
            )
        )
        db.add_all(
            database.AuditLog(
                username=user["username"],
                action=f"noise.{index}",
                resource_type="project",
                resource_id=str(noise_project_id),
                detail="unrelated",
                created_at=now + datetime.timedelta(seconds=index),
            )
            for index in range(220)
        )
        db.commit()

    response = client.get(
        f"/api/v1/scenic/projects/{project_id}/audit?limit=10",
        headers=user["headers"],
    )
    assert response.status_code == 200, response.text
    actions = {row["action"] for row in response.json()["audit"]}
    assert "target.old_but_relevant" in actions
    assert not any(action.startswith("noise.") for action in actions)


def test_scenic_media_history_detail_and_status_use_bounded_queries(
    client, register_scenic_user
):
    import scenic_database as database

    user = register_scenic_user("query_scale_user", "query-scale@example.com")
    project_id = _create_project(client, user, "Query scale")
    with database.SessionLocal() as db:
        media_rows = []
        target_rows = []
        for media_index in range(3):
            media = database.MediaFile(
                project_id=project_id,
                uploader_username=user["username"],
                filename=f"media-{media_index}.jpg",
                object_key=f"query-scale/media-{media_index}.jpg",
                content_type="image/jpeg",
                size_bytes=128,
                sha256=f"{media_index + 1:064x}",
                media_type="image",
                status="indexed",
            )
            db.add(media)
            db.flush()
            media_rows.append(media)
            for target_index in range(2):
                target = database.DetectionTarget(
                    project_id=project_id,
                    media_id=media.id,
                    crop_path=f"query-scale/crop-{media_index}-{target_index}.jpg",
                    crop_object_key=f"query-scale/crop-{media_index}-{target_index}.jpg",
                    source_info="query regression",
                    vector_id=f"query-scale-{media_index}-{target_index}",
                )
                db.add(target)
                target_rows.append(target)
        db.flush()

        for task_index in range(2):
            task = database.SearchTask(
                project_id=project_id,
                username=user["username"],
                query_type="text",
                query_content=f"task-{task_index}",
                filters_json='{"min_similarity": 0.1}',
                top_k=6,
            )
            db.add(task)
            db.flush()
            db.add_all(
                database.SearchResult(
                    search_task_id=task.id,
                    target_id=target_rows[result_index % len(target_rows)].id,
                    rank=result_index + 1,
                    path=target_rows[result_index % len(target_rows)].crop_path,
                    similarity=0.99 - result_index * 0.01,
                    source_info="query regression",
                )
                for result_index in range(6)
            )

        job_statuses = ["succeeded"] * 12 + ["running"] * 3 + ["failed"] * 5
        db.add_all(
            database.ProcessingJob(
                id=f"query-job-{index}",
                project_id=project_id,
                job_type="media_index",
                status=status,
                progress=1.0 if status != "running" else 0.5,
                message=status,
            )
            for index, status in enumerate(job_statuses)
        )
        db.commit()

    media_selects, media_listener = _count_selects(database.engine)
    try:
        media_response = client.get(
            f"/api/v1/scenic/projects/{project_id}/media?page=2&page_size=2",
            headers=user["headers"],
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", media_listener)
    assert media_response.status_code == 200, media_response.text
    media_payload = media_response.json()
    assert media_payload["pagination"]["total"] == 3
    assert media_payload["pagination"]["total_pages"] == 2
    assert len(media_payload["media"]) == 1
    assert media_payload["media"][0]["target_count"] == 2
    assert len(media_payload["media"][0]["targets"]) == 2
    assert len(media_selects) <= 7

    history_selects, history_listener = _count_selects(database.engine)
    try:
        history_response = client.get(
            "/api/v1/scenic/history?page=1&page_size=1&result_limit=2",
            headers=user["headers"],
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", history_listener)
    assert history_response.status_code == 200, history_response.text
    history_payload = history_response.json()
    assert history_payload["pagination"]["total"] == 2
    assert len(history_payload["history"]) == 1
    assert history_payload["history"][0]["result_count"] == 6
    assert len(history_payload["history"][0]["results"]) == 2
    assert len(history_selects) <= 7

    history_id = history_payload["history"][0]["id"]
    detail_response = client.get(
        f"/api/v1/scenic/history/{history_id}?page=2&page_size=2",
        headers=user["headers"],
    )
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    assert detail_payload["task"]["result_count"] == 6
    assert detail_payload["pagination"]["total_pages"] == 3
    assert len(detail_payload["results"]) == 2

    status_response = client.get(
        f"/api/v1/scenic/projects/{project_id}/status",
        headers=user["headers"],
    )
    assert status_response.status_code == 200, status_response.text
    status_payload = status_response.json()
    assert status_payload["status"] == "processing"
    assert status_payload["current"] == 12
    assert status_payload["total"] == 20
    assert status_payload["indexed_media"] == 3
    assert status_payload["indexed_targets"] == 6
