from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional

class ERPClientError(RuntimeError):
    """Base exception for ERP client errors."""
    pass

class ERPClient(ABC):
    """
    Abstract Base Class defining the required interface for all ERP integrations.
    Any new ERP integration (e.g., SAP B1, SAP S/4HANA, Oracle) must implement these methods.
    """

    @abstractmethod
    def post_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post an AP Invoice draft (or final document) to the ERP system.
        
        Args:
            payload: The normalized invoice data payload.
            
        Returns:
            Dict containing the resulting document details (e.g., DocEntry, DocNum).
        """
        pass

    @abstractmethod
    def get_open_purchase_orders(self, card_code: str) -> List[Dict[str, Any]]:
        """
        Fetch open Purchase Orders for a specific vendor.
        
        Args:
            card_code: The vendor identifier.
            
        Returns:
            List of open purchase order documents.
        """
        pass

    @abstractmethod
    def get_all_open_documents(self, doc_type: str = "PO") -> List[Dict[str, Any]]:
        """
        Fetch all open documents of a specific type (e.g., PO or GRN).
        
        Args:
            doc_type: The type of document to fetch (e.g., "PO", "GRN").
            
        Returns:
            List of open documents.
        """
        pass

    @abstractmethod
    def sync_all_master_data(self, target_category: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch active master data from the ERP system.
        
        Args:
            target_category: Optional category to filter (e.g., "vendors", "items", "accounts").
            
        Returns:
            List of normalized master data dictionaries:
            [{"category": ..., "code": ..., "name": ..., "extra_data": ...}]
        """
        pass

    @abstractmethod
    def fetch_historical_ap_invoices(self, top: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch historical posted AP Invoices for AI model training.
        
        Args:
            top: The number of recent records to fetch.
            
        Returns:
            List of historical AP Invoice documents.
        """
        pass
