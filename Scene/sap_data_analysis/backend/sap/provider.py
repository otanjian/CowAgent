"""SAP 数据提供者抽象基类。"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class SAPDataProvider(ABC):
    """统一 SAP 数据接入接口。

    所有 SAP 连接实现（ADT SQL、RFC、OData）都需要实现这个接口，
    这样上层 Handler 和前端不需要关心底层协议差异。
    """

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """测试与 SAP 系统的连接是否可用。

        Returns:
            包含 status 等信息的字典，例如 {"status": "success", "system": "..."}
        """
        pass

    @abstractmethod
    def fetch(self, scene_id: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """根据场景 ID 拉取数据，返回经过字段映射后的字典列表。

        Args:
            scene_id: 场景/子场景 ID，例如 supplier_qualification。
            params: 额外参数，例如 max_rows、company_code、purchasing_org 等。

        Returns:
            字段名已经映射为中文表头的字典列表。
        """
        pass
