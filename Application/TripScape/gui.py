#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
行旅识景 · 桌面版 GUI（customtkinter）

把原 Flask 网页版的三块能力搬进桌面窗口，并补上网页版没有的能力：

    1. 地标图鉴   —— 等距圆柱投影世界地图 + 26 个地标点位，点击打卡（本地持久化到
                     data/checkins.json），可离线浏览 knowledge_base 的 217 条问答。
    2. AI 问答    —— 优先走项目原有的 RAG（FAISS 向量 + BM25 混合检索，需 models/bge-small-zh）；
                     依赖或模型缺失时自动降级为纯 Python BM25 检索，保证离线可用。
    3. 图像识别   —— 调用 app/image_recognizer.py 的多尺度贝叶斯滑窗 + 逻辑回归（需 models/*.pkl），
                     模型缺失时给出明确原因，并提供不依赖码本的特征提取自检。
    4. 环境体检   —— 依赖 / 模型 / 数据状态一览，可一键 pip 安装、下载向量模型、重载问答引擎。

运行：
    cd Application/TripScape
    python gui.py

冒烟自检（开窗若干秒后自动退出）：
    set TRIPSCAPE_GUI_SMOKE=1 && python gui.py
"""

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# 路径与工作目录：项目内大量相对路径（data/、models/、static/），先切到项目根
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
try:
    os.chdir(PROJECT_ROOT)
except OSError:
    pass
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    print("缺少 customtkinter，请先执行： python -m pip install customtkinter")
    raise

from PIL import Image, ImageTk

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
APP_TITLE = "行旅识景 · 地标智能识别（桌面版）"
DB_PATH = PROJECT_ROOT / "data" / "landmark.db"
SPOTS_CSV = PROJECT_ROOT / "static" / "spots.csv"
EARTH_PNG = PROJECT_ROOT / "static" / "earth.png"
CHECKIN_PATH = PROJECT_ROOT / "data" / "checkins.json"
MODEL_NAMES = [
    "label_encoder.pkl",
    "kmeans_vlad.pkl",
    "pca_hog.pkl",
    "scaler.pkl",
    "lr_model.pkl",
]
EMBED_DIR = PROJECT_ROOT / "models" / "bge-small-zh"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
FONT = "Microsoft YaHei UI"

COLOR_BG = "#0f172a"
COLOR_PANEL = "#16213a"
COLOR_PANEL_2 = "#1d2b4a"
COLOR_ACCENT = "#2f6fed"
COLOR_CHECKED = "#f39c12"
COLOR_MUTED = "#8ba0bf"
COLOR_OK = "#2ecc71"
COLOR_BAD = "#e74c3c"

OPTIONAL_DEPS = [
    ("jieba", "中文分词（BM25 检索）"),
    ("faiss", "FAISS 向量索引（向量检索）"),
    ("sentence_transformers", "句向量模型（向量检索）"),
    ("skimage", "HOG / GLCM 特征（图像识别）"),
    ("cv2", "OpenCV（图像识别 / 特征提取）"),
    ("sklearn", "scikit-learn（分类器与降维）"),
    ("scipy", "SciPy（矢量量化）"),
]

# knowledge_base 中的问法未统一使用 heritage_items 的地标名，这里补别名对齐：
#   救世基督像  ← 基督救世主像 / 基督像
#   吉萨金字塔群 ← 吉萨金字塔 / 胡夫金字塔 / 金字塔（泛指金字塔的问题都指吉萨）
#   奇琴伊察    ← 库库尔坎金字塔 / 库库尔坎
#   崇圣寺三塔  ← 千寻塔 / 三塔
# 归组时按“最长别名优先”，避免「金字塔」把弯曲金字塔、左塞尔金字塔抢走。
LANDMARK_ALIASES = {
    "ChristTheRedeemer": ("基督救世主像", "基督像"),
    "GizaPyramids": ("吉萨金字塔", "胡夫金字塔", "金字塔"),
    "ChichenItza": ("库库尔坎金字塔", "库库尔坎"),
    "ThreePagodas": ("千寻塔", "三塔"),
    "Qomolangma": ("珠峰",),
    "RomanColosseum": ("斗兽场",),
    "Potala": ("布达拉",),
    "TajMahal": ("泰姬",),
    "GreatWall": ("中国长城",),
}

VIEW_META = [
    ("gallery", "🌍  地标图鉴"),
    ("chat", "💬  AI 问答"),
    ("recognize", "📷  图像识别"),
    ("env", "⚙️  环境体检"),
]


# ----------------------------------------------------------------------------
# 重依赖预加载
#
# CPython 下多个线程同时「首次」导入同一个重模块（torch / transformers / re …）
# 会撞进导入机制的锁里死锁，症状就是界面一直停在「加载中…」。
# 因此在主线程一次性预加载，并让线程内的导入统一走 ensure_import 串行化。
# ----------------------------------------------------------------------------
IMPORT_LOCK = threading.Lock()

PRELOAD_MODULES = (
    "jieba",
    "faiss",
    "torch",
    "sentence_transformers",
    "skimage",
    "cv2",
    "sklearn",
    "huggingface_hub",
)
PRELOAD_PROJECT_MODULES = ("app.db", "app.qa_engine", "app.image_recognizer")


def ensure_import(module_name: str):
    """线程内导入模块时串行化，避免与其它线程的首次导入相撞。"""
    with IMPORT_LOCK:
        return importlib.import_module(module_name)


def preload_heavy_imports(progress=None) -> dict:
    """在主线程预加载重依赖，返回 {模块: 结果描述}。"""
    results = {}
    for module_name in PRELOAD_MODULES:
        if not module_available(module_name):
            results[module_name] = "未安装"
            continue
        if progress:
            progress(f"正在加载 {module_name} …")
        started = time.time()
        try:
            ensure_import(module_name)
            results[module_name] = f"OK（{time.time() - started:.1f}s）"
        except Exception as exc:
            results[module_name] = f"失败：{exc}"
    for module_name in PRELOAD_PROJECT_MODULES:
        if progress:
            progress(f"正在加载 {module_name} …")
        started = time.time()
        try:
            ensure_import(module_name)
            results[module_name] = f"OK（{time.time() - started:.1f}s）"
        except Exception as exc:
            results[module_name] = f"失败：{exc}"
    return results


class _Splash:
    """预加载重依赖时显示的小提示窗（阻塞导入期间靠它刷新界面）。"""

    def __init__(self):
        self.win = ctk.CTk()
        self.win.title(APP_TITLE)
        self.win.geometry("460x150")
        self.win.resizable(False, False)
        self.win.configure(fg_color=COLOR_BG)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        ctk.CTkLabel(
            self.win, text="🌍 行旅识景", font=ctk.CTkFont(family=FONT, size=20, weight="bold"),
        ).pack(pady=(26, 4))
        self.status = ctk.CTkLabel(
            self.win, text="正在加载依赖…", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.status.pack()
        self.win.update()

    def set_status(self, text: str):
        self.status.configure(text=text)
        self.win.update()

    def close(self):
        # customtkinter 内部用 after() 排了 update / check_dpi_scaling 回调，
        # 直接 destroy 会让它们在窗口销毁后触发，报 invalid command name。
        try:
            for after_id in self.win.tk.call("after", "info"):
                try:
                    self.win.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------------
def module_available(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def module_version(mod: str) -> str:
    try:
        from importlib.metadata import version

        return version(mod)
    except Exception:
        return "-"


def load_checkins() -> dict:
    try:
        with open(CHECKIN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        checked = data.get("checked", {})
        return checked if isinstance(checked, dict) else {}
    except Exception:
        return {}


def save_checkins(checked: dict) -> None:
    try:
        CHECKIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKIN_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"version": 1, "checked": checked},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        print(f"保存打卡记录失败: {exc}")


def cjk_runs(text: str):
    """切出所有连续中文片段，便于做 bigram。"""
    return re.findall(r"[\u4e00-\u9fff]+", text)


try:  # 有 jieba 就用真分词；没有则退化为 bigram，保证降级后端零依赖也能跑
    import jieba as _jieba

    _jieba.setLogLevel(60)
except Exception:  # pragma: no cover
    _jieba = None

_LATIN_RE = re.compile(r"[a-z0-9]+")
_WORD_CHAR_RE = re.compile(r"[0-9a-z\u4e00-\u9fff]")


def tokenize(text: str) -> list:
    """分词：优先 jieba，缺失时退化为「拉丁/数字按词 + 中文 bigram」。"""
    text = (text or "").lower()
    if _jieba is not None:
        return [
            token
            for token in (t.strip() for t in _jieba.cut(text))
            if token and _WORD_CHAR_RE.search(token)
        ]
    tokens = _LATIN_RE.findall(text)
    for run in cjk_runs(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


# 出现这些词说明用户在“提问”而不是“点名地标”，此时不做介绍条目直答
QUESTION_WORDS = (
    "多少", "多大", "多高", "多长", "哪里", "哪儿", "什么", "怎么", "如何",
    "哪", "几", "吗", "呢", "吧", "谁", "为什么", "为啥", "是否", "介绍",
)

# 这些词命中不算“相关证据”，否则「今天天气怎么样？」会靠「怎么样」
# 蹭上「珠峰气候怎么样？」、「故宫在哪里？」会靠「在哪里」蹭上「布达拉宫在哪里？」
STOP_WORDS = set(QUESTION_WORDS) | {
    "怎么样", "怎样", "怎么办", "为什么", "是什么", "有哪些", "时候", "一下",
    "请问", "知道", "告诉", "想问", "的", "了", "是", "在", "有", "和", "与",
    "它", "他", "她", "这", "那", "你", "我", "会", "能", "要", "可以",
}

# IDF 低于该值说明词太通用（如「建于」「时候」），不作为相关证据
EVIDENCE_MIN_IDF = 3.0

NO_MATCH_TEXT = (
    "抱歉，本地知识库中暂时没有找到与您问题匹配的信息。请尝试更具体的关键词。"
)

_PUNCT_RE = re.compile(r"[\s，,。.、？?！!；;：:（）()【】\[\]\"'“”‘’\-—]+")


def strip_punct(text: str) -> str:
    return _PUNCT_RE.sub("", text or "")


def split_questions(text: str) -> list:
    """按中文标点拆分子问题，非疑问片段并回上一句（对齐 qa_engine._split_questions）。"""
    parts = re.split(r"([。？?！!；;])", text or "")
    sentences, buffer = [], ""
    for part in parts:
        if part in "。？?！!；;":
            buffer += part
            if buffer.strip():
                sentences.append(buffer.strip())
            buffer = ""
        else:
            buffer += part
    if buffer.strip():
        sentences.append(buffer.strip())

    valid = []
    for sentence in sentences:
        if len(sentence) < 2:
            continue
        is_question = (
            any(word in sentence for word in QUESTION_WORDS)
            or len(sentence) <= 6
            or sentence.endswith(("？", "?"))
        )
        if is_question:
            valid.append(sentence)
        elif valid:
            valid[-1] += sentence
        else:
            valid.append(sentence)
    return valid


def parse_matched_questions(answer: str) -> list:
    """从 qa_engine 的格式化答案里取出「匹配问题」列表。"""
    found = []
    for line in (answer or "").splitlines():
        if line.startswith("匹配问题："):
            found.append(line.replace("匹配问题：", "").strip())
    return found


# ----------------------------------------------------------------------------
# 数据层
# ----------------------------------------------------------------------------
class DataStore:
    """读取 landmark.db 与 spots.csv，并把知识条目按地标归组。"""

    def __init__(self):
        self.landmarks = []          # [{"target_id","name","lat","lon","entries":[...]}]
        self.by_target = {}
        self.by_name = {}
        self.kb_total = 0
        self.unmatched = []          # 未归组到任何地标的条目
        self.error = None

    def load(self):
        self.landmarks.clear()
        self.by_target.clear()
        self.by_name.clear()
        self.unmatched = []

        coords = self._load_spots()
        rows = self._load_heritage()
        entries = self._load_knowledge()
        self.kb_total = len(entries)

        for target_id, name in rows:
            lat, lon = coords.get(name, (None, None))
            item = {
                "target_id": target_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "entries": [],
            }
            self.landmarks.append(item)
            self.by_target[target_id] = item
            self.by_name[name] = item

        for entry in entries:
            haystack = f"{entry['question']}\n{entry['keywords']}"
            owner = None
            for alias, item in self._alias_index:
                if alias in haystack:
                    owner = item
                    break
            if owner is None:
                self.unmatched.append(entry)
            else:
                owner["entries"].append(entry)
        return self

    @property
    def alias_pairs(self):
        """[(别名, 地标名)]，按别名长度降序，保证最长匹配优先。"""
        pairs = []
        for item in self.landmarks:
            pairs.append((item["name"], item["name"]))
            for alias in LANDMARK_ALIASES.get(item["target_id"], ()):
                pairs.append((alias, item["name"]))
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        return pairs

    @property
    def _alias_index(self):
        """[(别名, 地标条目)]，按别名长度降序，保证最长匹配优先。"""
        name_to_item = {item["name"]: item for item in self.landmarks}
        return [(alias, name_to_item[name]) for alias, name in self.alias_pairs]

    @property
    def grouped_total(self):
        return sum(len(item["entries"]) for item in self.landmarks)

    # -- 私有加载 ------------------------------------------------------------
    def _load_spots(self) -> dict:
        result = {}
        try:
            with open(SPOTS_CSV, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        result[row["name"].strip()] = (
                            float(row["lat"]),
                            float(row["lon"]),
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
        except Exception as exc:
            self.error = f"读取 spots.csv 失败: {exc}"
        return result

    def _load_heritage(self) -> list:
        if not DB_PATH.exists():
            self.error = f"数据库不存在: {DB_PATH}"
            return []
        try:
            conn = sqlite3.connect(str(DB_PATH))
            try:
                rows = conn.execute(
                    "SELECT target_id, name FROM heritage_items ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
            return [(r[0], r[1]) for r in rows]
        except Exception as exc:
            self.error = f"查询 heritage_items 失败: {exc}"
            return []

    def _load_knowledge(self) -> list:
        if not DB_PATH.exists():
            return []
        try:
            conn = sqlite3.connect(str(DB_PATH))
            try:
                rows = conn.execute(
                    "SELECT id, question, keywords, answer FROM knowledge_base ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            self.error = f"查询 knowledge_base 失败: {exc}"
            return []
        entries = []
        for rid, question, keywords, answer in rows:
            question = question or ""
            keywords = keywords or ""
            answer = answer or ""
            entries.append(
                {
                    "id": rid,
                    "question": question,
                    "keywords": keywords,
                    "answer": answer,
                    "full_text": f"问题：{question}\n关键词：{keywords}\n答案：{answer}",
                }
            )
        return entries


# ----------------------------------------------------------------------------
# 问答后端：优先真实 RAG，失败降级纯 BM25
# ----------------------------------------------------------------------------
class SimpleBM25:
    """纯 Python BM25，作为缺失 faiss / sentence-transformers 时的兜底检索。"""

    def __init__(self, documents, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.docs = documents
        self.tokens = [tokenize(d["full_text"]) for d in documents]
        self.lengths = [len(t) or 1 for t in self.tokens]
        self.avg = sum(self.lengths) / max(len(self.lengths), 1)
        self.n = len(documents)

        df = {}
        for toks in self.tokens:
            for tok in set(toks):
                df[tok] = df.get(tok, 0) + 1
        self.idf = {
            tok: max(0.0, __import__("math").log((self.n - c + 0.5) / (c + 0.5) + 1))
            for tok, c in df.items()
        }

    def _score(self, q_tokens, idx):
        from collections import Counter

        counts = Counter(self.tokens[idx])
        dl = self.lengths[idx]
        score = 0.0
        for tok in q_tokens:
            idf = self.idf.get(tok)
            if not idf:
                continue
            tf = counts.get(tok, 0)
            if not tf:
                continue
            score += idf * (tf * (self.k1 + 1)) / (
                tf + self.k1 * (1 - self.b + self.b * dl / self.avg)
            )
        return score

    def search(self, query, top_k=None):
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scored = [(i, self._score(q_tokens, i)) for i in range(self.n)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored if top_k is None else scored[:top_k]


class QaBackend:
    """问答后端基类。"""

    label = "未知"

    def ask(self, question: str) -> dict:
        raise NotImplementedError


class RelevanceGuard:
    """
    相关性护栏（GUI 侧补充）。

    原 qa_engine.hybrid_search 把向量分与 BM25 分各自做 min-max 归一化后加权，
    归一化会把每轮的最高分固定拉到 1.0，于是 format_answer 里的 score_threshold=0.2
    形同虚设：「故宫在哪里？」会返回「布达拉宫在哪里？」，「故宫建于什么时候？」
    会返回「阿布辛贝神庙建于什么时候？」（模板词「建于」共现导致）。

    护栏用「地标归属」判断：知识库条目都属于某个地标，只有当提问里点到了这个地标
    （含别名），才认可这次命中。
    """

    def __init__(self, documents, alias_pairs=None):
        self.alias_pairs = list(alias_pairs or [])
        self.owner = [self._owner_of(doc) for doc in documents]
        self.tokens = [
            set(tokenize(d["question"])) | set(tokenize(d["keywords"]))
            for d in documents
        ]
        self.index = SimpleBM25(
            [
                {"full_text": f"问题：{d['question']}\n关键词：{d['keywords']}"}
                for d in documents
            ]
        )
        self.idf = self.index.idf
        self.index_of = {}
        for i, doc in enumerate(documents):
            self.index_of.setdefault(strip_punct(doc["question"]), i)

    def _owner_of(self, doc):
        haystack = f"{doc['question']}\n{doc.get('keywords', '')}"
        for alias, name in self.alias_pairs:
            if alias in haystack:
                return name
        return None

    def _mentions(self, question: str, owner: str) -> bool:
        return any(name == owner and alias in question for alias, name in self.alias_pairs)

    def _strong_token_evidence(self, question: str, doc_idx: int) -> bool:
        """条目没有归属地标时（自由问答）退回 IDF 证据。"""
        doc_tokens = self.tokens[doc_idx]
        for token in set(tokenize(question)):
            if token in STOP_WORDS or token not in doc_tokens:
                continue
            if self.idf.get(token, 0.0) >= EVIDENCE_MIN_IDF:
                return True
        return False

    def accepts(self, question: str, doc_idx: int) -> bool:
        owner = self.owner[doc_idx]
        if owner is None:
            return self._strong_token_evidence(question, doc_idx)
        return self._mentions(question, owner)

    def answer_is_relevant(self, question: str, answer: str) -> bool:
        """答案里出现的任一「匹配问题」与提问相关即认可（复合问题会有多条）。"""
        matched = parse_matched_questions(answer)
        if not matched:
            return True  # 解析不出匹配问题（如兜底文案）就不拦截
        for item in matched:
            idx = self.index_of.get(strip_punct(item))
            if idx is not None and self.accepts(question, idx):
                return True
        return False


class RagBackend(QaBackend):
    """包装项目原有 QASystem（FAISS 向量 + BM25 混合检索），并套上相关性护栏。"""

    label = "RAG 向量+BM25 混合检索"

    def __init__(self, alias_pairs=None):
        from app.qa_engine import QASystem

        self.qa = QASystem(use_rag=True)
        if getattr(self.qa, "rag_engine", None) is None:
            raise RuntimeError(
                "RAG 引擎未初始化（通常是缺少 models/bge-small-zh 向量模型）"
            )
        self.docs = self.qa.rag_engine.documents
        self.guard = RelevanceGuard(self.docs, alias_pairs)

    def ask(self, question: str) -> dict:
        result = self.qa.get_answer(question)
        answer = result.get("answer", "")
        hit = "本地知识库中暂时没有找到" not in answer
        if hit and not self.guard.answer_is_relevant(question, answer):
            return {"answer": NO_MATCH_TEXT, "source": "rag", "hit": False}
        return {"answer": answer, "source": "rag", "hit": hit}


class Bm25Backend(QaBackend):
    """降级后端：纯 Python BM25 + 问题级相似度，不依赖 faiss / 向量模型。"""

    label = "纯 BM25 关键词检索（降级模式）"

    def __init__(self, documents, landmarks, alias_pairs=None):
        self.docs = documents
        self.by_name = landmarks
        # 索引只用「问题 + 关键词」：答案文本很长，会把问题匹配的信号稀释掉，
        # 导致「X 在哪里」被「介绍一下 X」这类长答案条目抢走。
        self.question_index = SimpleBM25(
            [
                {"full_text": f"问题：{d['question']}\n关键词：{d['keywords']}"}
                for d in documents
            ]
        )
        self.question_tokens = [
            set(tokenize(d["question"])) | set(tokenize(d["keywords"]))
            for d in documents
        ]
        self.idf = self.question_index.idf
        self.guard = RelevanceGuard(documents, alias_pairs)

    # ---- 对外接口 ----------------------------------------------------------
    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            return {"answer": "请输入有效的问题。", "source": "bm25", "hit": False}

        sub_questions = split_questions(question)
        if len(sub_questions) > 1:
            anchor = self._find_landmark(question)
            answers, hit_any = [], False
            for idx, sub in enumerate(sub_questions):
                if idx > 0 and anchor and not self._find_landmark(sub):
                    sub = f"{anchor}{sub.lstrip('它他她这那')}"
                result = self._answer_one(sub)
                hit_any = hit_any or result["hit"]
                answers.append(result["answer"])
            return {"answer": "\n\n".join(answers), "source": "bm25", "hit": hit_any}

        return self._answer_one(question)

    # ---- 单问题检索 --------------------------------------------------------
    def _answer_one(self, question: str) -> dict:
        # 1) 与知识库问题完全一致 —— 直接命中
        target = strip_punct(question)
        for doc in self.docs:
            if strip_punct(doc["question"]) == target:
                return {"answer": self._format(doc, 1.0), "source": "bm25", "hit": True}

        # 2) 单纯点名地标 —— 返回介绍条目
        intro = self._intro_entry(question)
        if intro is not None:
            return {"answer": self._format(intro, 0.95), "source": "bm25", "hit": True}

        # 3) BM25（问题侧）+ 问题词重合度 融合
        scored = self.question_index.search(question, top_k=None)
        if not scored:
            return {"answer": NO_MATCH_TEXT, "source": "bm25", "hit": False}

        q_tokens = set(tokenize(question))
        top_raw = scored[0][1] or 1.0
        best_idx, best_score = scored[0][0], -1.0
        for idx, raw in scored:
            overlap = len(q_tokens & self.question_tokens[idx]) / max(len(q_tokens), 1)
            fused = 0.6 * (raw / top_raw) + 0.4 * overlap
            if fused > best_score:
                best_idx, best_score = idx, fused

        # 4) 证据校验：命中条目必须归属于提问里点名的地标，否则判为超纲问题
        if not self.guard.accepts(question, best_idx):
            entry = self._intro_of(self._find_landmark(question))
            if entry is not None:  # 点名了地标但该地标没有对应问法 → 退回介绍条目
                return {"answer": self._format(entry, 0.6), "source": "bm25", "hit": True}
            return {"answer": NO_MATCH_TEXT, "source": "bm25", "hit": False}

        score = round(min(best_score, 1.0), 4)
        return {
            "answer": self._format(self.docs[best_idx], score),
            "source": "bm25",
            "hit": True,
        }

    def _find_landmark(self, text: str):
        for name in sorted(self.by_name, key=len, reverse=True):
            if name and name in text:
                return name
        return None

    def _intro_of(self, name):
        """取某个地标的介绍条目（没有介绍条目就用第一条）。"""
        item = self.by_name.get(name) if name else None
        if not item or not item["entries"]:
            return None
        for entry in item["entries"]:
            if "介绍" in entry["question"]:
                return entry
        return item["entries"][0]

    def _intro_entry(self, question: str):
        """纯点名地标（不含疑问词）时，直接返回该地标的介绍条目。"""
        if any(word in question for word in QUESTION_WORDS):
            return None
        return self._intro_of(self._find_landmark(question))

    @staticmethod
    def _format(doc: dict, score: float) -> str:
        return (
            f"{doc['answer']}\n"
            f"匹配问题：{doc['question']}\n"
            f"关键词：{doc['keywords']}\n"
            f"[置信度: {score:.2%}]"
        )


# ----------------------------------------------------------------------------
# 视图基类
# ----------------------------------------------------------------------------
class BaseView(ctk.CTkFrame):
    title = "视图"
    subtitle = ""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = app.store

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(
            header,
            text=self.title,
            font=ctk.CTkFont(family=FONT, size=22, weight="bold"),
        ).pack(side="left")
        if self.subtitle:
            ctk.CTkLabel(
                header,
                text=self.subtitle,
                text_color=COLOR_MUTED,
                font=ctk.CTkFont(family=FONT, size=12),
            ).pack(side="left", padx=(12, 0), pady=(8, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

    def on_show(self):
        pass


# ----------------------------------------------------------------------------
# 视图 1：地标图鉴
# ----------------------------------------------------------------------------
class GalleryView(BaseView):
    title = "🌍 地标图鉴"
    subtitle = "点击地图上的光点查看详情并打卡 · 打卡记录保存在 data/checkins.json"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.checked = dict(app.checked)
        self.selected = None
        self.filter_text = ""
        self.earth = None            # PIL.Image
        self._tk_img = None
        self._resize_job = None
        self._marker_pos = []        # [(target_id, x, y)]

        left = ctk.CTkFrame(self.body, width=272, corner_radius=12, fg_color=COLOR_PANEL)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        ctk.CTkLabel(
            left, text="进度", font=ctk.CTkFont(family=FONT, size=14, weight="bold")
        ).pack(anchor="w", padx=14, pady=(14, 4))

        self.progress_label = ctk.CTkLabel(
            left, text="已打卡 0/0", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.progress_label.pack(anchor="w", padx=14)

        self.progress = ctk.CTkProgressBar(left, height=10, progress_color=COLOR_CHECKED)
        self.progress.pack(fill="x", padx=14, pady=(4, 10))
        self.progress.set(0)

        ctk.CTkButton(
            left, text="重置全部打卡", height=30, fg_color=COLOR_PANEL_2,
            hover_color="#27395f", font=ctk.CTkFont(family=FONT, size=12),
            command=self.reset_checkins,
        ).pack(fill="x", padx=14)

        self.search = ctk.CTkEntry(
            left, placeholder_text="搜索地标…", height=32,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.search.pack(fill="x", padx=14, pady=(12, 8))
        self.search.bind("<KeyRelease>", self.on_search)

        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 12))

        center = ctk.CTkFrame(self.body, corner_radius=12, fg_color="#0a1122")
        center.pack(side="left", fill="both", expand=True, padx=12)
        self.map_hint = ctk.CTkLabel(
            center, text="地图加载中…", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=13),
        )
        self.map_hint.place(relx=0.5, rely=0.5, anchor="center")
        self.canvas = tk.Canvas(center, bg="#0a1122", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Motion>", self.on_canvas_motion)

        right = ctk.CTkFrame(self.body, width=396, corner_radius=12, fg_color=COLOR_PANEL)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)

        self.detail_name = ctk.CTkLabel(
            right, text="未选择地标", anchor="w",
            font=ctk.CTkFont(family=FONT, size=20, weight="bold"),
        )
        self.detail_name.pack(fill="x", padx=16, pady=(16, 2))

        self.detail_meta = ctk.CTkLabel(
            right, text="请在地图或左侧列表中选择一个地标", anchor="w",
            justify="left", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.detail_meta.pack(fill="x", padx=16)

        self.checkin_btn = ctk.CTkButton(
            right, text="📍 打卡", height=36, fg_color=COLOR_ACCENT,
            hover_color="#1f5bd0", state="disabled",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
            command=self.toggle_checkin,
        )
        self.checkin_btn.pack(fill="x", padx=16, pady=(12, 12))

        ctk.CTkLabel(
            right, text="知识库问答条目（点击查看答案）", anchor="w",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        ).pack(fill="x", padx=16)

        self.q_frame = ctk.CTkScrollableFrame(right, fg_color="transparent", height=190)
        self.q_frame.pack(fill="x", padx=10, pady=(6, 8))

        self.answer_box = ctk.CTkTextbox(
            right, height=180, wrap="word", fg_color="#101a30",
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.answer_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.answer_box.insert("1.0", "选择上方问题即可查看知识库原文。")
        self.answer_box.configure(state="disabled")

        self.refresh_list()
        self.update_progress()
        threading.Thread(target=self._load_earth_bg, daemon=True).start()

    # -- 地图 ---------------------------------------------------------------
    def _load_earth_bg(self):
        try:
            img = Image.open(EARTH_PNG).convert("RGB")
        except Exception as exc:
            self.app.post(lambda e=exc: self.map_hint.configure(text=f"地图加载失败：{e}"))
            return
        self.earth = img

        def done():
            self.map_hint.place_forget()
            self.render_map()

        self.app.post(done)

    def on_canvas_resize(self, _event=None):
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(140, self.render_map)

    def render_map(self):
        self._resize_job = None
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 60 or h < 60:
            self._resize_job = self.after(200, self.render_map)
            return
        self.canvas.delete("all")
        self._marker_pos.clear()

        if self.earth is not None:
            iw, ih = self.earth.size
            scale = min(w / iw, h / ih)
            tw, th = max(1, int(iw * scale)), max(1, int(ih * scale))
            ox, oy = (w - tw) // 2, (h - th) // 2
            resized = self.earth.resize((tw, th), Image.LANCZOS)
            self._tk_img = ImageTk.PhotoImage(resized)
            self.canvas.create_image(ox, oy, image=self._tk_img, anchor="nw")
        else:
            tw, th, ox, oy = w, h, 0, 0

        def project(lat, lon):
            x = ox + (lon + 180.0) / 360.0 * tw
            y = oy + (90.0 - lat) / 180.0 * th
            return x, y

        for item in self.store.landmarks:
            if item["lat"] is None or item["lon"] is None:
                continue
            x, y = project(item["lat"], item["lon"])
            checked = item["target_id"] in self.checked
            is_sel = self.selected and item["target_id"] == self.selected["target_id"]
            color = COLOR_CHECKED if checked else COLOR_ACCENT
            r = 6 if checked else 5
            if is_sel:
                self.canvas.create_oval(x - 13, y - 13, x + 13, y + 13, outline="#ffffff", width=2)
            self.canvas.create_oval(x - r - 4, y - r - 4, x + r + 4, y + r + 4,
                                    outline=color, width=1)
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
            self._marker_pos.append((item["target_id"], x, y))

        if self.selected:
            item = self.selected
            if item["lat"] is not None:
                x, y = project(item["lat"], item["lon"])
                label = f"{'✅' if item['target_id'] in self.checked else '📍'} {item['name']}"
                self.canvas.create_text(x, y - 20, text=label, fill="#ffffff",
                                        font=(FONT, 11, "bold"))

    def _nearest_marker(self, x, y, radius=16):
        best, best_d = None, radius
        for target_id, mx, my in self._marker_pos:
            d = ((mx - x) ** 2 + (my - y) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = target_id, d
        return best

    def on_canvas_click(self, event):
        target_id = self._nearest_marker(event.x, event.y)
        if target_id:
            self.select(self.store.by_target[target_id])

    def on_canvas_motion(self, event):
        target_id = self._nearest_marker(event.x, event.y)
        self.canvas.configure(cursor="hand2" if target_id else "")

    # -- 列表与详情 ----------------------------------------------------------
    def on_search(self, _event=None):
        self.filter_text = self.search.get().strip()
        self.refresh_list()

    def refresh_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        text = self.filter_text
        shown = 0
        for item in self.store.landmarks:
            if text and text not in item["name"] and text.lower() not in item["target_id"].lower():
                continue
            shown += 1
            checked = item["target_id"] in self.checked
            is_sel = self.selected and item["target_id"] == self.selected["target_id"]
            ctk.CTkButton(
                self.list_frame,
                text=f"{'✅' if checked else '📍'} {item['name']}",
                anchor="w", height=30,
                fg_color=COLOR_ACCENT if is_sel else "transparent",
                hover_color="#22345a", text_color="#ffffff" if is_sel else "#d6e2f5",
                font=ctk.CTkFont(family=FONT, size=12),
                command=lambda it=item: self.select(it),
            ).pack(fill="x", pady=2)
        if shown == 0:
            ctk.CTkLabel(
                self.list_frame, text="没有匹配的地标", text_color=COLOR_MUTED,
                font=ctk.CTkFont(family=FONT, size=12),
            ).pack(pady=10)

    def select(self, item):
        self.selected = item
        self.refresh_list()
        self.render_map()

        checked = item["target_id"] in self.checked
        lat = f"{item['lat']:.3f}" if item["lat"] is not None else "无"
        lon = f"{item['lon']:.3f}" if item["lon"] is not None else "无"
        self.detail_name.configure(text=item["name"])
        self.detail_meta.configure(
            text=(
                f"标识：{item['target_id']}\n"
                f"坐标：纬度 {lat} · 经度 {lon}\n"
                f"知识条目：{len(item['entries'])} 条\n"
                f"状态：{'✅ 已打卡 · ' + self.checked[item['target_id']][:19] if checked else '📍 未打卡'}"
            )
        )
        self.checkin_btn.configure(
            state="normal",
            text="↩️ 取消打卡" if checked else "📍 打卡",
            fg_color="#7a5a12" if checked else COLOR_ACCENT,
        )

        for widget in self.q_frame.winfo_children():
            widget.destroy()
        if not item["entries"]:
            ctk.CTkLabel(
                self.q_frame, text="该地标暂无知识库条目", text_color=COLOR_MUTED,
                font=ctk.CTkFont(family=FONT, size=12),
            ).pack(anchor="w", pady=6)
        for entry in item["entries"]:
            label = entry["question"]
            if len(label) > 26:
                label = label[:25] + "…"
            ctk.CTkButton(
                self.q_frame, text=label, anchor="w", height=28, fg_color=COLOR_PANEL_2,
                hover_color="#2a3d63", font=ctk.CTkFont(family=FONT, size=12),
                command=lambda e=entry: self.show_answer(e),
            ).pack(fill="x", pady=2)

        self.set_answer("选择上方问题即可查看知识库原文。")

    def show_answer(self, entry):
        self.set_answer(
            f"问题：{entry['question']}\n关键词：{entry['keywords']}\n\n{entry['answer']}"
        )

    def set_answer(self, text):
        self.answer_box.configure(state="normal")
        self.answer_box.delete("1.0", "end")
        self.answer_box.insert("1.0", text)
        self.answer_box.configure(state="disabled")

    # -- 打卡 ---------------------------------------------------------------
    def toggle_checkin(self):
        if not self.selected:
            return
        target_id = self.selected["target_id"]
        if target_id in self.checked:
            del self.checked[target_id]
        else:
            self.checked[target_id] = datetime.now().isoformat(timespec="seconds")
        self.app.checked = dict(self.checked)
        save_checkins(self.checked)
        self.select(self.selected)
        self.refresh_list()
        self.update_progress()

    def reset_checkins(self):
        if not self.checked:
            return
        if not messagebox.askyesno("重置打卡", "确定清空全部打卡记录吗？"):
            return
        self.checked.clear()
        self.app.checked = {}
        save_checkins(self.checked)
        self.update_progress()
        self.refresh_list()
        self.render_map()
        if self.selected:
            self.select(self.selected)

    def update_progress(self):
        total = len(self.store.landmarks)
        done = sum(1 for it in self.store.landmarks if it["target_id"] in self.checked)
        self.progress.set(done / total if total else 0)
        self.progress_label.configure(
            text=f"已打卡 {done}/{total}（{done / total * 100:.0f}%）" if total else "无数据"
        )

    def on_show(self):
        self.render_map()


# ----------------------------------------------------------------------------
# 视图 2：AI 问答
# ----------------------------------------------------------------------------
class ChatView(BaseView):
    title = "💬 AI 问答"
    subtitle = "优先使用项目原有 RAG 检索，依赖或向量模型缺失时自动降级为纯 BM25"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.busy = False

        bar = ctk.CTkFrame(self.body, fg_color=COLOR_PANEL, corner_radius=10)
        bar.pack(fill="x", pady=(0, 10))
        self.backend_label = ctk.CTkLabel(
            bar, text="检索后端：初始化中…", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.backend_label.pack(side="left", padx=14, pady=8)
        ctk.CTkButton(
            bar, text="重载引擎", width=90, height=28, fg_color=COLOR_PANEL_2,
            hover_color="#2a3d63", font=ctk.CTkFont(family=FONT, size=12),
            command=self.reload_backend,
        ).pack(side="right", padx=10, pady=6)

        self.messages = ctk.CTkScrollableFrame(self.body, fg_color=COLOR_PANEL, corner_radius=12)
        self.messages.pack(fill="both", expand=True)

        chips = ctk.CTkFrame(self.body, fg_color="transparent")
        chips.pack(fill="x", pady=(10, 0))
        for text in ("介绍一下天坛", "埃菲尔铁塔在哪里？", "长城是世界遗产吗？"):
            ctk.CTkButton(
                chips, text=text, height=26, fg_color=COLOR_PANEL_2, hover_color="#2a3d63",
                font=ctk.CTkFont(family=FONT, size=12),
                command=lambda t=text: self.quick_ask(t),
            ).pack(side="left", padx=(0, 8))

        bottom = ctk.CTkFrame(self.body, fg_color="transparent")
        bottom.pack(fill="x", pady=(10, 0))
        self.entry = ctk.CTkTextbox(
            bottom, height=76, wrap="word", fg_color=COLOR_PANEL,
            font=ctk.CTkFont(family=FONT, size=13),
        )
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.bind("<Return>", self.on_return)
        self.entry.bind("<Control-Return>", self.on_return)
        self.send_btn = ctk.CTkButton(
            bottom, text="发送", width=96, height=76, fg_color=COLOR_ACCENT,
            hover_color="#1f5bd0", font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
            command=self.send,
        )
        self.send_btn.pack(side="left", padx=(10, 0))

        self.add_bubble(
            "你好，我是行旅识景的本地知识库助手。可以问我 26 座世界地标的位置、高度、"
            "建造者、历史意义等问题（Ctrl+Enter 发送）。",
            "bot",
        )

    # -- 消息气泡 ------------------------------------------------------------
    def add_bubble(self, text, kind):
        row = ctk.CTkFrame(self.messages, fg_color="transparent")
        row.pack(fill="x", pady=5, padx=6)
        if kind == "user":
            color, anchor = COLOR_ACCENT, "e"
        elif kind == "error":
            color, anchor = "#5c1f1f", "w"
        else:
            color, anchor = COLOR_PANEL_2, "w"
        bubble = ctk.CTkFrame(row, fg_color=color, corner_radius=12)
        bubble.pack(anchor=anchor)
        ctk.CTkLabel(
            bubble, text=text, wraplength=620, justify="left", anchor="w",
            font=ctk.CTkFont(family=FONT, size=13),
        ).pack(padx=14, pady=10)
        self._scroll_bottom()
        return bubble

    def _scroll_bottom(self):
        try:
            self.messages._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    # -- 交互 ---------------------------------------------------------------
    def on_return(self, event):
        if event.state & 0x0004:  # Ctrl 按下
            self.send()
            return "break"
        return None

    def quick_ask(self, text):
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", text)
        self.send()

    def send(self):
        if self.busy:
            return
        question = self.entry.get("1.0", "end").strip()
        if not question:
            return
        self.entry.delete("1.0", "end")
        self.add_bubble(question, "user")

        backend = self.app.qa_backend
        if backend is None:
            self.add_bubble("问答引擎尚未就绪，请稍候或到「环境体检」查看原因。", "error")
            return

        self.busy = True
        self.send_btn.configure(state="disabled", text="检索中")
        placeholder = self.add_bubble("正在检索本地知识库…", "bot")

        def work():
            started = time.time()
            try:
                result = backend.ask(question)
                elapsed = time.time() - started
                self.app.post(lambda: self._finish(placeholder, result, elapsed))
            except Exception as exc:
                self.app.post(lambda e=exc: self._fail(placeholder, e))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, placeholder, result, elapsed):
        for child in placeholder.winfo_children():
            child.destroy()
        tag = "" if result.get("hit", True) else "（未命中，返回兜底提示）"
        ctk.CTkLabel(
            placeholder,
            text=f"{result.get('answer', '')}\n\n— {self._source_label(result)} · {elapsed:.2f}s {tag}",
            wraplength=620, justify="left", anchor="w",
            font=ctk.CTkFont(family=FONT, size=13),
        ).pack(padx=14, pady=10)
        self.busy = False
        self.send_btn.configure(state="normal", text="发送")
        self._scroll_bottom()

    def _fail(self, placeholder, exc):
        for child in placeholder.winfo_children():
            child.destroy()
        placeholder.configure(fg_color="#5c1f1f")
        ctk.CTkLabel(
            placeholder, text=f"检索出错：{exc}", wraplength=620, justify="left",
            font=ctk.CTkFont(family=FONT, size=13),
        ).pack(padx=14, pady=10)
        self.busy = False
        self.send_btn.configure(state="normal", text="发送")

    @staticmethod
    def _source_label(result):
        return "RAG 向量+BM25" if result.get("source") == "rag" else "BM25 降级检索"

    def reload_backend(self):
        self.add_bubble("正在重新加载问答引擎…", "bot")
        self.app.load_qa_backend(notify=self._on_backend_loaded)

    def _on_backend_loaded(self, label, detail):
        color = COLOR_OK if self.app.qa_backend else COLOR_BAD
        self.backend_label.configure(text=f"检索后端：{label}", text_color=color)
        self.add_bubble(detail, "bot")

    def on_show(self):
        self.backend_label.configure(
            text=f"检索后端：{self.app.qa_label}", 
            text_color=COLOR_OK if self.app.qa_backend else COLOR_BAD,
        )


# ----------------------------------------------------------------------------
# 视图 3：图像识别
# ----------------------------------------------------------------------------
class RecognizeView(BaseView):
    title = "📷 图像识别"
    subtitle = "SIFT+VLAD / HOG / 轮廓 / 颜色矩 / GLCM 多特征融合 + 多尺度贝叶斯滑窗"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.image_path = None
        self._preview = None       # 防止 PhotoImage 被回收
        self.busy = False

        left = ctk.CTkFrame(self.body, width=430, corner_radius=12, fg_color=COLOR_PANEL)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        ctk.CTkButton(
            left, text="选择图片（PNG / JPG，≤2MB）", height=36, fg_color=COLOR_ACCENT,
            hover_color="#1f5bd0", font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            command=self.pick_image,
        ).pack(fill="x", padx=16, pady=(16, 10))

        self.preview = ctk.CTkLabel(
            left, text="尚未选择图片", width=380, height=280,
            fg_color="#101a30", corner_radius=10, text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.preview.pack(padx=16, pady=(0, 10))

        self.path_label = ctk.CTkLabel(
            left, text="", wraplength=380, justify="left", anchor="w",
            text_color=COLOR_MUTED, font=ctk.CTkFont(family=FONT, size=11),
        )
        self.path_label.pack(fill="x", padx=16)

        self.recognize_btn = ctk.CTkButton(
            left, text="开始识别（Top-3）", height=40, fg_color="#1d7a46",
            hover_color="#166138", font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
            state="disabled", command=self.recognize,
        )
        self.recognize_btn.pack(fill="x", padx=16, pady=(12, 8))

        self.feature_btn = ctk.CTkButton(
            left, text="特征提取自检（不需模型）", height=34, fg_color=COLOR_PANEL_2,
            hover_color="#2a3d63", font=ctk.CTkFont(family=FONT, size=12),
            state="disabled", command=self.feature_probe,
        )
        self.feature_btn.pack(fill="x", padx=16, pady=(0, 16))

        right = ctk.CTkFrame(self.body, corner_radius=12, fg_color=COLOR_PANEL)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        ctk.CTkLabel(
            right, text="模型状态", anchor="w",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
        ).pack(fill="x", padx=16, pady=(16, 6))

        self.model_frame = ctk.CTkFrame(right, fg_color="transparent")
        self.model_frame.pack(fill="x", padx=16)

        ctk.CTkLabel(
            right, text="识别结果", anchor="w",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
        ).pack(fill="x", padx=16, pady=(14, 6))

        self.result_frame = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 16))

        self.refresh_model_status()

    # -- 模型状态 ------------------------------------------------------------
    def missing_models(self):
        model_dir = PROJECT_ROOT / "models"
        return [n for n in MODEL_NAMES if not (model_dir / n).exists()]

    def refresh_model_status(self):
        for widget in self.model_frame.winfo_children():
            widget.destroy()
        model_dir = PROJECT_ROOT / "models"
        missing = self.missing_models()
        for name in MODEL_NAMES:
            exists = (model_dir / name).exists()
            ctk.CTkLabel(
                self.model_frame,
                text=f"{'✅' if exists else '❌'}  models/{name}",
                anchor="w", text_color=COLOR_OK if exists else COLOR_BAD,
                font=ctk.CTkFont(family=FONT, size=12),
            ).pack(fill="x")

        if missing:
            self.recognize_btn.configure(state="disabled")
            self.set_result(
                "⚠️ 识别模型缺失，无法执行图像识别。\n\n"
                "仓库 .gitignore 排除了 models/ 目录，需要自行训练：\n"
                "  1) 从 Kaggle 下载地标数据集，放到 data/train 与 data/valid\n"
                "     （目录结构：data/train/类别名/*.jpg，共 26 个类别）\n"
                "  2) 执行 python app/train.py\n"
                "  3) 回到本页面点「刷新模型状态」\n\n"
                f"缺失文件：{', '.join(missing)}\n\n"
                "可以先点左侧「特征提取自检」，验证特征链路是否正常。",
                warn=True,
            )
        else:
            self.set_result("模型齐备，选择图片后即可识别。")

    def set_result(self, text, warn=False):
        for widget in self.result_frame.winfo_children():
            widget.destroy()
        ctk.CTkLabel(
            self.result_frame, text=text, wraplength=520, justify="left", anchor="w",
            text_color="#f0c674" if warn else "#d6e2f5",
            font=ctk.CTkFont(family=FONT, size=12),
        ).pack(fill="x", padx=6, pady=6)

    # -- 选图与预览 ----------------------------------------------------------
    def pick_image(self):
        path = filedialog.askopenfilename(
            title="选择地标图片",
            filetypes=[("图片", "*.jpg *.jpeg *.png"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.image_path = path
        size_kb = os.path.getsize(path) / 1024
        self.path_label.configure(text=f"{path}\n（{size_kb:.0f} KB）")

        try:
            img = Image.open(path)
            img.thumbnail((380, 280))
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._preview = ctk_img
            self.preview.configure(image=ctk_img, text="")
        except Exception as exc:
            self.preview.configure(image=None, text=f"预览失败：{exc}")

        self.feature_btn.configure(state="normal")
        self.recognize_btn.configure(
            state="normal" if not self.missing_models() else "disabled"
        )
        if size_kb > 2048:
            self.set_result("⚠️ 图片超过 2MB，网页版会拒绝该文件；桌面版仍可尝试，但耗时较长。", warn=True)

    # -- 识别 ---------------------------------------------------------------
    def recognize(self):
        if self.busy or not self.image_path:
            return
        self.busy = True
        self.recognize_btn.configure(state="disabled", text="识别中…")
        self.set_result("正在执行多尺度贝叶斯滑窗推理，请稍候…")

        path = self.image_path

        def work():
            started = time.time()
            try:
                ImageRecognizer = ensure_import("app.image_recognizer").ImageRecognizer
                recognizer = ImageRecognizer()
                if recognizer.predictor is None:
                    raise RuntimeError("模型加载失败，请检查 models/ 目录")
                results = recognizer.predictor.predict(path, top_k=3, use_bayes=True)
                elapsed = time.time() - started
                payload = []
                for label, conf in results:
                    info = recognizer.target_info.get(label.lower(), {})
                    payload.append(
                        {
                            "label": label,
                            "name": info.get("name", label),
                            "confidence": float(conf),
                        }
                    )
                self.app.post(lambda: self._show_results(payload, elapsed))
            except Exception as exc:
                self.app.post(lambda e=exc: self._show_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_results(self, payload, elapsed):
        self.busy = False
        self.recognize_btn.configure(state="normal", text="开始识别（Top-3）")
        for widget in self.result_frame.winfo_children():
            widget.destroy()
        ctk.CTkLabel(
            self.result_frame,
            text=f"推理耗时 {elapsed:.2f}s · 贝叶斯多尺度滑窗",
            text_color=COLOR_MUTED, anchor="w", font=ctk.CTkFont(family=FONT, size=11),
        ).pack(fill="x", padx=6, pady=(4, 8))

        for i, item in enumerate(payload):
            row = ctk.CTkFrame(self.result_frame, fg_color=COLOR_PANEL_2, corner_radius=10)
            row.pack(fill="x", padx=6, pady=4)
            head = "🥇" if i == 0 else ("🥈" if i == 1 else "🥉")
            ctk.CTkLabel(
                row, text=f"{head} {item['name']}", anchor="w",
                font=ctk.CTkFont(family=FONT, size=15, weight="bold"),
            ).pack(fill="x", padx=12, pady=(8, 2))
            ctk.CTkLabel(
                row, text=f"标签 {item['label']} · 置信度 {item['confidence'] * 100:.1f}%",
                anchor="w", text_color=COLOR_MUTED,
                font=ctk.CTkFont(family=FONT, size=11),
            ).pack(fill="x", padx=12)
            bar = ctk.CTkProgressBar(row, height=8, progress_color=COLOR_ACCENT)
            bar.pack(fill="x", padx=12, pady=(6, 10))
            bar.set(max(0.0, min(1.0, item["confidence"])))

        if not payload:
            self.set_result("未能识别出地标。", warn=True)

    def _show_error(self, exc):
        self.busy = False
        self.recognize_btn.configure(state="normal", text="开始识别（Top-3）")
        self.set_result(f"❌ 识别失败：{exc}", warn=True)

    # -- 特征自检 -----------------------------------------------------------
    def feature_probe(self):
        if not self.image_path:
            return
        self.set_result("正在提取特征…")
        path = self.image_path

        def work():
            try:
                if not module_available("skimage"):
                    raise RuntimeError("缺少 scikit-image，请到「环境体检」安装")
                cv2 = ensure_import("cv2")
                ensure_import("numpy")
                features = ensure_import("app.image_recognizer")
                extract_spm_hog = features.extract_spm_hog
                extract_profile = features.extract_profile
                extract_color_moments = features.extract_color_moments
                extract_glcm = features.extract_glcm

                img = cv2.imread(path)
                if img is None:
                    raise RuntimeError("OpenCV 无法读取该图片")
                started = time.time()
                hog = extract_spm_hog(img, 256)
                profile = extract_profile(img, 20)
                color = extract_color_moments(img)
                glcm = extract_glcm(img)
                elapsed = time.time() - started
                vlad_dim = 64 * 128
                note = []
                if (PROJECT_ROOT / "models" / "kmeans_vlad.pkl").exists():
                    note.append("VLAD 码本已存在，可算出 VLAD 维度 " + str(vlad_dim))
                else:
                    note.append("VLAD 需 models/kmeans_vlad.pkl（训练后生成），当前缺失")
                total = vlad_dim + hog.shape[0] + profile.shape[0] + color.shape[0] + glcm.shape[0]
                text = (
                    f"图片尺寸：{img.shape[1]}×{img.shape[0]}\n"
                    f"HOG（Letterbox 256）：{hog.shape[0]} 维\n"
                    f"轴向轮廓：{profile.shape[0]} 维\n"
                    f"Lab 颜色矩：{color.shape[0]} 维\n"
                    f"GLCM 纹理：{glcm.shape[0]} 维\n"
                    f"—— 不含 PCA 的原始总维度：约 {total} 维\n"
                    f"HOG PCA 降维后（训练所得 512 主成分）总维度：约 {vlad_dim + 512 + 20 + 9 + 4} 维\n"
                    f"特征提取耗时：{elapsed:.2f}s\n\n"
                    + "\n".join(note)
                )
                self.app.post(lambda: self.set_result(text))
            except Exception as exc:
                self.app.post(lambda e=exc: self.set_result(f"❌ 特征自检失败：{e}", warn=True))

        threading.Thread(target=work, daemon=True).start()

    def on_show(self):
        self.refresh_model_status()


# ----------------------------------------------------------------------------
# 视图 4：环境体检
# ----------------------------------------------------------------------------
class EnvView(BaseView):
    title = "⚙️ 环境体检"
    subtitle = "依赖、模型、数据状态一览；可一键安装与下载"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.installing = False

        grid = ctk.CTkFrame(self.body, fg_color="transparent")
        grid.pack(fill="both", expand=True)

        # 左：依赖
        left = ctk.CTkFrame(grid, corner_radius=12, fg_color=COLOR_PANEL)
        left.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            left, text="Python 依赖", anchor="w",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
        ).pack(fill="x", padx=16, pady=(14, 4))
        self.dep_frame = ctk.CTkFrame(left, fg_color="transparent")
        self.dep_frame.pack(fill="x", padx=16)
        ctk.CTkButton(
            left, text="安装缺失依赖（清华源）", height=34, fg_color=COLOR_ACCENT,
            hover_color="#1f5bd0", font=ctk.CTkFont(family=FONT, size=12),
            command=self.install_deps,
        ).pack(fill="x", padx=16, pady=(10, 6))
        self.pip_log = ctk.CTkTextbox(
            left, height=150, wrap="word", fg_color="#0b1324",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.pip_log.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.pip_log.configure(state="disabled")

        # 右：模型与数据
        right = ctk.CTkFrame(grid, corner_radius=12, fg_color=COLOR_PANEL, width=430)
        right.pack(side="left", fill="both", padx=(12, 0))
        right.pack_propagate(False)

        ctk.CTkLabel(
            right, text="模型文件", anchor="w",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
        ).pack(fill="x", padx=16, pady=(14, 4))
        self.model_frame = ctk.CTkFrame(right, fg_color="transparent")
        self.model_frame.pack(fill="x", padx=16)
        ctk.CTkButton(
            right, text="下载向量模型 bge-small-zh-v1.5", height=34, fg_color=COLOR_PANEL_2,
            hover_color="#2a3d63", font=ctk.CTkFont(family=FONT, size=12),
            command=self.download_embedding,
        ).pack(fill="x", padx=16, pady=(10, 12))

        ctk.CTkLabel(
            right, text="数据与项目", anchor="w",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
        ).pack(fill="x", padx=16)
        self.data_label = ctk.CTkLabel(
            right, text="", anchor="w", justify="left", text_color="#d6e2f5",
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self.data_label.pack(fill="x", padx=16, pady=(4, 10))

        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.pack(fill="x", padx=16)
        ctk.CTkButton(
            actions, text="刷新状态", height=32, fg_color=COLOR_PANEL_2,
            hover_color="#2a3d63", font=ctk.CTkFont(family=FONT, size=12),
            command=self.refresh,
        ).pack(side="left", expand=True, fill="x")
        ctk.CTkButton(
            actions, text="打开项目目录", height=32, fg_color=COLOR_PANEL_2,
            hover_color="#2a3d63", font=ctk.CTkFont(family=FONT, size=12),
            command=self.open_project_dir,
        ).pack(side="left", expand=True, fill="x", padx=(8, 0))

        ctk.CTkLabel(
            right, text="说明", anchor="w",
            font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
        ).pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(
            right,
            text=(
                "· 问答：需 faiss + sentence-transformers + jieba 与\n"
                "  models/bge-small-zh；缺失时自动降级纯 BM25。\n"
                "· 识别：需 scikit-image 与 models/ 下 5 个 pkl；\n"
                "  这些文件被 .gitignore 排除，须跑 app/train.py 生成。\n"
                "· 训练数据（Kaggle）需自行放入 data/train、data/valid。"
            ),
            anchor="w", justify="left", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=11),
        ).pack(fill="x", padx=16, pady=(0, 14))

        self.refresh()

    # -- 状态刷新 -----------------------------------------------------------
    def refresh(self):
        for widget in self.dep_frame.winfo_children():
            widget.destroy()
        for mod, desc in OPTIONAL_DEPS:
            ok = module_available(mod)
            text = f"{'✅' if ok else '❌'} {mod}  {module_version(mod) if ok else '未安装'}   — {desc}"
            ctk.CTkLabel(
                self.dep_frame, text=text, anchor="w",
                text_color=COLOR_OK if ok else COLOR_BAD,
                font=ctk.CTkFont(family=FONT, size=12),
            ).pack(fill="x", pady=1)

        for widget in self.model_frame.winfo_children():
            widget.destroy()
        model_dir = PROJECT_ROOT / "models"
        for name in MODEL_NAMES:
            exists = (model_dir / name).exists()
            ctk.CTkLabel(
                self.model_frame, text=f"{'✅' if exists else '❌'}  models/{name}",
                anchor="w", text_color=COLOR_OK if exists else COLOR_BAD,
                font=ctk.CTkFont(family=FONT, size=12),
            ).pack(fill="x", pady=1)
        embed_ok = EMBED_DIR.exists() and any(EMBED_DIR.glob("*.json"))
        ctk.CTkLabel(
            self.model_frame,
            text=f"{'✅' if embed_ok else '❌'}  models/bge-small-zh（向量模型）",
            anchor="w", text_color=COLOR_OK if embed_ok else COLOR_BAD,
            font=ctk.CTkFont(family=FONT, size=12),
        ).pack(fill="x", pady=1)

        store = self.store
        grouped = store.grouped_total
        unmatched = store.kb_total - grouped
        self.data_label.configure(
            text=(
                f"地标：{len(store.landmarks)} 个\n"
                f"知识条目：{store.kb_total} 条（已按地标归组 {grouped} 条）\n"
                f"未归组：{unmatched} 条\n"
                f"已打卡：{len(self.app.checked)} 个\n"
                f"问答后端：{self.app.qa_label}\n"
                f"项目路径：{PROJECT_ROOT}"
            )
        )

    def open_project_dir(self):
        try:
            os.startfile(str(PROJECT_ROOT))  # noqa: S606 - Windows 专用
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    # -- 安装依赖 -----------------------------------------------------------
    def _log(self, line):
        self.pip_log.configure(state="normal")
        self.pip_log.insert("end", line)
        self.pip_log.see("end")
        self.pip_log.configure(state="disabled")

    def install_deps(self):
        if self.installing:
            return
        self.installing = True
        self._log("\n$ pip install jieba faiss-cpu sentence-transformers scikit-image\n")

        cmd = [
            sys.executable, "-m", "pip", "install",
            "-i", PIP_MIRROR,
            "jieba", "faiss-cpu", "sentence-transformers", "scikit-image",
        ]

        def work():
            try:
                creation = 0
                if os.name == "nt":
                    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1, creationflags=creation,
                )
                for line in proc.stdout:
                    self.app.post(lambda l=line: self._log(l))
                proc.wait()
                code = proc.returncode
                self.app.post(lambda: self._log(f"\n[安装结束] 退出码 {code}\n"))
                self.app.post(self.refresh)
            except Exception as exc:
                self.app.post(lambda e=exc: self._log(f"\n[安装失败] {e}\n"))
            finally:
                self.installing = False

        threading.Thread(target=work, daemon=True).start()

    # -- 下载向量模型 -------------------------------------------------------
    def download_embedding(self):
        if not module_available("huggingface_hub"):
            messagebox.showwarning("缺少依赖", "请先安装 sentence-transformers / huggingface_hub。")
            return
        self._log("\n$ 下载 BAAI/bge-small-zh-v1.5 → models/bge-small-zh\n")

        def work():
            try:
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                snapshot_download = ensure_import("huggingface_hub").snapshot_download

                path = snapshot_download(
                    "BAAI/bge-small-zh-v1.5",
                    local_dir=str(EMBED_DIR),
                )
                self.app.post(lambda: self._log(f"[完成] 模型已保存到 {path}\n"))
                self.app.post(self.refresh)
                self.app.post(lambda: self.app.load_qa_backend())
            except Exception as exc:
                self.app.post(lambda e=exc: self._log(f"[下载失败] {e}\n"))

        threading.Thread(target=work, daemon=True).start()

    def on_show(self):
        self.refresh()


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class TripScapeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("1400x880")
        self.minsize(1180, 720)
        self.configure(fg_color=COLOR_BG)

        self.ui_queue = queue.Queue()
        self.checked = load_checkins()
        self.store = DataStore().load()
        self.qa_backend = None
        self.qa_label = "初始化中…"
        self.views = {}
        self.current = None

        # 侧边栏
        side = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color="#0b1324")
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        ctk.CTkLabel(
            side, text="🌍 行旅识景", font=ctk.CTkFont(family=FONT, size=19, weight="bold"),
        ).pack(pady=(22, 2))
        ctk.CTkLabel(
            side, text="世界著名地标智能识别", text_color=COLOR_MUTED,
            font=ctk.CTkFont(family=FONT, size=11),
        ).pack(pady=(0, 18))

        self.nav_buttons = {}
        for idx, (key, label) in enumerate(VIEW_META):
            btn = ctk.CTkButton(
                side, text=label, anchor="w", height=38, corner_radius=8,
                fg_color="transparent", hover_color="#1b2947",
                font=ctk.CTkFont(family=FONT, size=13),
                command=lambda k=key: self.show_view(k),
            )
            btn.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[key] = btn
            self.bind(f"<Control-Key-{idx + 1}>", lambda _e, k=key: self.show_view(k))

        self.side_status = ctk.CTkLabel(
            side, text="问答：初始化中…", text_color=COLOR_MUTED, wraplength=180,
            justify="left", font=ctk.CTkFont(family=FONT, size=11),
        )
        self.side_status.pack(side="bottom", fill="x", padx=14, pady=14)

        # 内容区
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(side="left", fill="both", expand=True)

        for key, cls in (
            ("gallery", GalleryView),
            ("chat", ChatView),
            ("recognize", RecognizeView),
            ("env", EnvView),
        ):
            self.views[key] = cls(self.content, self)

        self.show_view("gallery")
        self.after(80, self._drain_queue)

        if self.store.error:
            self.views["chat"].add_bubble(f"数据加载提示：{self.store.error}", "error")

        self.load_qa_backend()

    # -- 线程安全的 UI 更新 --------------------------------------------------
    def post(self, fn):
        self.ui_queue.put(fn)

    def _drain_queue(self):
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:
                print(f"UI 回调出错: {exc}")
        self.after(60, self._drain_queue)

    # -- 视图切换 -----------------------------------------------------------
    def show_view(self, key):
        if self.current == key:
            return
        for k, view in self.views.items():
            if k == key:
                view.pack(fill="both", expand=True)
            else:
                view.pack_forget()
        for k, btn in self.nav_buttons.items():
            btn.configure(
                fg_color=COLOR_ACCENT if k == key else "transparent",
                text_color="#ffffff" if k == key else "#c9d6ea",
            )
        self.current = key
        self.views[key].on_show()

    # -- 问答引擎加载 -------------------------------------------------------
    def load_qa_backend(self, notify=None):
        self.qa_label = "加载中…"
        self.side_status.configure(text="问答：加载中…", text_color=COLOR_MUTED)

        def work():
            label = None
            detail = ""
            backend = None
            if module_available("faiss") and module_available("sentence_transformers") and module_available("jieba"):
                try:
                    ensure_import("app.qa_engine")
                    backend = RagBackend(self.store.alias_pairs)
                    label = "RAG 向量 + BM25 混合检索"
                    detail = (
                        f"RAG 引擎就绪：已索引 {len(backend.docs)} 条知识条目，"
                        f"向量模型 {EMBED_DIR.name}。"
                    )
                except Exception as exc:
                    detail = f"RAG 引擎不可用，已降级为纯 BM25（原因：{exc}）"
            else:
                missing = [
                    m
                    for m in ("faiss", "sentence_transformers", "jieba")
                    if not module_available(m)
                ]
                detail = f"缺少依赖 {', '.join(missing)}，已降级为纯 BM25 关键词检索。"

            if backend is None:
                documents = []
                for item in self.store.landmarks:
                    documents.extend(item["entries"])
                if not documents:
                    label = "无可用数据"
                    detail = "知识库为空，请检查 data/landmark.db"
                else:
                    backend = Bm25Backend(
                        documents, self.store.by_name, self.store.alias_pairs
                    )
                    label = "纯 BM25 关键词检索（降级）"

            def done():
                self.qa_backend = backend
                self.qa_label = label or "不可用"
                color = COLOR_OK if backend and label.startswith("RAG") else "#f0c674"
                self.side_status.configure(text=f"问答：{self.qa_label}", text_color=color)
                if notify:
                    notify(self.qa_label, detail)
                self.views["chat"].on_show()
                self.views["env"].refresh()

            self.post(done)

        threading.Thread(target=work, daemon=True).start()


def main():
    if not os.environ.get("TRIPSCAPE_GUI_SKIP_PRELOAD"):
        splash = _Splash()
        started = time.time()
        try:
            results = preload_heavy_imports(progress=splash.set_status)
        finally:
            splash.close()
        loaded = sum(1 for value in results.values() if value.startswith("OK"))
        print(
            f"[启动] 预加载完成：{loaded}/{len(results)} 个模块，"
            f"耗时 {time.time() - started:.1f}s"
        )
        for name, value in results.items():
            if not value.startswith("OK"):
                print(f"[启动]   {name}: {value}")

    app = TripScapeApp()
    start = os.environ.get("TRIPSCAPE_GUI_START")
    if start in app.views:
        app.show_view(start)

    if os.environ.get("TRIPSCAPE_GUI_SMOKE"):
        ms = int(os.environ.get("TRIPSCAPE_GUI_SMOKE_MS", "3000"))
        for key in ("gallery", "chat", "recognize", "env"):
            app.after(200, lambda k=key: app.show_view(k))
        app.after(ms, app.destroy)
        app.mainloop()
        print("GUI smoke OK")
        return

    app.mainloop()


if __name__ == "__main__":
    main()
