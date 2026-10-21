"""Initialize visitor access without overriding an operator's saved settings."""
import scenic_database as database
from app.core.config import get_settings


def initialize_missing_visitor_services():
    with database.SessionLocal() as db:
        projects = db.query(database.Project).outerjoin(
            database.VisitorService,
            database.VisitorService.project_id == database.Project.id,
        ).filter(
            database.Project.project_type == "scenic",
            database.VisitorService.project_id.is_(None),
        ).all()
        for project in projects:
            db.add(database.VisitorService(
                project_id=project.id, enabled=True,
                price_cents=get_settings().scenic_order_price_cents,
            ))
        db.commit()
        return len(projects)
