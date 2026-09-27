# 行旅识景 · 桌面版 GUI

两个入口，共用同一套数据层与问答逻辑：

| 入口 | 技术 | 说明 |
| --- | --- | --- |
| **`gui_qt.py`（推荐）** | PySide6 + 内嵌 Chromium | 一个窗口里既有**原版网页（HTML/JS/CSS 一行未改）**，也有原生视图 |
| `gui.py` | customtkinter | 只有原生视图，不依赖 Chromium；没有 Three.js 3D 地球 |

```bash
cd Application/TripScape
python gui_qt.py        # 推荐
python gui.py           # tkinter 版
```

## gui_qt.py：把原版网页装进 GUI

侧边栏 5 个页签：

| 页签 | 内容 |
| --- | --- |
| 🌍 **网页版原貌** | 内嵌 QtWebEngine(Chromium) 直接加载 `templates/index.html` 与 `templates/app.html`。Three.js 3D 地球、OrbitControls 旋转缩放、点击光点打卡、点击聚焦动画、粒子星空、浮动聊天窗、识别可信度进度条——全部按原样运行。工具栏可切「入口页 /」「应用页 /app」「刷新」「在系统浏览器打开」。 |
| 🗺 **地标图鉴** | 原生地图打卡 + 离线浏览 217 条知识库问答（网页版没有的能力）。 |
| 💬 **AI 问答** | 走内嵌 Flask 的 `/api/chat`，与网页版共用同一个 RAG 实例。 |
| 📷 **图像识别** | 走内嵌 Flask 的 `/api/recognize`；模型缺失时给出缺失清单，另有不依赖模型的特征提取自检。 |
| ⚙️ **环境体检** | 依赖 / 模型 / 数据状态，一键安装依赖、下载向量模型、打开项目目录。 |

**架构**：进程内后台线程按文件路径加载 `app.py`（不能 `import app`，同目录的 `app/` 包会优先匹配），
关闭 Werkzeug 重载器（否则会 fork 出第二份模型，约 1.5GB），Flask 以 `threaded=True` 跑在
`127.0.0.1:<端口>`（默认 5000，被占用则自动挑空闲端口）。内嵌 Chromium 与原生问答/识别
都打这个地址，**模型只加载一份**。

`templates/` 下的 HTML 未做任何改动，可用 `git status` 验证（不会出现在修改列表里）。

### 几个使用上的注意点

- 启动时先在主线程预加载 `torch` 等重依赖（闪屏提示，约 15s），随后内嵌 Flask 再加载向量模型；
  状态栏会显示「正在启动内嵌 Flask…」直到就绪。
- 网页页签**不可见时 `requestAnimationFrame` 会暂停**，3D 地球停止自转（切回来即恢复），这是浏览器行为，不是故障。
- `app.html` 自带一个 JS 报错：`scene.add(new THREE.DirectionalLight('#FFFFFF', 0.3).position.set(-10, -5, -10))`
  把 `position.set()` 的返回值（Vector3）当成对象塞进场景，控制台会打印
  `THREE.Object3D.add: object not an instance of THREE.Object3D`。这是**原版缺陷**，为保持「原样不动」未作修改。
- 网页版的打卡状态只存在页面内存里（刷新即丢）；原生图鉴页的打卡持久化到 `data/checkins.json`。两者互不影响。

## gui.py（tkinter 版）

只有原生视图，3D 地球换成等距圆柱投影的静态地图。适合不想装 PySide6 的场景；
`selftest.py` 针对这一版。

## 首次准备

```bash
# GUI 本体
python -m pip install customtkinter pillow            # tkinter 版
python -m pip install PySide6                         # Qt 版（含 QtWebEngine，约 245MB 下载）

# 问答与识别依赖
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    jieba faiss-cpu sentence-transformers scikit-image

# 向量模型（约 95MB）；也可点「环境体检 → 下载向量模型」
set HF_ENDPOINT=https://hf-mirror.com
python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5', local_dir='models/bge-small-zh')"
```

内嵌网页需要联网加载 `unpkg.com` 上的 Three.js（`index.html` 的地球贴图来自 `threejs.org`，
`app.html` 用的是本地 `static/earth.png`）。

**图像识别还需要自行训练模型**：`.gitignore` 排除了 `models/`，Kaggle 数据集也不在仓库里，
需要先准备 `data/train/<类别名>/*.jpg`、`data/valid/...`，再执行 `python app/train.py` 生成 5 个 pkl。
在此之前识别页会明确提示缺少哪些文件，其余页签不受影响。

## 依赖缺失时的行为

| 缺失项 | 影响 |
| --- | --- |
| `PySide6` | `gui_qt.py` 无法启动（`gui.py` 仍可用） |
| `customtkinter` | `gui.py` 与 `selftest.py` 无法启动 |
| `jieba` / `faiss` / `sentence-transformers` / `models/bge-small-zh` | 内嵌 Flask 的问答引擎初始化失败，`/api/chat` 返回兜底文案 |
| `scikit-image` | 特征自检与识别不可用 |
| `models/*.pkl` | 图像识别不可用，页面显示缺失清单与训练步骤 |

## 自检

```bash
python selftest_qt.py     # Qt 版：用 runJavaScript 探测内嵌网页里的真实 DOM + 原生视图断言
python selftest.py        # tkinter 版
```

`selftest_qt.py` 会实际加载 `/app`，然后探测：`canvas` 是否存在、WebGL 上下文是否可用、
浮动聊天窗 `#chatFloat`、打卡卡片 `#spotCard`、CSS2D 点击点位是否挂载、页面自己读 `spots.csv` 是否 26 行；
再加上图鉴列表/地图点位/打卡持久化、`/api/chat` 问答正确性、识别页模型状态、特征自检、页签切换。

## 共享代码与顺带修掉的三个问题

`gui_qt.py` 通过 `import gui as core` 复用与界面无关的部分（`DataStore`、BM25、问答后端、
相关性护栏、重依赖预加载、打卡存储），因此两个界面的数据与检索行为完全一致。

1. **知识条目归组漏配**：`knowledge_base` 的问法没统一用 `heritage_items` 的地标名
   （`救世基督像` vs `基督救世主像`/`基督像`；`吉萨金字塔群` vs `吉萨金字塔`/`胡夫金字塔`；
   `库库尔坎金字塔` 属奇琴伊察、`千寻塔` 属崇圣寺三塔），原按全名匹配会漏 20 条、2 个地标空白。
   现按“最长别名优先”归组，217 条全部落位。
2. **超纲问题被强行匹配**：`qa_engine.hybrid_search` 对向量分与 BM25 分各自做 min-max 归一化，
   最高分恒为 1.0，`format_answer` 的 `score_threshold=0.2` 形同虚设——「故宫在哪里？」会答
   「布达拉宫在哪里？」。GUI 侧加了 `RelevanceGuard`：只有提问点到命中条目所属的地标（含别名）
   才认可命中。注意：**内嵌网页走的是原版 `/api/chat`，没有这层护栏**，网页版仍会出现上述近似误答。
3. **多线程首次导入死锁**：`torch` / `transformers` / `re` 等在多个线程中同时首次导入会卡在
   CPython 导入锁上，表现为界面永远停在「加载中…」。现在启动时主线程预加载，线程内导入统一走
   `ensure_import` 串行化。

另外为让 GUI 轻量启动，把 `app/__init__.py` 改成 PEP 562 惰性导入
（`from app import ImageRecognizer, QASystem` 的旧写法保持兼容），并给 `.gitignore` 加了
`data/checkins.json`（桌面版打卡记录属个人运行时状态）。

## 已知限制

- 图像识别模型未随仓库提供，需自行训练 `models/*.pkl`。
- 内嵌网页依赖公网 CDN（Three.js）；离线时地球渲染不出来，其它功能正常。
- 网页版与原生图鉴的打卡状态互不相通，网页版的打卡刷新即丢。
- 原生图鉴的问答护栏按「地标归属」判断：若提问只给别名之外的线索（如只问「斯尼夫鲁是谁？」），
  即使知识库里有相关条目也会返回兜底文案。
