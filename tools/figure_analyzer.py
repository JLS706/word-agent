# -*- coding: utf-8 -*-
"""
Tool: PDF 图表分析（多模态视觉）

从 PDF 中提取指定图片，发送给支持视觉的 LLM 进行分析。
典型场景：
  - 综述写作时理解参考文献中的实验结果图
  - 校验论文插图与文字描述是否一致
  - 提取图表中的关键数据点
"""

import os
import base64
import logging

from tools.base import Tool

logger = logging.getLogger(__name__)


def _extract_pdf_images(
    pdf_path: str,
    page_numbers: list[int] | None = None,
    max_images: int = 10,
) -> list[dict]:
    """
    从 PDF 中提取嵌入图片。

    Args:
        pdf_path: PDF 文件路径
        page_numbers: 指定页码列表（0-based），None 表示全部
        max_images: 最多提取的图片数量

    Returns:
        [{"page": 0, "index": 0, "base64": "...", "width": 800, "height": 600}, ...]
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("需要安装 PyMuPDF: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    results = []

    pages = page_numbers if page_numbers else range(len(doc))

    for page_num in pages:
        if page_num >= len(doc):
            continue
        page = doc[page_num]
        image_list = page.get_images(full=True)

        for img_idx, img_info in enumerate(image_list):
            if len(results) >= max_images:
                break

            xref = img_info[0]
            try:
                pix = fitz.Pixmap(doc, xref)
                # 转为 RGB（如果是 CMYK 等）
                if pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)

                img_bytes = pix.tobytes("png")
                b64 = base64.b64encode(img_bytes).decode("ascii")

                results.append({
                    "page": page_num,
                    "index": img_idx,
                    "base64": b64,
                    "width": pix.width,
                    "height": pix.height,
                })
                pix = None  # 释放
            except Exception as e:
                logger.debug("提取图片失败 (page=%d, xref=%d): %s", page_num, xref, e)
                continue

        if len(results) >= max_images:
            break

    doc.close()
    return results


def _render_pdf_page(pdf_path: str, page_num: int, dpi: int = 150) -> str:
    """
    将 PDF 指定页渲染为图片（当页面没有嵌入图片时的备选方案）。

    Returns:
        base64 编码的 PNG 图片
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("需要安装 PyMuPDF: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    if page_num >= len(doc):
        doc.close()
        raise ValueError(f"页码 {page_num} 超出范围（共 {len(doc)} 页）")

    page = doc[page_num]
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    doc.close()

    return base64.b64encode(img_bytes).decode("ascii")


class AnalyzeFigureTool(Tool):
    """
    分析 PDF 中的图表（多模态视觉问答）。

    提取 PDF 的指定页面/图片，发送给视觉 LLM 进行分析。
    """

    name = "analyze_figure"
    description = (
        "从 PDF 中提取图表并使用视觉 AI 进行分析。"
        "可以回答关于图表内容、趋势、数据点的问题。"
        "例如：'这张图的横轴是什么？''实验结果是否支持文中的结论？'"
        "支持两种模式：提取嵌入图片，或将整页渲染为图片。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "PDF 文件路径",
            },
            "question": {
                "type": "string",
                "description": "关于图表的问题（如 '这张图展示了什么趋势？'）",
            },
            "page": {
                "type": "integer",
                "description": "页码（从 0 开始），不指定则自动提取前几张图",
            },
            "mode": {
                "type": "string",
                "description": (
                    "提取模式：'images' 提取嵌入图片（默认），"
                    "'page' 将整页渲染为图片（适合扫描版 PDF 或复杂排版）"
                ),
            },
        },
        "required": ["file_path", "question"],
    }

    def __init__(self, llm=None):
        self._llm = llm

    def execute(
        self,
        file_path: str,
        question: str,
        page: int | None = None,
        mode: str = "images",
        **kwargs,
    ) -> str:
        from core.schema import Message, Role

        if self._llm is None:
            return "❌ LLM 未注入，无法执行视觉分析"

        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"❌ 文件不存在: {abs_path}"
        if not abs_path.lower().endswith(".pdf"):
            return "❌ 仅支持 PDF 文件"

        self.report_progress(10, "提取图片中...")

        try:
            if mode == "page":
                # 整页渲染模式
                target_page = page if page is not None else 0
                b64_img = _render_pdf_page(abs_path, target_page)
                image_data = [b64_img]
                context = f"以下是 PDF 第 {target_page + 1} 页的渲染图片。"
            else:
                # 嵌入图片提取模式
                page_list = [page] if page is not None else None
                images = _extract_pdf_images(abs_path, page_numbers=page_list, max_images=4)

                if not images:
                    # 没有嵌入图片 → 回退到整页渲染
                    target_page = page if page is not None else 0
                    self.report_progress(30, "未找到嵌入图片，渲染整页...")
                    b64_img = _render_pdf_page(abs_path, target_page)
                    image_data = [b64_img]
                    context = f"PDF 中未找到嵌入图片，以下是第 {target_page + 1} 页的渲染图。"
                else:
                    image_data = [img["base64"] for img in images]
                    pages_info = ", ".join(
                        f"第{img['page']+1}页图{img['index']+1}({img['width']}×{img['height']})"
                        for img in images
                    )
                    context = f"提取到 {len(images)} 张图片：{pages_info}。"

        except Exception as e:
            return f"❌ 图片提取失败: {e}"

        self.report_progress(50, f"提取完成（{len(image_data)} 张），发送给视觉 LLM...")

        # 构造多模态消息
        prompt = (
            f"你是学术论文图表分析专家。\n\n"
            f"**文件**: {os.path.basename(abs_path)}\n"
            f"**图片信息**: {context}\n\n"
            f"**用户问题**: {question}\n\n"
            f"请仔细观察图片，给出准确、详细的分析。"
            f"如果图中有数据，请尽量提取具体数值。"
            f"请使用中文回答。"
        )

        msg = Message.with_images(
            role=Role.USER,
            text=prompt,
            image_data=image_data,
            detail="high",
        )

        try:
            response = self._llm.chat([
                Message(role=Role.SYSTEM, content="你是学术论文图表分析专家，擅长解读实验结果图、性能对比图、系统架构图等。"),
                msg,
            ])
            result = response.content or "(视觉 LLM 未返回分析结果)"
        except Exception as e:
            return f"❌ 视觉分析失败: {e}"

        self.report_progress(100, "分析完成")
        return f"📊 **图表分析结果**\n\n{context}\n\n{result}"
