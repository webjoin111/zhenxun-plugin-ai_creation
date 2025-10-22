from pathlib import Path
import sys

import aiofiles
import tomli_w

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

from .config import DEFAULT_TEMPLATES

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PLUGIN_NAME = Path(__file__).parent.name
TEMPLATES_DIR = DATA_PATH / PLUGIN_NAME
TEMPLATES_FILE = TEMPLATES_DIR / "templates.toml"


class TemplateManager:
    def __init__(self):
        self._templates: dict[str, str] = {}

    async def initialize(self):
        """初始化模板管理器，创建默认文件并加载模板。"""
        await self._setup_default_templates()
        await self._load_templates()

    async def _setup_default_templates(self):
        """如果模板文件不存在，则创建并写入默认模板。"""
        if not TEMPLATES_FILE.exists():
            logger.info(f"未找到模板文件，将在 {TEMPLATES_FILE} 创建默认模板。")
            TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(TEMPLATES_FILE, "wb") as f:
                default_toml_content = tomli_w.dumps(DEFAULT_TEMPLATES)
                await f.write(default_toml_content.encode("utf-8"))

    async def _load_templates(self):
        """从TOML文件加载所有模板到内存。"""
        try:
            async with aiofiles.open(TEMPLATES_FILE, "rb") as f:
                content = await f.read()
                self._templates = tomllib.loads(content.decode("utf-8")) or {}
            logger.info(f"成功加载 {len(self._templates)} 个绘图模板。")
        except FileNotFoundError:
            logger.error(f"模板文件 {TEMPLATES_FILE} 不存在，请检查。")
            self._templates = {}
        except tomllib.TOMLDecodeError as e:
            logger.error(f"模板文件 {TEMPLATES_FILE} 格式错误，无法解析: {e}")
            self._templates = {}

    def get_prompt(self, name: str) -> str | None:
        """根据名称获取模板提示词。"""
        return self._templates.get(name)

    def list_templates(self) -> dict[str, str]:
        """获取所有模板的字典。"""
        return self._templates.copy()

    async def reload_templates(self) -> int:
        """从文件重新加载所有模板，并返回加载数量。"""
        await self._load_templates()
        return len(self._templates)

    async def _save_templates(self):
        """将当前模板字典保存到TOML文件。"""
        try:
            async with aiofiles.open(TEMPLATES_FILE, "wb") as f:
                content = tomli_w.dumps(self._templates)
                await f.write(content.encode("utf-8"))
            logger.info(f"成功保存 {len(self._templates)} 个模板到 {TEMPLATES_FILE}。")
        except Exception as e:
            logger.error(f"保存模板文件失败: {e}")

    async def add_template(self, name: str, prompt: str) -> bool:
        """添加一个新模板。"""
        if name in self._templates:
            return False
        self._templates[name] = prompt
        await self._save_templates()
        return True

    async def update_template(self, name: str, prompt: str) -> bool:
        """更新一个现有模板。"""
        if name not in self._templates:
            return False
        self._templates[name] = prompt
        await self._save_templates()
        return True

    async def delete_template(self, name: str) -> bool:
        """删除一个模板。"""
        if name not in self._templates:
            return False
        del self._templates[name]
        await self._save_templates()
        return True

    async def clear_all_templates(self) -> int:
        """清空所有模板。"""
        count = len(self._templates)
        if count == 0:
            return 0
        self._templates.clear()
        await self._save_templates()
        return count


template_manager = TemplateManager()
