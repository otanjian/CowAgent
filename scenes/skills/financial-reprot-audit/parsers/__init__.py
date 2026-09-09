"""
财务文件解析模块

支持解析主流财务软件导出的文件及通用格式：
- 财务软件：金蝶、用友、SAP等
- 通用格式：Excel(.xlsx/.xls)、Word(.docx)、PDF(.pdf)、CSV(.csv)

使用示例：
    from parsers import FileParserManager
    
    manager = FileParserManager()
    result = manager.parse_file('/path/to/financial_report.xlsx')
    
    if result.success:
        financial_data = result.data
        print(f"营业收入: {financial_data.get('revenue')}")
"""

from .base_parser import BaseParser, ParseResult, FinancialData
from .parser_manager import FileParserManager
from .file_identifier import FileIdentifier, FileType, SoftwareType

# 便捷函数：直接解析文件
def parse_file(file_path: str, parser_name: str = None) -> ParseResult:
    """便捷函数：使用默认管理器解析单个文件"""
    manager = FileParserManager()
    return manager.parse_file(file_path, parser_name)

def scan_and_parse(directory: str, recursive: bool = True, extensions: list = None):
    """便捷函数：扫描目录并解析所有支持的文件"""
    manager = FileParserManager()
    return manager.scan_and_parse(directory, recursive=recursive, extensions=extensions)

__all__ = [
    'BaseParser', 
    'ParseResult', 
    'FinancialData',
    'FileParserManager',
    'FileIdentifier',
    'FileType',
    'SoftwareType',
    'parse_file',
    'scan_and_parse',
]
