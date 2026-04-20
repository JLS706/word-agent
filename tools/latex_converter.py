# -*- coding: utf-8 -*-
"""
Tool: LaTeX → MathType 批量转换
将Word文档中的 $...$ 和 $$...$$ LaTeX公式转换为 MathType OLE 对象。
"""

import importlib.util
import os
import sys

from tools.base import Tool


# MathType 单次等待窗口最长可达 10 秒（activate_mathtype_window），
# 所以申请 30 秒租约让看门狗放宽阈值，避免单公式阻塞触发熔断。
_WATCHDOG_LEASE_SEC = 30.0


class LatexConverterTool(Tool):
    name = "convert_latex_to_mathtype"
    description = (
        "将Word文档中的LaTeX公式（$...$行内公式和$$...$$块公式）批量转换为MathType公式对象。"
        "需要电脑上已安装MathType软件。转换过程会逐个处理文档中的公式，"
        "通过自动化操控MathType完成LaTeX到OLE对象的转换。"
        "⚠️ 此工具执行时间较长，且需要MathType窗口处于可用状态。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Word文档的完整文件路径",
            },
            "overwrite": {
                "type": "boolean",
                "description": "是否覆盖原文件（true=覆盖并自动备份，false=另存为_converted.docx）。默认false。",
            },
            "exclude": {
                "type": "string",
                "description": (
                    "要排除（不转换）的公式编号，逗号分隔或区间语法，"
                    "如 '1,3,5' 或 '2-8' 或 '1,3-5,8'。留空则全部转换。"
                ),
            },
        },
        "required": ["file_path"],
    }

    # ─── 内部：解析 exclude 字符串为编号集合 ───
    @staticmethod
    def _parse_exclude(exclude: str) -> set[int]:
        if not exclude:
            return set()
        out: set[int] = set()
        for part in exclude.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    out.update(range(int(a), int(b) + 1))
                except ValueError:
                    continue
            else:
                try:
                    out.add(int(part))
                except ValueError:
                    continue
        return out

    def execute(self, file_path: str, overwrite: bool = False,
                exclude: str = "") -> str:
        agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        latex_script = os.path.join(agent_dir, "latex.py")

        if not os.path.exists(latex_script):
            return "❌ 未找到 latex.py 脚本文件"

        if not os.path.exists(file_path):
            return f"❌ 文件不存在: {file_path}"

        # ── 动态加载 latex.py 模块（不依赖 sys.path）──
        spec = importlib.util.spec_from_file_location("latex_script", latex_script)
        if spec is None or spec.loader is None:
            return "❌ 无法加载 latex.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # ── 构造进度桥接：latex.py 的 (current, total, stage) → report_progress ──
        #    首次心跳附带 temp_timeout 租约，让看门狗放宽阈值
        lease_sent = [False]

        stage_msgs = {
            "scan": "扫描文档中的公式...",
            "scan_done": "公式扫描完成",
            "find": "定位下一个公式",
            "ole_insert": "插入 OLE 对象",
            "mathtype_open": "等待 MathType 窗口",
            "mathtype_close": "等待 MathType 关闭",
            "converted": "已完成公式",
            "excluded": "已排除公式",
            "skipped": "跳过失败公式",
            "save": "定期保存",
            "done": "全部转换完成",
        }

        def _on_progress(current: int, total: int, stage: str):
            # 基础心跳 metadata
            meta: dict = {"stage": stage, "current": current, "total": total}
            # 首次心跳申请长租约（覆盖单公式阻塞最坏情况）
            if not lease_sent[0]:
                meta["temp_timeout"] = _WATCHDOG_LEASE_SEC
                lease_sent[0] = True

            if total > 0:
                pct = min(95, 5 + int(90 * current / total))
                msg = f"[{current}/{total}] {stage_msgs.get(stage, stage)}"
            else:
                pct = 5
                msg = stage_msgs.get(stage, stage)
            self.report_progress(pct, msg, metadata=meta)

        # ── 调用 latex.main()（通过 sys.argv 传文件路径 + 模式标志）──
        excluded_set = self._parse_exclude(exclude)
        original_argv = sys.argv
        try:
            sys.argv = ["latex.py", file_path,
                        "--overwrite" if overwrite else "--safe"]
            self.report_progress(
                2, "启动 LaTeX→MathType 转换...",
                metadata={"temp_timeout": _WATCHDOG_LEASE_SEC},
            )
            mod.main(progress_callback=_on_progress,
                     excluded_indices=excluded_set)
        finally:
            sys.argv = original_argv

        self.report_progress(100, "完成")
        excl_note = f"（排除 {len(excluded_set)} 个）" if excluded_set else ""
        return (
            f"✅ LaTeX→MathType 转换完成{excl_note}。"
            f"文件: {os.path.basename(file_path)}"
        )
