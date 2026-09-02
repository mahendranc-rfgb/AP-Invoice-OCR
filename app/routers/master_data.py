from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from app.repository import DatabaseRepository
from app.settings import settings
import logging

log = logging.getLogger(__name__)
router = APIRouter(tags=["master-data"])

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

@router.post("/admin/master-data")
def add_admin_master_data(payload: dict):
    cat = payload.get("category")
    code = payload.get("code")
    name = payload.get("name", "")
    extra = payload.get("extra_data", "")
    is_default = bool(payload.get("is_default", False))
    if not cat or not code:
        raise HTTPException(status_code=400, detail="Category and Code are required.")
    repository.upsert_custom_master_data(cat, code, name, extra, is_default)
    return {"status": "ok", "message": f"Saved {code} in {cat}"}

@router.delete("/admin/master-data")
def delete_admin_master_data(category: str, code: str):
    repository.delete_custom_master_data(category, code)
    return {"status": "ok", "message": f"Deleted {code} from {category}"}

@router.post("/admin/erp-sync-master-data")
def sync_sap_master_data_live(category: str | None = Query(None)):
    from app.erp.factory import get_erp_client
    from app.erp.base import ERPClientError
    try:
        target_cat = category.strip().lower() if category and category.strip() and category != "all" else None
        records = get_erp_client().sync_all_master_data(target_category=target_cat)
        
        if target_cat:
            if target_cat.startswith("cost_centers"):
                for c_cat in ("cost_centers1", "cost_centers2", "cost_centers3", "cost_centers4", "cost_centers5"):
                    repository.delete_custom_master_category(c_cat)
            else:
                repository.delete_custom_master_category(target_cat)

        count = 0
        for r in records:
            repository.upsert_custom_master_data(
                category=r["category"],
                code=r["code"],
                name=r["name"],
                extra_data=r["extra_data"],
                is_default=False
            )
            if not target_cat or r["category"] == target_cat or (target_cat.startswith("cost_centers") and r["category"].startswith("cost_centers")):
                count += 1
            
        cat_label = f"category '{target_cat}'" if target_cat else "all categories"
        return {"status": "ok", "message": f"Successfully synchronized {count} active master records live from SAP Business One API for {cat_label}!", "count": count}
    except ERPClientError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to sync master data from SAP API: {exc}")

@router.post("/admin/master-data/set-default")
def set_admin_master_default(payload: dict):
    category = payload.get("category")
    code = payload.get("code")
    if not category or not code:
        raise HTTPException(status_code=400, detail="Category and Code are required.")
    
    existing = repository.get_custom_master_data()
    found = next((item for item in existing if item["category"] == category and item["code"] == code), None)
    name = found["name"] if found else code
    extra = found["extra_data"] if found else ""
    
    repository.upsert_custom_master_data(category, code, name, extra, is_default=True)
    return {"status": "ok", "message": f"Set {code} as default for {category}"}
