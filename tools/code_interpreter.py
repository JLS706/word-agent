# -*- coding: utf-8 -*-
"""
Tool: 安全沙盒 Python 代码解释器
让 Agent 具备"临时写代码并执行"的能力，用于分析、计算等只读任务。

安全机制（三重防护）：
  1. AST 静态分析 — 在执行前扫描代码，拦截危险 import 和操作
  2. builtins 白名单 — 运行时移除 exec/eval/__import__ 等危险内置函数
  3. 超时保护 — 5 秒超时，防止死循环或资源耗尽
"""

import ast
import io
import sys
import multiprocessing
import traceback
from typing import Optional

from tools.base import Tool


# ─────────────────────────────────────────────
# 安全策略配置
# ─────────────────────────────────────────────

# 允许使用的模块（白名单）
ALLOWED_MODULES = {
    # 文本处理
    "re", "string", "textwrap", "difflib",
    # 数学与统计
    "math", "statistics", "decimal", "fractions", "random",
    # 数据结构
    "collections", "itertools", "functools", "operator",
    # 日期时间
    "datetime", "time",
    # 序列化（只读分析用）
    "json", "csv",
    # 类型
    "typing", "enum",
    # 其他安全模块
    "copy", "pprint", "hashlib", "unicodedata",
}

# 明确禁止的模块（黑名单，用于给出更好的错误提示）
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "shutil", "pathlib",
    "socket", "http", "urllib", "requests", "ftplib",
    "sqlite3", "pickle", "shelve", "marshal",
    "ctypes", "importlib", "runpy", "code", "codeop",
    "signal", "multiprocessing", "threading",
    "win32com", "win32api", "win32con",
    "builtins", "__builtin__",
}

# 执行超时（秒）
EXECUTION_TIMEOUT = 5

# 输出最大长度（字符）
MAX_OUTPUT_LENGTH = 2000


# ─────────────────────────────────────────────
# AST 安全检查器（第 1 层防护）
# ─────────────────────────────────────────────

class SafetyChecker(ast.NodeVisitor):
    """
    遍历 AST 树，检测并拦截危险操作。
    """

    def __init__(self):
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_root = alias.name.split(".")[0]
            if module_root in BLOCKED_MODULES:
                self.errors.append(
                    f"🚫 禁止导入模块 '{alias.name}'（安全限制）"
                )
            elif module_root not in ALLOWED_MODULES:
                self.errors.append(
                    f"🚫 模块 '{alias.name}' 不在白名单中。"
                    f"允许的模块: {', '.join(sorted(ALLOWED_MODULES))}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module_root = node.module.split(".")[0]
            if module_root in BLOCKED_MODULES:
                self.errors.append(
                    f"🚫 禁止从 '{node.module}' 导入（安全限制）"
                )
            elif module_root not in ALLOWED_MODULES:
                self.errors.append(
                    f"🚫 模块 '{node.module}' 不在白名单中。"
                    f"允许的模块: {', '.join(sorted(ALLOWED_MODULES))}"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # 检查调用 exec / eval / __import__ / compile
        if isinstance(node.func, ast.Name):
            if node.func.id in ("exec", "eval", "__import__", "compile"):
                self.errors.append(
                    f"🚫 禁止调用 '{node.func.id}()'（安全限制）"
                )
        # 检查 os.system / subprocess.run 等
        if isinstance(node.func, ast.Attribute):
            dangerous_attrs = {
                "system", "popen", "remove", "unlink", "rmdir",
                "rename", "makedirs", "mkdir", "write", "writelines",
            }
            if node.func.attr in dangerous_attrs:
                self.errors.append(
                    f"🚫 禁止调用 '.{node.func.attr}()'（安全限制）"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # 拦截 __subclasses__, __bases__ 等元编程属性
        if node.attr.startswith("__") and node.attr.endswith("__"):
            safe_dunders = {
                "__init__", "__str__", "__repr__", "__len__",
                "__getitem__", "__setitem__", "__contains__",
                "__iter__", "__next__", "__enter__", "__exit__",
                "__add__", "__sub__", "__mul__", "__truediv__",
                "__eq__", "__lt__", "__gt__", "__le__", "__ge__",
                "__hash__", "__bool__", "__name__",
            }
            if node.attr not in safe_dunders:
                self.errors.append(
                    f"🚫 禁止访问 '{node.attr}'（安全限制）"
                )
        self.generic_visit(node)


def check_code_safety(code: str) -> Optional[str]:
    """
    对代码进行 AST 安全检查。

    Returns:
        None 表示安全，否则返回错误信息字符串
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"❌ 代码语法错误: {e}"

    checker = SafetyChecker()
    checker.visit(tree)

    if checker.errors:
        return "代码安全检查未通过:\n" + "\n".join(checker.errors)

    return None


# ─────────────────────────────────────────────
# 受限 builtins（第 2 层防护）
# ─────────────────────────────────────────────

def _make_safe_builtins() -> dict:
    """构建安全的 builtins 字典，移除危险函数"""
    import builtins as _builtins

    safe = {}
    # 白名单：允许的内置函数
    allowed_builtins = {
        # 类型转换
        "int", "float", "str", "bool", "bytes", "bytearray",
        "list", "tuple", "dict", "set", "frozenset",
        "complex", "memoryview",
        # 常用函数
        "abs", "all", "any", "bin", "chr", "divmod",
        "enumerate", "filter", "format", "hash", "hex",
        "isinstance", "issubclass", "iter", "len", "map",
        "max", "min", "next", "oct", "ord", "pow",
        "print", "range", "repr", "reversed", "round",
        "slice", "sorted", "sum", "type", "zip",
        # 字符串相关
        "ascii", "callable", "id", "dir", "vars", "hasattr", "getattr",
        # 异常类
        "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "AttributeError", "RuntimeError",
        "StopIteration", "ZeroDivisionError", "FileNotFoundError",
        "True", "False", "None",
        # 允许只读 open
        "open",
    }

    for name in allowed_builtins:
        obj = getattr(_builtins, name, None)
        if obj is not None:
            safe[name] = obj

    # 替换 open 为只读版本
    original_open = _builtins.open

    def safe_open(file, mode="r", *args, **kwargs):
        # 只允许读模式
        if any(c in mode for c in ("w", "a", "x", "+")):
            raise PermissionError(
                f"🚫 安全限制: 只允许读取文件 (mode='r')，"
                f"不允许写入 (mode='{mode}')"
            )
        return original_open(file, mode, *args, **kwargs)

    safe["open"] = safe_open

    # 提供安全的 __import__（仅允许白名单模块）
    # Python 的 import 语句底层依赖 __import__，必须提供，否则 import 会失败。
    # AST 检查器（第1层）已经过滤了危险模块，这里是第2层的纵深防御。
    original_import = _builtins.__import__

    def safe_import(name, *args, **kwargs):
        module_root = name.split(".")[0]
        if module_root not in ALLOWED_MODULES:
            raise ImportError(
                f"模块 '{name}' 不在白名单中，禁止导入。"
            )
        return original_import(name, *args, **kwargs)

    safe["__import__"] = safe_import

    return safe


# ─────────────────────────────────────────────
# 沙盒执行引擎（第 3 层防护：进程隔离 + 超时强杀）
# ─────────────────────────────────────────────

def _execute_in_sandbox(code: str, safe_builtins: dict) -> dict:
    """
    在受限环境中执行 Python 代码。

    Returns:
        {"stdout": str, "result": str, "error": str}
    """
    # 捕获 stdout
    captured_stdout = io.StringIO()

    # 构建受限执行环境
    restricted_globals = {
        "__builtins__": safe_builtins,
        "__name__": "__sandbox__",
    }

    # 预导入白名单中的模块（方便 Agent 使用）
    for mod_name in ("re", "math", "json", "collections", "datetime"):
        try:
            restricted_globals[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    result = {"stdout": "", "result": "", "error": ""}

    old_stdout = sys.stdout
    try:
        sys.stdout = captured_stdout

        # 执行代码
        exec_result = exec(compile(code, "<agent_sandbox>", "exec"),
                           restricted_globals)

        result["stdout"] = captured_stdout.getvalue()

        # 尝试获取最后一个表达式的值（类似 REPL）
        try:
            tree = ast.parse(code)
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_expr = ast.Expression(body=tree.body[-1].value)
                ast.fix_missing_locations(last_expr)
                val = eval(compile(last_expr, "<agent_sandbox>", "eval"),
                           restricted_globals)
                if val is not None:
                    result["result"] = repr(val)
        except Exception:
            pass  # 忽略，不影响主执行结果

    except PermissionError as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        sys.stdout = old_stdout

    return result


def _sandbox_worker(code: str, result_queue: multiprocessing.Queue):
    """
    子进程入口函数：在完全独立的进程中执行沙盒代码。

    为什么必须是模块级函数？
    Windows 的 multiprocessing 使用 spawn 模式（而非 fork），
    子进程需要重新 import 模块，因此 target 函数必须是可 import 的
    模块级函数，不能是闭包或 lambda。

    safe_builtins 在子进程内部重新构建（不通过参数传递），
    因为 safe_open/safe_import 等函数对象无法跨进程 pickle 序列化。
    """
    try:
        safe_builtins = _make_safe_builtins()
        result = _execute_in_sandbox(code, safe_builtins)
        result_queue.put(result)
    except Exception as e:
        result_queue.put({"stdout": "", "result": "", "error": str(e)})


def execute_sandboxed(code: str, timeout: int = EXECUTION_TIMEOUT) -> str:
    """
    带进程隔离和超时强杀的沙盒执行。

    相比旧版 threading 方案的优势：
    - threading.Thread: 共享内存，受 GIL 限制，超时后线程仍在后台运行
    - multiprocessing.Process: 独立内存空间，可 terminate/kill 真正杀死

    Returns:
        执行结果的文本描述
    """
    # 第 1 层：AST 安全检查（在主进程中完成，零成本）
    safety_error = check_code_safety(code)
    if safety_error:
        return safety_error

    # 第 2 + 3 层：在独立子进程中执行（进程隔离 + 超时强杀）
    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_sandbox_worker,
        args=(code, result_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=timeout)

    # ── 超时处理：两级强杀 ──
    if proc.is_alive():
        # 第一级：SIGTERM（优雅终止）
        proc.terminate()
        proc.join(timeout=2)

        if proc.is_alive():
            # 第二级：SIGKILL（强制杀死，Windows 下等同 TerminateProcess）
            proc.kill()
            proc.join(timeout=1)

        return (
            f"⏰ 代码执行超时（超过 {timeout} 秒），已强制终止子进程！\n"
            f"可能原因: 死循环或计算量过大。请优化代码后重试。"
        )

    # ── 子进程异常退出 ──
    if proc.exitcode is not None and proc.exitcode != 0 and result_queue.empty():
        return f"❌ 沙盒子进程异常退出 (exit code: {proc.exitcode})"

    # ── 获取执行结果 ──
    if result_queue.empty():
        return "❌ 执行异常: 子进程未返回结果"

    try:
        result = result_queue.get_nowait()
    except Exception:
        return "❌ 执行异常: 无法读取子进程结果"

    # 组装输出
    output_parts = []

    if result["error"]:
        output_parts.append(f"❌ 运行报错:\n{result['error']}")

    if result["stdout"]:
        stdout = result["stdout"]
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + \
                     f"\n... (输出过长，已截断。共 {len(result['stdout'])} 字符)"
        output_parts.append(f"📤 输出:\n{stdout}")

    if result["result"]:
        output_parts.append(f"📊 返回值: {result['result']}")

    if not output_parts:
        output_parts.append("✅ 代码执行完成（无输出）")

    return "\n".join(output_parts)


# ─────────────────────────────────────────────
# Tool 类（注册到 Agent）
# ─────────────────────────────────────────────

class CodeInterpreterTool(Tool):
    name = "execute_python"
    description = (
        "安全沙盒 Python 代码解释器。你可以编写并执行任意 Python 代码来完成分析、计算、"
        "文本处理等任务。这是一个只读沙盒，不能修改文件或访问网络。\n"
        "使用场景：\n"
        "- 用正则表达式分析文本格式问题\n"
        "- 统计文档中的数据（词频、字数、参考文献分布等）\n"
        "- 数学计算和数据处理\n"
        "- 对比不同文本段落的风格差异\n"
        "- 任何需要'写个小脚本算一下'的临时需求\n"
        "可用模块: re, math, statistics, collections, json, csv, datetime, "
        "string, textwrap, difflib, itertools, functools, random, hashlib 等。\n"
        "可以用 open(path, 'r') 只读打开文件。禁止写入文件、访问网络或系统操作。\n"
        "代码执行有 5 秒超时限制。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "要执行的 Python 代码。可以使用 print() 输出结果。"
                    "代码在安全沙盒中运行，只允许使用白名单中的模块。"
                ),
            },
        },
        "required": ["code"],
    }

    def execute(self, code: str) -> str:
        if not code or not code.strip():
            return "❌ 代码为空，请提供要执行的 Python 代码。"
        return execute_sandboxed(code)
