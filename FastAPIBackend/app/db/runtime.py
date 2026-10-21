import uuid

from sqlalchemy import event, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import redact_database_url
from app.services.database_migrations import upgrade_database
from app.services.time_utils import utc_now


DEFAULT_ROLES = ("admin", "operator", "viewer")


def configure_sqlite_engine(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _seed_default_roles(engine, session_factory, role_model, *, description_prefix: str = "") -> None:
    descriptions = f"{description_prefix.strip()} " if description_prefix.strip() else ""
    values = [
        {"name": role_name, "description": f"{descriptions}{role_name} role", "created_at": utc_now()}
        for role_name in DEFAULT_ROLES
    ]
    if engine.dialect.name == "postgresql":
        statement = postgresql_insert(role_model).values(values).on_conflict_do_nothing(index_elements=["name"])
    elif engine.dialect.name == "sqlite":
        statement = sqlite_insert(role_model).values(values).on_conflict_do_nothing(index_elements=["name"])
    else:
        with session_factory() as db:
            existing = {row[0] for row in db.query(role_model.name).all()}
            db.add_all(
                role_model(name=role_name, description=f"{descriptions}{role_name} role")
                for role_name in DEFAULT_ROLES
                if role_name not in existing
            )
            db.commit()
        return
    with engine.begin() as connection:
        connection.execute(statement)


def _seed_project_owners_as_members(engine, session_factory, project_model, member_model) -> None:
    source = select(project_model.id, project_model.owner_username, text("'owner'"), project_model.created_at)
    columns = ["project_id", "username", "role", "created_at"]
    if engine.dialect.name == "postgresql":
        statement = postgresql_insert(member_model).from_select(columns, source).on_conflict_do_nothing(
            index_elements=["project_id", "username"]
        )
    elif engine.dialect.name == "sqlite":
        statement = sqlite_insert(member_model).from_select(columns, source).prefix_with("OR IGNORE")
    else:
        with session_factory() as db:
            for project in db.query(project_model).all():
                exists = db.query(member_model.id).filter(
                    member_model.project_id == project.id,
                    member_model.username == project.owner_username,
                ).first()
                if not exists:
                    db.add(member_model(project_id=project.id, username=project.owner_username, role="owner"))
            db.commit()
        return
    with engine.begin() as connection:
        connection.execute(statement)


def _backfill_user_required_fields(session_factory, user_model) -> None:
    with session_factory() as db:
        now = utc_now()
        changed = False
        users = db.query(user_model).filter(
            or_(
                user_model.public_id.is_(None),
                user_model.public_id == "",
                user_model.avatar_url.is_(None),
                user_model.role.is_(None),
                user_model.is_active.is_(None),
                user_model.created_at.is_(None),
            )
        ).all()
        for user in users:
            if not user.public_id:
                user.public_id = str(uuid.uuid4())
                changed = True
            if user.avatar_url is None:
                user.avatar_url = ""
                changed = True
            if user.role is None:
                user.role = "operator"
                changed = True
            if user.is_active is None:
                user.is_active = True
                changed = True
            if user.created_at is None:
                user.created_at = now
                changed = True
        if changed:
            db.commit()


def initialize_domain_database(
    *,
    database_url: str,
    engine,
    metadata,
    scope: str,
    session_factory,
    role_model,
    user_model,
    project_model,
    project_member_model,
    role_description_prefix: str = "",
) -> None:
    upgrade_database(database_url=database_url, engine=engine, metadata=metadata, scope=scope)
    _seed_default_roles(engine, session_factory, role_model, description_prefix=role_description_prefix)
    _backfill_user_required_fields(session_factory, user_model)
    _seed_project_owners_as_members(engine, session_factory, project_model, project_member_model)


def check_connection(engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def database_info(engine, database_url: str) -> dict:
    return {
        "url": redact_database_url(database_url),
        "dialect": engine.dialect.name,
        "driver": engine.dialect.driver,
    }


def session_dependency(session_factory):
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
