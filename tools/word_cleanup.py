# -*- coding: utf-8 -*-
"""
Tool: 关闭残留 Word 进程

策略优先级：
  1. 精准狙击 —— 仅击杀 COMSafeLock 注册的 Agent 托管 PID
  2. 灾难保底 —— 当 force=True 且精准狙击后仍有残留时，降级为全局 taskkill
"""

import subprocess
from tools.base import Tool


class CloseWordTool(Tool):
    """关闭 Agent 托管的 Word 进程（精准 PID 狙击）。"""

    name = "close_word"
    description = (
        "关闭 Agent 托管的 Word (WINWORD.EXE) 进程。\n"
        "默认仅精准关闭由 Agent 内部 COMSafeLock 启动的 Word 实例（PID 级别），"
        "不会影响用户自己打开的 Word 文档。\n"
        "仅当设置 force=true 且精准清理后仍有残留时，才会降级为全局强杀。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "force": {
                "type": "boolean",
                "description": "是否在精准狙击后仍有残留时降级为全局强杀。默认 True。",
            },
        },
    }

    def execute(self, force: bool = True, **kwargs) -> str:
        from core.com_watchdog import COMSafeLock

        report_lines = []

        # ── 第一优先级：精准 PID 狙击 ──
        active_pids = COMSafeLock.get_active_pids()
        if active_pids:
            killed = COMSafeLock.kill_pids(active_pids)
            if killed:
                report_lines.append(
                    f"✅ 精准关闭 {len(killed)} 个 Agent 托管的 Word 进程 (PID: {killed})"
                )
            else:
                report_lines.append("⚠️ Agent PID 已注册但击杀失败，进程可能已退出。")
        else:
            report_lines.append("ℹ️ 当前没有 Agent 托管的 Word PID。")

        # ── 第二优先级：检查是否仍有残留 Word 进程 ──
        try:
            check = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE"],
                capture_output=True, text=True, timeout=10,
            )
            remaining = check.stdout.count("WINWORD.EXE")
        except Exception:
            remaining = 0

        if remaining == 0:
            report_lines.append("✅ 系统中已无残留 Word 进程。")
            return "\n".join(report_lines)

        # ── 灾难保底：全局 taskkill（仅 force=True 时触发）──
        if not force:
            report_lines.append(
                f"⚠️ 仍有 {remaining} 个非 Agent Word 进程运行中。"
                " 这些可能是用户自己打开的文档，未执行全局强杀。"
                " 如需清理，请设置 force=true。"
            )
            return "\n".join(report_lines)

        try:
            result = subprocess.run(
                ["taskkill", "/IM", "WINWORD.EXE", "/F"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                report_lines.append(
                    f"⚠️ 已降级为全局强杀，关闭了 {remaining} 个残留 Word 进程。"
                    " 注意：这可能影响了用户正在编辑的文档。"
                )
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                report_lines.append(f"❌ 全局强杀失败: {error_msg}")
        except subprocess.TimeoutExpired:
            report_lines.append("❌ 全局强杀超时，请手动在任务管理器中结束。")
        except Exception as e:
            report_lines.append(f"❌ 全局强杀异常: {e}")

        return "\n".join(report_lines)
