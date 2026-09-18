"""
SAP 数据源统一接入层。

支持多种 SAP 连接方式：
- adt_sql: 通过 sap-adt-cli 执行 ADT Data Preview SQL，轻量、无需 SDK
- rfc: 通过 pyrfc + SAP NW RFC SDK 调用 RFC_READ_TABLE，适合生产环境大批量数据
- odata: 预留，用于 S/4HANA Cloud 等 OData 场景
"""

from .factory import SAPProviderFactory
from .provider import SAPDataProvider

__all__ = ["SAPProviderFactory", "SAPDataProvider"]
