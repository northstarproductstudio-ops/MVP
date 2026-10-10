"""File-type library: one adapter class per type key."""

from __future__ import annotations

from core.domain.adapters.base import Adapter, SourceSheet, TransformResult
from core.domain.adapters.erp_sales_orders import ErpSalesOrdersAdapter
from core.domain.adapters.market_category_insight import MarketCategoryInsightAdapter
from core.domain.adapters.platform_po import (
    PlatformPoBigbasketAdapter,
    PlatformPoInstamartAdapter,
    PlatformPoZeptoAdapter,
)
from core.domain.adapters.platform_stock_blinkit import PlatformStockBlinkitAdapter
from core.domain.adapters.sku_master import CustomAdapter, SkuMasterAdapter

REGISTRY: dict[str, type[Adapter]] = {
    a.key: a
    for a in (
        ErpSalesOrdersAdapter,
        PlatformPoInstamartAdapter,
        PlatformPoZeptoAdapter,
        PlatformPoBigbasketAdapter,
        PlatformStockBlinkitAdapter,
        MarketCategoryInsightAdapter,
        SkuMasterAdapter,
        CustomAdapter,
    )
}


def get_adapter(key: str) -> Adapter:
    if key not in REGISTRY:
        return CustomAdapter()
    return REGISTRY[key]()


__all__ = ["REGISTRY", "Adapter", "SourceSheet", "TransformResult", "get_adapter"]
