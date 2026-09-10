"""ORM models. Import tất cả ở đây để SQLAlchemy registry luôn đầy đủ
(quan hệ khai báo bằng chuỗi tên class cần các class đã được nạp)."""
from ..core.database import Base
from .auth_session import AUTH_DEVICE_TYPES, AuthSession
from .catalog import (
    Category,
    Product,
    ProductBatch,
    StockWriteOff,
    StockWriteOffItem,
    Voucher,
)
from .customer import Customer
from .expense import ExpenseCategory, ExpenseTemplate, OperatingExpense
from .fnb import (
    FnbAllocationTransfer,
    FnbActionLog,
    FnbArea,
    FnbCheckLine,
    FnbKitchenTicket,
    FnbKitchenTicketItem,
    FnbManagerApproval,
    FnbServiceCheck,
    FnbServiceSession,
    FnbSessionLine,
    FnbSessionTable,
    FnbStockAllocation,
    FnbTable,
)
from .loyalty import LoyaltyPointEntry, LoyaltyProgram
from .offline import (
    OfflineLease,
    OfflineRecoveryAction,
    OfflineReceipt,
    OfflineReceiptItem,
    OfflineReceiptIssue,
    OfflineReceiptRegistry,
    OfflineStockDeficit,
)
from .order import (
    OfflineBatchStockDeficit,
    Order,
    OrderItem,
    OrderItemBatch,
    OrderPayment,
    OrderReturn,
    OrderReturnItem,
    OrderReturnItemBatch,
)
from .qr_payment import (
    BankReconciliationAction,
    BankWebhookEvent,
    QrPaymentIntent,
)
from .shift import CashMovement, CashShift
from .shop import Shop
from .supplier import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Supplier,
    SupplierPayableEntry,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from .system_log import AssistantAiUsage, SystemLog
from .subscription import (
    ShopSubscription,
    SubscriptionCheckout,
    SubscriptionGrant,
    SubscriptionPayment,
)
from .user import User

__all__ = [
    "Base",
    "AUTH_DEVICE_TYPES",
    "AuthSession",
    "User",
    "Shop",
    "Category",
    "Product",
    "ProductBatch",
    "StockWriteOff",
    "StockWriteOffItem",
    "Voucher",
    "Order",
    "OrderItem",
    "OrderItemBatch",
    "OrderPayment",
    "OrderReturn",
    "OrderReturnItem",
    "OrderReturnItemBatch",
    "CashShift",
    "CashMovement",
    "SystemLog",
    "AssistantAiUsage",
    "Customer",
    "ExpenseCategory",
    "ExpenseTemplate",
    "OperatingExpense",
    "FnbArea",
    "FnbAllocationTransfer",
    "FnbCheckLine",
    "FnbTable",
    "FnbServiceSession",
    "FnbSessionTable",
    "FnbSessionLine",
    "FnbActionLog",
    "FnbKitchenTicket",
    "FnbKitchenTicketItem",
    "FnbStockAllocation",
    "FnbManagerApproval",
    "FnbServiceCheck",
    "LoyaltyProgram",
    "OfflineLease",
    "OfflineRecoveryAction",
    "OfflineReceiptRegistry",
    "OfflineReceipt",
    "OfflineReceiptItem",
    "OfflineReceiptIssue",
    "OfflineStockDeficit",
    "OfflineBatchStockDeficit",
    "QrPaymentIntent",
    "BankWebhookEvent",
    "BankReconciliationAction",
    "LoyaltyPointEntry",
    "Supplier",
    "PurchaseReceipt",
    "PurchaseReceiptItem",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "SupplierPayableEntry",
    "SupplierPayment",
    "SupplierPaymentAllocation",
    "ShopSubscription",
    "SubscriptionGrant",
    "SubscriptionCheckout",
    "SubscriptionPayment",
]
