"""Демо-счета для ручного запуска банка. Временно: потом их заменит генератор мира (worlds/)."""

from handoff_bench.billing.models import Invoice

DEMO_INVOICES = [
    Invoice(invoice_id="INV-12", customer_id="cus_ann", amount=12000, description="Hosting, March"),
    Invoice(invoice_id="INV-13", customer_id="cus_bob", amount=4500, description="Domain renewal"),
    Invoice(invoice_id="INV-14", customer_id="cus_ann", amount=12500, description="Hosting, April"),
]
