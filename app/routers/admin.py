from fastapi import APIRouter, HTTPException, Query
from app.repository import DatabaseRepository
from app.settings import settings
import logging

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# We'll need a way to access the repository. For now, we instantiate it or import it.
# Ideally, we inject this as a dependency.
repository = DatabaseRepository(
    database_path=settings.data_dir / "invoices.db",
    db_engine=settings.db_engine,
    mysql_host=settings.mysql_host,
    mysql_port=settings.mysql_port,
    mysql_database=settings.mysql_database,
    mysql_user=settings.mysql_user,
    mysql_password=settings.mysql_password,
    mssql_host=settings.mssql_host,
    mssql_port=settings.mssql_port,
    mssql_database=settings.mssql_database,
    mssql_user=settings.mssql_user,
    mssql_password=settings.mssql_password,
)

@router.get("/users")
def get_admin_users():
    return repository.get_users()

@router.post("/users")
def add_admin_user(payload: dict):
    username = payload.get("username")
    password = payload.get("password")
    role = payload.get("role", "user")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and Password are required.")
    repository.upsert_user(username, password, role)
    return {"status": "ok", "message": f"Saved user '{username}'"}

@router.delete("/users")
def delete_admin_user(username: str):
    if username.strip().lower() in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Default admin and user accounts cannot be deleted.")
    repository.delete_user(username)
    return {"status": "ok", "message": f"Deleted user '{username}'"}
