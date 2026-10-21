from app.services.redaction import redact_credentials


def add_audit_log(
    db,
    audit_log_model,
    username: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    detail: str = "",
) -> None:
    db.add(
        audit_log_model(
            username=username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=redact_credentials(detail),
        )
    )
