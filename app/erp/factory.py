from .base import ERPClient
from .sap_b1 import SapB1Client
from app.settings import settings
import logging

log = logging.getLogger(__name__)

def get_erp_client() -> ERPClient:
    """
    Factory function to return the correct ERP client implementation
    based on the configured ERP_TYPE.
    """
    erp_type = settings.erp_type.upper()
    
    if erp_type == "SAP_B1":
        return SapB1Client()
    # elif erp_type == "SAP_S4HANA":
    #     from .sap_s4hana import SapS4Client
    #     return SapS4Client()
    
    log.warning(f"Unknown ERP_TYPE '{erp_type}'. Defaulting to SAP_B1.")
    return SapB1Client()
