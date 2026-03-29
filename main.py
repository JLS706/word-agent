# -*- coding: utf-8 -*-
"""
DocMaster Agent - 主入口
学术论文排版 AI 智能体，通过自然语言指令驱动 Word 文档自动化处理。

用法:
    python main.py                  # 交互式模式
    python main.py --dry-run        # Dry-run 模式（不实际执行工具）
    python main.py --test           # 测试 LLM 连通性
"""

import os
import sys
import argparse

from core.logger import logger

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_config() -> dict:
    """加载配置文件"""
    try:
        import toml
    except ImportError:
        # 回退到标准库 tomllib (Python 3.11+)
        try:
            import tomllib as toml
            # tomllib 只支持 rb 模式
            config_path = os.path.join(PROJECT_ROOT, "config", "config.toml")
            if not os.path.exists(config_path):
                print("❌ 配置文件未找到！")
                print(f"   请复制 config/config.example.toml 为 config/config.toml")
                print(f"   然后填入你的 LLM API Key")
                sys.exit(1)
            with open(config_path, "rb") as f:
                return toml.load(f)
        except ImportError:
            print("❌ 需要安装 toml 库: pip install toml")
            sys.exit(1)

    config_path = os.path.join(PROJECT_ROOT, "config", "config.toml")
    if not os.path.exists(config_path):
        print("❌ 配置文件未找到！")
        print(f"   请复制 config/config.example.toml 为 config/config.toml")
        print(f"   然后填入你的 LLM API Key")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return toml.load(f)


def create_agent(config: dict, dry_run: bool = False):
    """根据配置创建 Agent 实例和 Multi-Agent 编排器"""
    from core.llm import LLM
    from core.agent import Agent
    from core.memory import Memory
    from core.multi_agent import MultiAgentOrchestrator
    from tools.base import ToolRegistry

    # 导入所有工具
    from tools.ref_formatter import RefFormatterTool
    from tools.ref_crossref import RefCrossRefTool
    from tools.fig_crossref import FigCrossRefTool
    from tools.fig_caption import FigCaptionTool
    from tools.acronym_checker import AcronymCheckerTool
    from tools.latex_converter import LatexConverterTool
    from tools.doc_reader import DocReaderTool
    from tools.pipeline import AnalyzeDocumentTool
    from tools.memory_tool import RecallHistoryTool, SavePreferenceTool
    from tools.code_interpreter import CodeInterpreterTool
    from tools.learned_rules import (
        SaveLearnedRuleTool, ForgetLearnedRuleTool, ListLearnedRulesTool
    )
    from tools.rag import IndexDocumentTool, SearchDocumentTool
    from tools.doc_summarizer import SummarizeDocumentTool
    from tools.doc_format_inspector import InspectDocFormatTool
    from tools.word_cleanup import CloseWordTool
    from tools.pipeline_tool import RunPipelineTool, set_orchestrator
    from tools.tool_creator import (
        CreateToolTool, ApproveToolTool, ListCustomToolsTool,
        load_custom_tools,
    )

    # 初始化 LLM
    llm_config = config.get("llm", {})
    llm = LLM(**llm_config)

    # 初始化 Embedding 客户端（Memory 和 Skills 共用）
    embed_client = None
    try:
        from core.embeddings import EmbeddingClient
        embed_client = EmbeddingClient(
            api_key=llm_config.get("api_key", ""),
            base_url=llm_config.get("base_url", ""),
            model=llm_config.get("embedding_model", "gemini-embedding-001"),
        )
    except Exception:
        pass

    # 初始化本地记忆（含向量记忆）
    memory_dir = os.path.join(PROJECT_ROOT, "memory")
    memory = Memory(memory_dir, embed_client=embed_client)

    # 注册所有工具
    registry = ToolRegistry()
    registry.register(AnalyzeDocumentTool())      # 文档分析（Pipeline规划）
    registry.register(DocReaderTool())             # 文档读取（供LLM分析）
    registry.register(InspectDocFormatTool())       # 文档格式检查（样式/字体/缩进）
    registry.register(RecallHistoryTool(memory))   # 记忆查询
    registry.register(SavePreferenceTool(memory))  # 偏好保存
    registry.register(RefFormatterTool())
    registry.register(RefCrossRefTool())
    registry.register(FigCrossRefTool())
    registry.register(FigCaptionTool())
    registry.register(AcronymCheckerTool())
    registry.register(LatexConverterTool())
    registry.register(CodeInterpreterTool())        # 安全沙盒代码解释器
    registry.register(SaveLearnedRuleTool())         # 自学习：保存规则
    registry.register(ForgetLearnedRuleTool())       # 自学习：删除规则
    registry.register(ListLearnedRulesTool())        # 自学习：列出规则
    registry.register(IndexDocumentTool())           # RAG：文档索引
    registry.register(SearchDocumentTool())          # RAG：语义搜索
    registry.register(CloseWordTool())                # Word 进程清理
    registry.register(SummarizeDocumentTool())         # 全文摘要(Map-Reduce)
    registry.register(CreateToolTool())               # 动态工具创建
    registry.register(ApproveToolTool(registry))      # 工具审批激活
    registry.register(ListCustomToolsTool())          # 列出自定义工具
    registry.register(RunPipelineTool())               # Multi-Agent 流水线触发

    # 自动加载已审批的自定义工具
    custom_count = load_custom_tools(registry)
    if custom_count > 0:
        print(f"📦 已加载 {custom_count} 个自定义工具")

    # 初始化 Skills 管理器（复用同一 embed_client）
    from core.skills import SkillManager
    skills_dir = os.path.join(PROJECT_ROOT, "skills")
    skill_manager = SkillManager(skills_dir, embed_client=embed_client)
    logger.info("已加载 %d 个技能: %s",
                len(skill_manager.skills),
                [s.name for s in skill_manager.skills])

    # 创建 Executor Agent
    agent_config = config.get("agent", {})

    # 根据配置调整日志级别
    from core.logger import setup_logger
    verbose = agent_config.get("verbose", True)
    setup_logger(verbose=verbose)

    agent = Agent(
        llm=llm,
        tool_registry=registry,
        max_steps=agent_config.get("max_steps", 10),
        verbose=agent_config.get("verbose", True),
        dry_run=dry_run,
        memory=memory,
        skill_manager=skill_manager,
    )

    # 创建 Multi-Agent 编排器
    orchestrator = MultiAgentOrchestrator(
        llm=llm,
        executor_agent=agent,
        tool_registry=registry,
        memory=memory,
        verbose=agent_config.get("verbose", True),
        checkpoint_dir=os.path.join(PROJECT_ROOT, "checkpoints"),
    )

    # 注入 orchestrator 到 pipeline_tool（让 Agent 可以自主调用流水线）
    set_orchestrator(orchestrator)

    return agent, orchestrator


def test_connection(config: dict):
    """测试 LLM 连通性"""
    from core.llm import LLM

    llm_config = config.get("llm", {})
    logger.info("🔗 正在测试 LLM 连接...")
    logger.info("   模型: %s", llm_config.get('model', '未指定'))
    logger.info("   地址: %s", llm_config.get('base_url', '未指定'))

    try:
        llm = LLM(**llm_config)
        reply = llm.test_connection()
        logger.info("✅ 连接成功！模型回复: %s", reply)
    except Exception as e:
        logger.error("❌ 连接失败: %s", e)
        import traceback
        traceback.print_exc()


def interactive_loop(agent, orchestrator=None):
    """交互式命令循环"""
    print("\n" + "=" * 60)
    print("  🤖 DocMaster Agent — 学术论文排版智能助手")
    print("  输入你的需求，Agent 会自动选择工具完成任务")
    print("  输入 'quit' 或 'exit' 退出")
    print("  输入 'reset' 重置对话历史")
    print("  输入 'tools' 查看可用工具列表")
    print("  输入 'pipeline <文件路径>' 启动 Multi-Agent 流水线")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n🧑 你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ("quit", "exit", "q"):
            print("👋 再见！")
            break
        elif cmd == "reset":
            agent.reset()
            print("🔄 对话历史已重置。")
            continue
        elif cmd == "tools":
            print("\n📦 可用工具:")
            print(agent.tools.describe())
            continue
        elif cmd.startswith("pipeline"):
            # Multi-Agent 流水线模式
            parts = user_input.split(maxsplit=1)
            if len(parts) < 2:
                print("⚠️ 用法: pipeline <Word文档路径>")
                print("   例: pipeline C:\\Users\\xxx\\论文.docx")
                continue
            file_path = parts[1].strip()
            if orchestrator:
                orchestrator.run_pipeline(file_path)
            else:
                print("⚠️ Multi-Agent 模式未初始化。")
            continue

        # 普通对话走单 Agent 模式
        response = agent.run(user_input)
        if not agent.verbose:
            print(f"\n🤖 Agent: {response}")


def main():
    parser = argparse.ArgumentParser(description="DocMaster Agent - 学术论文排版智能助手")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run模式，不实际执行工具")
    parser.add_argument("--test", action="store_true", help="测试LLM连通性")
    args = parser.parse_args()

    # 加载配置
    config = load_config()

    if args.test:
        test_connection(config)
        return

    # 创建 Agent 和 Multi-Agent 编排器
    agent, orchestrator = create_agent(config, dry_run=args.dry_run)

    if args.dry_run:
        logger.info("🏜️  Dry-Run 模式已启用，工具将不会实际执行。")

    interactive_loop(agent, orchestrator)


if __name__ == "__main__":
    main()
