class ImageGenerationError(Exception):
    """图片生成错误"""

    pass


class CookieInvalidError(ImageGenerationError):
    """Cookie失效（检测到未登录状态）"""

    pass
