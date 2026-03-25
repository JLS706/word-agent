# -*- coding: utf-8 -*-
"""
DocMaster Agent - 统一日志系统

替代全局 print，提供：
  - 日志级别（DEBUG / INFO / WARNING / ERROR）
  - 彩色 emoji 前缀（终端友好）
  - 可选文件输出（调试回溯）
  - 通过 verbose 参数控制是否输出 DEBUG 级别

用法：
    from core.logger import logger
    logger.info("🧠 Agent 收到指令: %s", user_input)
    logger.warning("⚠️ Docker 沙盒不可用，回退本地")
    logger.error("❌ LLM 调用失败: %s", e)
    logger.debug("  工具参数: %s", args)  # 仅 verbose=True 时显示
"""

import os
import sys
import logging

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_logger(
    name: str = "docmaster",
    verbose: bool = True,
    log_file: str = "",
) -> logging.Logger:
    """
    配置并返回 Logger 实例。

    Args:
        name: logger 名称
        verbose: True=DEBUG级别, False=INFO级别
        log_file: 可选，日志文件路径（追加模式）
    """
    log = logging.getLogger(name)

    # 避免重复添加 handler
    if log.handlers:
        return log

    level = logging.DEBUG if verbose else logging.INFO
    log.setLevel(level)

    # ── 终端输出 ──
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    # 简洁格式：不打印时间戳和模块名（保持和原 print 一样的阅读体验）
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    # ── 文件输出（可选）──
    if log_file:
        log_path = os.path.join(_PROJECT_ROOT, log_file)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        log.addHandler(file_handler)

    return log


# 全局 logger 单例（默认 verbose=True，main.py 中可重新配置）
logger = setup_logger()
