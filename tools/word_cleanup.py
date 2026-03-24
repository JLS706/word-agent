# -*- coding: utf-8 -*-
"""
Tool: 关闭残留 Word 进程
解决每次工具执行后 Word 进程残留的问题。
"""

import subprocess
import re
from tools.base import Tool


class CloseWordTool(Tool):
    """关闭所有残留的 Microsoft Word 进程。"""

    name = "close_word"
    description = (
        "关闭所有残留的 Microsoft Word (WINWORD.EXE) 进程。"
        "在完成 Word 文档操作后调用，防止 Word 进程残留占用资源。\n"
        "注意：这会关闭所有 Word 进程，包括用户手动打开的文档。"
        "如果用户正在编辑文档，请先提醒用户保存。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "force": {
                "type": "boolean",
                "description": "是否强制关闭（/F 参数）。默认 True。",
            },
        },
    }

    def execute(self, force: bool = True, **kwargs) -> str:
        # 先检查是否有 Word 进程
        try:
            check = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE"],
                capture_output=True, text=True, timeout=10,
            )
            if "WINWORD.EXE" not in check.stdout:
                return "✅ 当前没有运行中的 Word 进程，无需清理。"

            # 统计进程数量
            count = check.stdout.count("WINWORD.EXE")
        except Exception as e:
            return f"❌ 检查 Word 进程失败: {e}"

        # 执行关闭
        try:
            cmd = ["taskkill", "/IM", "WINWORD.EXE"]
            if force:
                cmd.append("/F")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )

            if result.returncode == 0:
                return f"✅ 已成功关闭 {count} 个 Word 进程。"
            else:
                # returncode != 0 但可能部分成功
                error_msg = result.stderr.strip() or result.stdout.strip()
                return f"⚠️ 关闭 Word 进程时出现问题: {error_msg}"

        except subprocess.TimeoutExpired:
            return "❌ 关闭 Word 进程超时，进程可能被卡住。请尝试手动在任务管理器中结束。"
        except Exception as e:
            return f"❌ 关闭 Word 进程失败: {e}"
