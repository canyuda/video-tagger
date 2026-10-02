# AGENTS.md — 视频内容分析桌面应用

Windows + macOS 桌面应用，两阶段交付：**V1 视频分类工具**（导入本地/抖音视频 → 自动打类型标签 → 任务库筛选/统计）→ **V2 内容分析**（云端 ASR + 关键帧 + 多模态 VLM 生成结构化报告）。全云端模型，不使用任何本地模型。设计文档在 [docs/](docs/)，调研定稿于 2026-10-02。

## 当前状态与职责边界

- **本目录是正式项目**：一切实现工作在本目录的项目会话进行（覆盖工作区根 AGENTS.md「工作区会话只调研不写码」的边界——那是对调研会话的约束）。
- 当前只有 docs/ 设计文档，**源码未开始、git 未初始化**（首个 commit 不得含任何密钥，`git status` 看不到 `.env.local` 即验证通过）。
- 下一步：Phase 0 分类内核 CLI（Python），任务卡与实测清单见 docs/07 §5；openspec/ 已初始化（opsx 变更工作流），暂无活动 change。
- 分类评测集：`eval-videos/`（git 忽略的本地数据）——视频 `video-NN.mp4`、`mapping.csv`（aweme_id 溯源）、`labels.csv`（ground truth，name_en 键；草稿需人工确认）、`README.md`（构成与缺口）。

## 文档导航（开工必读）

- **[docs/00-决策记录.md](docs/00-决策记录.md)**：全部已拍板决策 D01–D55 的单点索引。**要推翻任何决策，先更新该文件（标注日期与原因）再动代码。**
- **[docs/07-项目会话交接.md](docs/07-项目会话交接.md)**：凭据位置、双机同步注意、Phase 0 任务卡、已挂账待决事项（D13/D37/D04/D47/D48——勿重复讨论）。
- 专项：docs/02 阶段计划与验收标准；docs/03 §3.0 分类方案（核心）；docs/04 架构/数据模型/建议仓库结构（§9）；docs/05 风险清单；docs/06 UI 设计（§7 设计工具）。

## 工程规范（跨机生效，继承用户全局配置）

- 交流用中文；代码与注释用英文。
- fail fast：错误尽早暴露，禁止吞异常；业务校验不静默降级。
- 优先函数式编程范式；简洁优于巧妙，可读性第一。
- 禁止硬编码：provider base_url、模型名、中间件配置一律进配置/设置存储。
- 时间戳 UTC 存储、本地时区显示；外部 API 重试一律指数退避 + 抖动（最多 3 次）；SQLite 开 WAL。
- 错误分 `retryable` / `terminal` 两类（带前缀错误码）。

## 架构与技术约束（出处 docs/00 / 03 / 04）

- 技术栈：Tauri 2 壳（尽量薄）+ React 前端 + Python sidecar（FastAPI，PyInstaller 单文件，127.0.0.1 随机端口 + 一次性 token 握手）+ ffmpeg（仅 LGPL 构建分发）。
- 建议仓库结构：`src-tauri/`（壳）、`src/`（前端）、`sidecar/`（Python 管线，模块划分见 docs/04 §3）、`shared/`（契约，TS + Pydantic 双生成）、`scripts/`。
- provider 分两类接口：`chat`（VLM/ASR，OpenAI 兼容）与 `decision`（Jev/typesafe.ai）；决策层可整体切换降级为「VLM 直出」并标记。
- 分类两层：感知层 flash 档 VLM 输出英文 `observations_en`（半受控 schema：~20 个受控枚举标签 + free_note）→ 决策层 Jev（**只收文本、中文精度低，state/criteria 全英文**）。
- taxonomy：`[{name, name_en, worthy, hint, hint_en?}]` + 版本号。**`name_en` 是稳定键**——Jev 选项名、SQLite/CSV/标注样本库一律存 name_en，中文 name 仅 UI 显示。决策层只判类型，「是否有价值」由配置映射（改 worthy 即时生效不重跑模型；缓存键含 taxonomy 版本）。
- V1 冻结三个契约（Phase 0.5）：任务事件流（SSE）、taxonomy 结构、observations schema。
- 任务状态机：V1 终态 `classified`（每条都出结果，无中断语义）；`cancelled` 是用户取消终态；**20 分钟时长上限仅约束 V2 分析**，V1 分类不限时长。
- 缓存键 `file_hash + stage + version`：换提示词/模型重新分析不重跑转写与抽帧。存储一律以 file_hash 命名（原名仅显示）；本地文件引用原路径不复制；导入去重 `file_hash` / `aweme_id`。
- 抖音下载隔离为**可整体替换的适配器**，失败只停 downloading 阶段，绝不影响本地文件链路。
- 测试四层（docs/04 §7）：**CI 只跑零成本层**（纯函数单测 + provider 契约 fixture 回放）；评测集回归（24 个标注样本，真调 API）手动触发。
- UI 中文；UI/交互设计产出统一用 ui-ux-pro-max 插件（D55），不改变 docs/06 §6 的验收标准。

## 凭据与安全

- `.env.local`（权限 600，git 已忽略）存全部 key：DASHSCOPE_API_KEY（必填）、TYPESAFE_API_KEY（Jev 决策层）、ZHIPU_API_KEY、ARK_API_KEY、OSS_*（**默认不启用**，现指向团队测试桶，勿写）、DOUYIN_COOKIE。字段说明见 `.env.example`。
- **被 .gitignore 排除的文件（尤其 `.env.local`）禁止读取、禁止入代码/日志/文档/git。**
- key 读取顺序：环境变量 → `.env.local`，缺失即启动报错。
- 双机开发：家庭 Mac 开发、公司 Windows 真机验证；公司机无 `.env.local`，需加密渠道手动同步。
- 开工第一件事：各 key 发最小请求冒烟验证（docs/07 §3）。
