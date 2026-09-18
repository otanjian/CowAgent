"""SAP Provider 工厂，根据配置创建对应的 SAP 数据提供者。"""

from typing import Any, Dict

from .adt_sql_provider import AdtSqlProvider
from .provider import SAPDataProvider
from .rfc_provider import RfcProvider


class SAPProviderFactory:
    """根据 provider 类型创建对应的 SAPDataProvider 实例。"""

    _providers = {
        "adt_sql": AdtSqlProvider,
        "rfc": RfcProvider,
    }

    @classmethod
    def create(cls, provider_type: str, creds: Dict[str, Any]) -> SAPDataProvider:
        """创建 SAP Provider。

        Args:
            provider_type: 提供者类型，例如 adt_sql、rfc。
            creds: 连接所需参数。

        Returns:
            SAPDataProvider 实例。
        """
        provider_type = (provider_type or "adt_sql").lower()
        provider_cls = cls._providers.get(provider_type)
        if not provider_cls:
            raise ValueError(f"不支持的 SAP provider 类型: {provider_type}")
        return provider_cls(**creds)

    @classmethod
    def register(cls, name: str, provider_cls: type):
        """注册新的 Provider 类型，便于后续扩展 OData 等。"""
        cls._providers[name.lower()] = provider_cls
