from arclet.alconna import (
    Alconna,
    AllParam,
    Args,
    CommandMeta,
    Field,
    MultiVar,
    Option,
    Subcommand,
)
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import is_type
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna.uniseg import Image as UniImage

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.limiters import FreqLimiter

from .config import base_config

draw_limiter = FreqLimiter(base_config.get("draw_cd", 120))

__plugin_meta__ = PluginMetadata(
    name="AI创作",
    description="多功能 AI 创作插件，集成了豆包（Doubao）与多模态大模型 API，支持文生图、图生图及强大的模板系统。",
    usage="""
## 🎨 AI绘图

### 基础用法
- `draw [描述]` - 根据文本生成图片
- `draw [描述] [图片]` - 结合图片和文本进行创作
- `[引用图片] draw [描述]` - 引用消息中的图片进行创作

> 💡 **提示**: 支持 `@用户` 获取其头像进行图生图，可同时 `@多用户` 或附加 `多图片`

---

## ⚙️ 绘图参数

- **`-e, --engine <引擎>`** - 临时指定绘图引擎
    - `doubao` - 使用豆包浏览器模拟引擎
    - `api` - 使用多模态大模型 API 引擎

- **`-t, --template <模板>`** - 使用预设的风格模板
    - 可通过 `绘图模板 list` 查看所有可用模板

- **`-o, --optimize <模式>`** - 临时开启/关闭提示词优化
    - `on` - 开启 AI 润色，与 `-t` 合用时会智能融合描述与模板
    - `off` - 关闭 AI 润色，与 `-t` 合用时进行简单文本拼接

---

## 🎴 绘图模板管理

指令格式：`绘图模板 <子命令>` 或 `preset <子命令>`

### 普通用户命令
- **`list`** - 查看所有可用的绘图模板
- **`info <模板名>`** - 查看指定模板的详细提示词内容

### 超级用户命令
- **`create [图片] [描述]`** - 基于图片智能创建新模板（进入交互式会话）
- **`optimize <名称> [指令]`** - 根据指令优化现有模板（进入交互式会话）
- **`add <名称> <提示词>`** - 手动添加一个新的绘图模板
- **`del <名称1> [名称2]...`** - 删除一个或多个指定的模板
- **`edit <名称> <新提示词>`** - 修改现有模板的提示词内容
- **`reload`** - 从配置文件重新加载所有模板
- **`clear`** - 清空所有绘图模板（需二次确认）

---

## ✨ 使用示例

```
draw 一只可爱的猫
draw 一只可爱的猫 -e api
draw -t 手办 @用户
draw -t 巨物手办 场景是夜晚 @用户
draw [附带图片] -o on 换成赛博朋克风格
```
    """,
    type="application",
    homepage="https://github.com/webjoin111/zhenxun-plugin-ai_creation",
    supported_adapters={"~onebot.v11"},
    extra=PluginExtraData(
        author="webjoin111",
        version="1.2.7",
        configs=[
            RegisterConfig(
                module="ai_creation",
                key="enable_draw_prompt_optimization",
                value=False,
                help="是否启用AI绘图描述优化。开启后会使用辅助LLM润色用户描述以生成更佳效果，会额外消耗API额度。",
            ),
            RegisterConfig(
                module="ai_creation",
                key="default_draw_engine",
                value="doubao",
                help=(
                    "默认使用的AI绘图引擎。可选值: 'doubao' (Playwright模拟), "
                    "'api' (LLM原生API)。"
                ),
            ),
            RegisterConfig(
                module="ai_creation",
                key="enable_api_draw_engine",
                value=True,
                help="是否启用API绘图引擎（如Gemini等）。关闭后，`--engine api` 参数将不可用。",
            ),
            RegisterConfig(
                module="ai_creation",
                key="api_draw_model",
                value="Gemini/gemini-2.5-flash-image-preview",
                help="使用API绘图时调用的模型名称，格式：提供商/模型名",
            ),
            RegisterConfig(
                module="ai_creation",
                key="auxiliary_llm_model",
                value="Gemini/gemini-2.5-flash",
                help="辅助LLM模型名称，用于绘图描述优化，格式：提供商/模型名",
            ),
            RegisterConfig(
                module="ai_creation",
                key="DOUBAO_COOKIES",
                value=[],
                help="豆包AI绘图的Cookies列表，用于免登录生成图片。支持多个Cookie轮询。",
            ),
            RegisterConfig(
                module="ai_creation",
                key="ENABLE_DOUBAO_COOKIES",
                value=True,
                help="是否启用豆包Cookies进行绘图，关闭后将以游客模式尝试（可能失败）。",
            ),
            RegisterConfig(
                module="ai_creation",
                key="HEADLESS_BROWSER",
                value=True,
                help="是否使用无头浏览器模式进行AI绘图。True为后台运行（服务器推荐），False会弹出浏览器窗口（便于本地调试）。",
            ),
            RegisterConfig(
                module="ai_creation",
                key="DOUBAO_AUTO_SOLVE_CAPTCHA",
                value=True,
                help="是否在豆包绘图时启用AI自动解决拖拽验证码。关闭后，遇到验证码将导致绘图失败。",
            ),
            RegisterConfig(
                module="ai_creation",
                key="browser_cooldown_seconds",
                value=15,
                help="浏览器关闭后的冷却时间（秒），冷却期间不接受新绘图请求",
            ),
            RegisterConfig(
                module="ai_creation",
                key="doubao_wait_signal_timeout",
                value=120,
                help="豆包引擎等待图片生成完成信号的超时时间（秒）",
            ),
            RegisterConfig(
                module="ai_creation",
                key="draw_cd",
                value=120,
                help="AI绘图功能的冷却时间（秒）",
            ),
            RegisterConfig(
                module="ai_creation",
                key="browser_idle_timeout_minutes",
                value=0,
                help="浏览器在没有绘图任务时，闲置多少分钟后自动关闭。设置为 0 则禁用此功能。",
            ),
        ],
    ).dict(),
)

draw_alconna = Alconna(
    "draw",
    Option(
        "--engine|-e",
        Args["engine_name", str],
        help_text="临时指定绘图引擎 (doubao/api)",
    ),
    Option(
        "--optimize|-o", Args["mode", str], help_text="开启或关闭提示词优化 (on/off)"
    ),
    Option(
        "--template|-t",
        Args["template_name", str],
        help_text="使用一个预设的风格模板",
    ),
    Option(
        "--size|-s",
        Args["img_size", str],
        help_text="指定生成分辨率 (部分模型支持: 1K, 2K, 4K)",
    ),
    Args[
        "prompt?",
        MultiVar(str | UniImage),
        Field(default=[], completion=lambda: "输入图片描述..."),
    ],
    meta=CommandMeta(
        description="AI图片生成",
        usage="draw <描述>\ndraw <描述> [图片] - 基于图片进行风格转换",
        example="""
            draw 一只可爱的小猫
            draw --engine api 未来都市
            draw -o on [附带图片] 换成赛博朋克风格
            draw -t 手办 @用户
        """,
        strict=False,
    ),
)


draw_cmd = on_alconna(
    draw_alconna,
    rule=is_type(GroupMessageEvent, MessageEvent),
    priority=5,
    block=True,
)

dtemplate_alc = Alconna(
    "绘图模板",
    Subcommand(
        "list",
        alias=["ls"],
        help_text="列出所有可用的绘图模板",
    ),
    meta=CommandMeta(
        description="AI绘图模板管理",
        usage=("绘图模板 list"),
    ),
)

dtemplate_superuser_alc = Alconna(
    "绘图模板",
    Subcommand(
        "create",
        Args["prompt?", MultiVar(str | UniImage)],
        alias=["创建"],
        help_text="基于图片和文本创建新模板",
    ),
    Subcommand(
        "info",
        Args["name", str],
        help_text="查看一个模板的详细内容",
    ),
    Subcommand(
        "optimize",
        Args["name", str]["instruction", AllParam, ""],
        alias=["优化"],
        help_text="优化一个已有的模板",
    ),
    Subcommand(
        "add",
        Args["name", str]["prompt", AllParam],
        help_text="添加一个新的绘图模板",
    ),
    Subcommand(
        "del",
        Args["names", MultiVar(str)],
        help_text="删除一个或多个绘图模板",
    ),
    Subcommand(
        "edit",
        Args["name", str]["prompt", AllParam],
        help_text="修改一个现有的绘图模板",
    ),
    Subcommand(
        "reload",
        alias=["重载"],
        help_text="从文件重新加载所有模板",
    ),
    Subcommand(
        "clear",
        help_text="清空所有绘图模板",
    ),
    meta=CommandMeta(
        description="AI绘图模板管理 (超级用户)",
        usage=(
            "绘图模板 create [图片] [描述] -> 进入创建会话\n"
            "绘图模板 info <模板名称>\n"
            "绘图模板 optimize <名称> [优化指令] -> 进入优化会话\n"
            "绘图模板 add <名称> <提示词>\n"
            "绘图模板 del <名称1> [名称2] ...\n"
            "绘图模板 edit <名称> <新提示词>\n"
            "绘图模板 reload\n"
            "绘图模板 clear"
        ),
    ),
)

dtemplate_public_cmd = on_alconna(
    dtemplate_alc,
    aliases={"preset"},
    priority=5,
    block=True,
)

dtemplate_superuser_cmd = on_alconna(
    dtemplate_superuser_alc,
    aliases={"preset"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
driver = get_driver()


@driver.on_startup
async def _():
    logger.debug("AI Draw Plugin: 正在初始化...")
    from . import templates
    from .engines.doubao.cookie_manager import cookie_manager
    from .engines.doubao.queue_manager import draw_queue_manager

    try:
        cooldown = base_config.get("browser_cooldown_seconds")
        draw_queue_manager.set_browser_cooldown(cooldown)
        await cookie_manager.load_and_sync_cookies()
        draw_queue_manager.start_idle_monitor()
        draw_queue_manager.start_queue_processor()
        await templates.template_manager.initialize()
        logger.debug(f"AI Draw 插件核心服务已启动, 浏览器冷却时间: {cooldown}s")
    except Exception as e:
        logger.error(f"AI Draw 插件初始化失败: {e}")


@driver.on_shutdown
async def ai_draw_shutdown():
    logger.debug("AI Draw Plugin: 正在关闭...")
    from .engines.doubao.queue_manager import draw_queue_manager

    await draw_queue_manager.stop_idle_monitor()
    await draw_queue_manager.stop_queue_processor()
    await draw_queue_manager.shutdown_browser()


from . import handlers  # noqa: E402, F401
