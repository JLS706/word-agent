# -*- coding: utf-8 -*-
"""
Tool: Agent 动态工具创建系统（沙盒安全版）

安全模型（三层防护 + 人工审批）：
  Layer 1: AST 安全检查（静态分析，core/sandbox.py）
  Layer 2: 沙盒试执行（进程隔离 + 超时强杀，core/sandbox.py）
  Layer 3: 用户审批（人工审查代码后才允许注册到主环境）

流程：
  Agent 写代码 → AST 检查 → 沙盒试执行 → 保存为 .draft
                                              ↓
                              用户审查 → approve_tool → 注册到主环境
"""

import os
import re
import ast
import json
import importlib
import importlib.util
from datetime import datetime

from tools.base import Tool, ToolRegistry
from core.sandbox import check_code_safety, test_tool_sandboxed

# 自定义工具目录
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom")


# ═════════════════════════════════════════════
# 代码生成
# ═════════════════════════════════════════════

def _generate_tool_code(
    tool_name: str,
    description: str,
    parameters: dict,
    code_body: str,
) -> str:
    """根据参数生成完整的工具 .py 文件内容"""
    params_str = json.dumps(parameters, ensure_ascii=False, indent=8)

    return f'''# -*- coding: utf-8 -*-
"""
自定义工具: {tool_name}
由 Agent 动态创建，经用户审批后激活。
创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}
"""

from tools.base import Tool


class CustomTool(Tool):
    """自定义工具: {tool_name}"""

    name = "{tool_name}"
    description = """{description}"""
    parameters = {params_str}

    def execute(self, **kwargs) -> str:
{_indent_code(code_body, 8)}
'''


def _indent_code(code: str, spaces: int) -> str:
    """为代码块添加缩进"""
    prefix = " " * spaces
    lines = code.strip().split("\n")
    return "\n".join(prefix + line for line in lines)


# ═════════════════════════════════════════════
# Tool 1: 创建工具（AST检查 + 沙盒试执行 + 保存草稿）
# ═════════════════════════════════════════════

class CreateToolTool(Tool):
    """让 Agent 生成工具代码，经安全检查和沙盒试执行后保存为草稿。"""

    name = "create_tool"
    description = (
        "创建一个新工具。你可以编写工具的功能代码，系统会进行：\n"
        "  1. AST 安全检查（静态分析）\n"
        "  2. 沙盒试执行（在隔离进程中测试代码能否正常运行）\n"
        "通过后保存为草稿，等待用户审批（approve_tool）后正式激活。\n\n"
        "使用场景：\n"
        "- 用户需要一个当前工具中没有的新功能\n"
        "- 你发现需要一个重复使用的自动化操作\n\n"
        "code_body 是工具 execute 方法的函数体，可以使用 kwargs 获取参数。\n"
        "允许使用的模块：os, subprocess, re, json, datetime, pathlib, shutil 等。\n"
        "禁止使用：网络模块、数据库、exec/eval、多线程等。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "工具名称（英文，下划线命名，如 'kill_word_process'）",
            },
            "description": {
                "type": "string",
                "description": "工具功能描述（中文即可，会显示给 Agent 自己看）",
            },
            "parameters": {
                "type": "object",
                "description": "JSON Schema 格式的参数定义",
            },
            "code_body": {
                "type": "string",
                "description": (
                    "execute 方法的函数体（Python 代码）。"
                    "通过 kwargs 获取参数，必须返回 str。"
                    "例：'name = kwargs.get(\"name\", \"World\")\\nreturn f\"Hello, {name}!\"'"
                ),
            },
        },
        "required": ["tool_name", "description", "parameters", "code_body"],
    }

    def execute(self, tool_name: str, description: str,
                parameters: dict, code_body: str, **kwargs) -> str:
        # 校验工具名
        if not re.match(r'^[a-z][a-z0-9_]*$', tool_name):
            return (
                "❌ 工具名称格式错误，必须是小写字母+下划线，"
                "如 'kill_word_process'，不能包含空格或特殊字符。"
            )

        # 生成完整代码
        full_code = _generate_tool_code(tool_name, description, parameters, code_body)

        # ── Layer 1: AST 安全检查（调用 core/sandbox.py）──
        safety_error = check_code_safety(full_code, mode="tool")
        if safety_error:
            return f"❌ Layer 1 (AST安全检查) 未通过：\n{safety_error}"

        # ── Layer 2: 沙盒试执行（调用 core/sandbox.py）──
        sandbox_result = test_tool_sandboxed(full_code)

        if not sandbox_result.get("success"):
            error = sandbox_result.get("error", "未知错误")
            stdout = sandbox_result.get("stdout", "")
            detail = f"❌ Layer 2 (沙盒试执行) 未通过：\n{error}"
            if stdout:
                detail += f"\n\n标准输出:\n{stdout[:500]}"
            return detail

        # ── 通过所有检查，保存为草稿 ──
        os.makedirs(_TOOLS_DIR, exist_ok=True)
        draft_path = os.path.join(_TOOLS_DIR, f"{tool_name}.py.draft")
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(full_code)

        # 组装结果
        test_output = sandbox_result.get("test_output", "")
        stdout = sandbox_result.get("stdout", "")

        result_parts = [
            f"✅ 工具 '{tool_name}' 已通过全部安全检查！\n",
            f"🔒 Layer 1 (AST安全检查): 通过",
            f"🧪 Layer 2 (沙盒试执行): 通过",
        ]

        if test_output:
            result_parts.append(f"   试执行结果: {test_output[:200]}")
        if stdout:
            result_parts.append(f"   标准输出: {stdout[:200]}")

        result_parts.extend([
            f"\n📄 草稿已保存: {draft_path}\n",
            f"📋 生成的代码：",
            f"```python\n{full_code}\n```\n",
            f"⏳ 等待 Layer 3 (用户审批)。",
            f"请用户确认代码无误后调用 approve_tool(tool_name='{tool_name}') 激活。",
        ])

        return "\n".join(result_parts)


# ═════════════════════════════════════════════
# Tool 2: 审批工具（Layer 3: 用户确认后激活）
# ═════════════════════════════════════════════

class ApproveToolTool(Tool):
    """用户审批后激活自定义工具（Layer 3: 人工审查）。"""

    name = "approve_tool"
    description = (
        "激活一个由 create_tool 创建的工具草稿。"
        "只有经过用户审批确认后才应调用此工具。"
        "激活后工具会立即注册可用，重启后也会自动加载。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "要激活的工具名称",
            },
        },
        "required": ["tool_name"],
    }

    def __init__(self, registry: ToolRegistry = None):
        self._registry = registry

    def execute(self, tool_name: str, **kwargs) -> str:
        draft_path = os.path.join(_TOOLS_DIR, f"{tool_name}.py.draft")
        final_path = os.path.join(_TOOLS_DIR, f"{tool_name}.py")

        if not os.path.exists(draft_path):
            if os.path.exists(final_path):
                return f"ℹ️ 工具 '{tool_name}' 已经是激活状态。"
            return f"❌ 未找到工具草稿 '{tool_name}'。请先用 create_tool 创建。"

        try:
            os.rename(draft_path, final_path)
        except Exception as e:
            return f"❌ 激活失败: {e}"

        if self._registry:
            try:
                tool = _load_tool_from_file(final_path)
                if tool:
                    self._registry.register(tool)
                    return (
                        f"✅ 工具 '{tool_name}' 已激活并注册！\n"
                        f"📁 文件: {final_path}\n"
                        f"现在可以直接使用了，下次启动也会自动加载。"
                    )
                else:
                    return f"⚠️ 文件已激活但加载失败，请检查代码。文件: {final_path}"
            except Exception as e:
                return f"⚠️ 文件已激活但注册失败: {e}"

        return (
            f"✅ 工具 '{tool_name}' 已激活！\n"
            f"📁 文件: {final_path}\n"
            f"⚠️ 无法动态注册（缺少 registry），重启 Agent 后生效。"
        )


# ═════════════════════════════════════════════
# Tool 3: 列出自定义工具
# ═════════════════════════════════════════════

class ListCustomToolsTool(Tool):
    """列出所有自定义工具及其状态。"""

    name = "list_custom_tools"
    description = (
        "列出所有由 create_tool 创建的自定义工具，"
        "包括草稿（待审批）和已激活的工具。"
    )
    parameters = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs) -> str:
        if not os.path.exists(_TOOLS_DIR):
            return "📭 还没有创建任何自定义工具。"

        drafts = []
        active = []

        for f in sorted(os.listdir(_TOOLS_DIR)):
            if f == "__init__.py" or f == "__pycache__":
                continue
            if f.endswith(".py.draft"):
                name = f.replace(".py.draft", "")
                drafts.append(name)
            elif f.endswith(".py"):
                name = f.replace(".py", "")
                active.append(name)

        if not drafts and not active:
            return "📭 还没有创建任何自定义工具。"

        lines = ["📦 自定义工具列表：\n"]
        for name in active:
            lines.append(f"  ✅ {name} （已激活）")
        for name in drafts:
            lines.append(f"  ⏳ {name} （草稿，待审批）")

        lines.append(f"\n共 {len(active)} 个已激活，{len(drafts)} 个待审批。")
        return "\n".join(lines)


# ═════════════════════════════════════════════
# 工具加载器（供 main.py 启动时使用）
# ═════════════════════════════════════════════

def _load_tool_from_file(file_path: str) -> Tool | None:
    """从 .py 文件动态加载 CustomTool 类"""
    try:
        module_name = os.path.splitext(os.path.basename(file_path))[0]
        spec = importlib.util.spec_from_file_location(
            f"tools.custom.{module_name}", file_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "CustomTool"):
            return module.CustomTool()

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, Tool)
                    and attr is not Tool and attr_name != "Tool"):
                return attr()

        return None
    except Exception as e:
        print(f"  ⚠️ 加载自定义工具 {file_path} 失败: {e}")
        return None


def load_custom_tools(registry: ToolRegistry) -> int:
    """扫描 tools/custom/ 加载所有已激活的自定义工具。"""
    if not os.path.exists(_TOOLS_DIR):
        return 0

    count = 0
    for filename in sorted(os.listdir(_TOOLS_DIR)):
        if filename.endswith(".py") and filename != "__init__.py":
            file_path = os.path.join(_TOOLS_DIR, filename)
            tool = _load_tool_from_file(file_path)
            if tool:
                registry.register(tool)
                count += 1

    return count
