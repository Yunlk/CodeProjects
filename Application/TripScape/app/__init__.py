"""
行旅识景 · 模块入口

采用惰性导入（PEP 562）：只有真正访问 ImageRecognizer / QASystem 时才加载对应模块，
这样轻量调用方（如 gui.py 只用数据库）不必先装齐 faiss / sentence-transformers / scikit-image。

原有用法保持兼容：
    from app import ImageRecognizer, QASystem
"""

__all__ = ["ImageRecognizer", "QASystem"]


def __getattr__(name):
    if name == "ImageRecognizer":
        from .image_recognizer import ImageRecognizer

        return ImageRecognizer
    if name == "QASystem":
        from .qa_engine import QASystem

        return QASystem
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + __all__)
