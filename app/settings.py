"""Local, non-secret configuration for SAP Business One connectivity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class SapServiceLayerSettings:
    base_url: str          # v1 – used for Login/Logout
    api_base_url: str      # v2 – used for PurchaseInvoices and all CRUD APIs
    company_db: str
    username: str
    password: str
    verify_tls: bool
    posting_enabled: bool
    posting_mode: str
    data_dir: Path
    ocr_provider: str
    tesseract_cmd: str
    erp_type: str = "SAP_B1"
    db_engine: str = "mysql"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "ap_invoice_ocr"
    mysql_user: str = "ap_user"
    mysql_password: str = "ap_password_123"
    mssql_host: str = "127.0.0.1"
    mssql_port: int = 1433
    mssql_database: str = "ap_invoice_ocr"
    mssql_user: str = "sa"
    mssql_password: str = "yourStrong(!)Password"
    ocr_api_key: str = ""
    ocr_api_url: str = ""
    ocr_model_name: str = "NVIDIA Llama 3.2 Vision OCR"
    vision_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2-vision"

    @property
    def login_url(self) -> str:
        """SAP Service Layer login endpoint (v1)."""
        return f"{self.base_url.rstrip('/')}/Login"

    @property
    def logout_url(self) -> str:
        """SAP Service Layer logout endpoint (v1)."""
        return f"{self.base_url.rstrip('/')}/Logout"

    @property
    def purchase_invoices_url(self) -> str:
        """SAP Service Layer PurchaseInvoices endpoint (v2)."""
        return f"{self.api_base_url.rstrip('/')}/PurchaseInvoices"

    @property
    def drafts_url(self) -> str:
        """SAP Service Layer Drafts endpoint (v2)."""
        return f"{self.api_base_url.rstrip('/')}/Drafts"


def env_flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().casefold() in {"1", "true", "yes", "on"}


settings = SapServiceLayerSettings(
    base_url=os.getenv("SAP_SERVICE_LAYER_BASE_URL", "").rstrip("/"),
    api_base_url=os.getenv("SAP_API_BASE_URL", os.getenv("SAP_SERVICE_LAYER_BASE_URL", "")).rstrip("/"),
    company_db=os.getenv("SAP_COMPANY_DB", ""),
    username=os.getenv("SAP_USERNAME", ""),
    password=os.getenv("SAP_PASSWORD", ""),
    verify_tls=env_flag("SAP_VERIFY_TLS", True),
    posting_enabled=env_flag("SAP_POSTING_ENABLED", False),
    posting_mode=os.getenv("SAP_POSTING_MODE", "invoice").lower(),
    data_dir=Path(os.getenv("APP_DATA_DIR", "data")),
    db_engine=os.getenv("DB_ENGINE", "mssql").lower(),
    mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
    mysql_database=os.getenv("MYSQL_DATABASE", "ap_invoice_ocr"),
    mysql_user=os.getenv("MYSQL_USER", "ap_user"),
    mysql_password=os.getenv("MYSQL_PASSWORD", "ap_password_123"),
    mssql_host=os.getenv("MSSQL_HOST", "collabrium.rfgb.net"),
    mssql_port=int(os.getenv("MSSQL_PORT", "1433")),
    mssql_database=os.getenv("MSSQL_DATABASE", "ap_invoice_ocr"),
    mssql_user=os.getenv("MSSQL_USER", "test"),
    mssql_password=os.getenv("MSSQL_PASSWORD", "Test@123"),
    ocr_provider=os.getenv("OCR_PROVIDER", "nvidia_ocr"),
    tesseract_cmd=os.getenv("OCR_TESSERACT_CMD", ""),
    erp_type=os.getenv("ERP_TYPE", "SAP_B1"),
    ocr_api_key=os.getenv("NVIDIA_API_KEY", os.getenv("OCR_API_KEY", "")),
    ocr_api_url=os.getenv("NVIDIA_OCR_URL", os.getenv("OCR_API_URL", "")),
    ocr_model_name=os.getenv("OCR_MODEL_NAME", "NVIDIA Llama 3.2 Vision OCR"),
    vision_provider=os.getenv("VISION_PROVIDER", "ollama"),
    ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2-vision"),
)


