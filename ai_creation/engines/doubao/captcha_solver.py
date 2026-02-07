import asyncio
import random

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

from zhenxun.services.llm import create_multimodal_message, generate_structured
from zhenxun.services.log import logger

from ...config import base_config
from .exceptions import ImageGenerationError
from .generator import HumanActionUtils # 引用新添加的工具类


class CaptchaSolution(BaseModel):
    """用于结构化LLM输出的验证码解决方案模型"""

    success: bool = Field(..., description="是否成功识别验证码内容")
    indices: list[int] = Field(..., description="需要拖动的图片的序号列表（从1开始）")


CAPTCHA_SYSTEM_PROMPT = """
你是顶级的视觉分析专家，专门解决"看图拖拽"人机验证码。
你的任务是：根据用户提供的验证码截图和文字提示，找出所有符合描述的图片，并返回它们的序号。

【工作流程】
1.  **分析图片与问题**：仔细阅读图片中的文字提示（例如："请选择所有用于身体锻炼的物品"），并观察下方九宫格中的所有图片。
2.  **识别目标**：根据文字提示，识别出九宫格中所有匹配的图片。
3.  **确定序号**：图片的序号遵循从左到右、从上到下的顺序，编号为 1 到 9。
4.  **格式化输出**：将所有匹配图片的序号以JSON格式返回。

【输出格式】
严格按照以下JSON格式返回，不要包含任何额外的解释或代码块标记。

{
    "success": true,
    "indices": [序号1, 序号2, ...]
}

【示例】
- **输入**：[一张包含9张图片的截图，其中第1、5、8张是猫] + 文字提示："请选择所有的猫"
- **输出**：
{
    "success": true,
    "indices": [1, 5, 8]
}
"""


async def solve_drag_captcha_if_present(page: Page) -> bool:
    """
    检查页面是否存在验证码，如果存在，则循环尝试解决。
    """
    if not page:
        return False

    try:
        captcha_container = page.locator("#captcha_container")
        await captcha_container.wait_for(state="visible", timeout=5000)

        logger.info("检测到验证码，启动处理程序...")
        solved = False
        for i in range(3):
            if await _solve_drag_captcha_attempt(page):
                solved = True
                logger.debug("验证码解决后，等待图片生成流程继续...")
                await asyncio.sleep(5)
                break
            else:
                logger.warning(f"验证码处理失败，第 {i+1} 次尝试...")
                await asyncio.sleep(2)

        if not solved:
            raise ImageGenerationError("多次尝试解决验证码失败。")

        return True

    except PlaywrightTimeoutError:
        logger.info("未检测到验证码弹窗，流程继续。")
        return False


async def _solve_drag_captcha_attempt(page: Page) -> bool:
    """
    [内部方法] 执行单次解决拖拽验证码的尝试。
    假定被调用时验证码弹窗已经可见。
    """
    if not page:
        return False
    try:
        logger.debug("等待验证码提示框出现")
        captcha_container = page.locator("#captcha_container")
        captcha_frame = page.frame_locator("#captcha_container iframe")

        try:
            prompt_element = captcha_frame.locator(".captcha-prompt-bar .tit")
            await prompt_element.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeoutError:
            logger.error("   - 未能在验证码弹窗中找到提示文本栏。")
            return False

        captcha_prompt = await prompt_element.text_content() or ""
        captcha_prompt = " ".join(captcha_prompt.split())

        captcha_box = captcha_frame.locator("#vc_captcha_box")
        screenshot_bytes = await captcha_box.screenshot()

        logger.debug(f"   - 验证码提示: '{captcha_prompt}'")
        logger.debug(f"   - 已截取验证码区域图片 ({len(screenshot_bytes)} bytes)")

        message = create_multimodal_message(
            text=f"问题是：'{captcha_prompt}'", images=[screenshot_bytes]
        )
        logger.debug("   - 正在调用Vision LLM分析验证码...")
        solution = await generate_structured(
            message,
            response_model=CaptchaSolution,
            model=base_config.get("auxiliary_llm_model"),
            instruction=CAPTCHA_SYSTEM_PROMPT,
        )

        if not solution or not solution.success or not solution.indices:
            logger.warning("   - LLM未能解析验证码或未返回有效序号。")
            return False

        logger.debug(f"   - LLM识别结果：需要拖动图片序号 {solution.indices}")

        image_elements = captcha_frame.locator(
            "#captcha_verify_image > div.img-container .canvas-container"
        )
        drag_area = captcha_frame.locator("#captcha_verify_image > div.drag-area")
        if await image_elements.count() == 0:
            logger.error("   - 无法在页面上定位到验证码图片元素。")
            return False

        for index in solution.indices:
            if 1 <= index <= await image_elements.count():
                source_element = image_elements.nth(index - 1)
                source_box = await source_element.bounding_box()
                target_box = await drag_area.bounding_box()

                if source_box and target_box:
                    logger.debug(f"   - 正在模拟拖动第 {index} 张图片...")
                    
                    # 计算起点和终点中心
                    start_x = source_box["x"] + source_box["width"] / 2
                    start_y = source_box["y"] + source_box["height"] / 2
                    end_x = target_box["x"] + target_box["width"] / 2
                    end_y = target_box["y"] + target_box["height"] / 2

                    # 1. 移动到源图片
                    await page.mouse.move(
                        start_x + random.uniform(-5, 5),
                        start_y + random.uniform(-5, 5),
                        steps=random.randint(10, 20)
                    )
                    await page.mouse.down()
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                    
                    # 2. 拖拽到目标区域（增加 steps 以模拟移动过程，而不是瞬移）
                    await page.mouse.move(
                        end_x + random.uniform(-10, 10), # 终点增加随机抖动
                        end_y + random.uniform(-10, 10),
                        steps=random.randint(30, 60),    # 拖拽过程慢一点
                    )
                    await page.mouse.up()
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                else:
                    logger.warning(
                        f"   - 无法获取第 {index} 张图片或拖动区域的边界框，跳过。"
                    )
            else:
                logger.warning(f"   - LLM返回了无效的图片序号: {index}，已跳过。")

        submit_button = captcha_frame.locator(".vc-captcha-verify-pc-button")
        await submit_button.click()
        logger.debug("   - 已点击提交按钮，等待验证码弹窗消失...")

        await captcha_container.wait_for(state="hidden", timeout=10000)
        logger.info("✅ 验证码成功解决！")
        return True

    except PlaywrightTimeoutError:
        logger.warning("   - 等待验证码元素超时或验证后弹窗未消失，可能失败。")
        try:
            refresh_button = captcha_frame.locator(".vc-captcha-refresh")
            if await refresh_button.is_visible():
                await refresh_button.click()
                logger.debug("   - 已尝试点击刷新按钮。")
        except Exception as e:
            logger.warning(f"   - 刷新验证码失败: {e}")
        return False
    except Exception as e:
        logger.error("❌ 解决验证码时发生意外错误", e=e)
        return False
