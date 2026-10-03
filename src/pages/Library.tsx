import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { api, type Task } from "../api";
import type { Taxonomy } from "../contracts";

const STATUS_ZH: Record<string, string> = {
  created: "排队中",
  probing: "正在探测",
  classifying: "正在分类",
  classified: "已分类",
  rejected: "已拒绝",
  cancelled: "已取消",
  failed: "失败",
};

export default function Library({
  tasks,
  onRefresh,
  onOpen,
  douyinEnabled,
}: {
  tasks: Task[];
  onRefresh: () => void;
  onOpen: (id: string) => void;
  douyinEnabled: boolean;
}) {
  const [typeFilter, setTypeFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [worthyOnly, setWorthyOnly] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [toast, setToast] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [showedPrivacy, setShowedPrivacy] = useState(
    localStorage.getItem("vt-privacy-toast") === "1",
  );

  useEffect(() => {
    api.taxonomy().then(setTaxonomy).catch(() => {});
  }, []);

  // Global drag & drop (Tauri payload carries real file paths).
  useEffect(() => {
    const unlisten = listen<{ paths: string[] }>("tauri://drag-drop", async (event) => {
      const paths = (event.payload.paths || []).filter((p) => /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(p));
      if (!paths.length) return;
      await importFiles(paths);
    });
    return () => {
      unlisten.then((f) => f());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (showedPrivacy) return;
    setToast("分类需将抽样帧上传云端模型，详见设置·隐私说明");
    localStorage.setItem("vt-privacy-toast", "1");
    const t = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(t);
  }, [showedPrivacy]);

  async function importFiles(paths: string[]) {
    try {
      const result = await api.importLocal(paths);
      if (result.missing.length) {
        setToast(`${result.missing.length} 个文件不存在，已跳过`);
        setTimeout(() => setToast(""), 4000);
      }
      if (!showedPrivacy) setShowedPrivacy(true);
      onRefresh();
    } catch (e) {
      setToast(`导入失败: ${e}`);
      setTimeout(() => setToast(""), 4000);
    }
  }

  async function quickAnalyze(id: string) {
    try {
      const rec = await api.getRecommendation(id);
      await api.analyze(id, {
        template_id: rec.template_id,
        model: rec.model,
      });
      setToast("已按分类推荐开始分析（转写约 1 分钟起）");
      setTimeout(() => setToast(""), 4000);
      onRefresh();
    } catch (e) {
      setToast(`分析失败: ${e}`);
      setTimeout(() => setToast(""), 4000);
    }
  }

  async function importLinks() {
    const links = pasteText.split(/\n+/).map((s) => s.trim()).filter(Boolean);
    if (!links.length) return;
    try {
      await api.importDouyin(links);
      setPasteText("");
      setPasteOpen(false);
      onRefresh();
    } catch (e) {
      setToast(`导入失败: ${e}`);
      setTimeout(() => setToast(""), 4000);
    }
  }

  const typeOptions = useMemo(() => {
    const set = new Set(tasks.map((t) => t.result_type).filter(Boolean) as string[]);
    return Array.from(set).sort();
  }, [tasks]);

  const filtered = useMemo(() => {
    const unworthy = new Set(
      (taxonomy?.types || []).filter((t) => !t.worthy).map((t) => t.name_en || t.name),
    );
    return tasks.filter((t) => {
      if (typeFilter && t.result_type !== typeFilter) return false;
      if (sourceFilter && t.source_type !== sourceFilter) return false;
      if (worthyOnly && t.result_type && unworthy.has(t.result_type)) return false;
      if (keyword && !(t.file_path || "").toLowerCase().includes(keyword.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [tasks, typeFilter, sourceFilter, worthyOnly, keyword, taxonomy]);

  const stats = useMemo(() => {
    // 分析视角的统计（D58）：分类只是素材属性，不再是主页主统计。
    let done = 0, analyzable = 0, noValue = 0, busy = 0;
    const unworthy = new Set(
      (taxonomy?.types || []).filter((t) => !t.worthy).map((t) => t.name_en || t.name),
    );
    for (const t of tasks) {
      if (t.status === "done") done += 1;
      if (["extracting", "transcribing", "sampling", "analyzing"].includes(t.status)) busy += 1;
      if (t.result_type) {
        if (unworthy.has(t.result_type)) noValue += 1;
        else analyzable += 1;
      }
    }
    return { done, analyzable, noValue, busy };
  }, [tasks, taxonomy]);

  const activeCount = tasks.filter((t) =>
    ["created", "probing", "classifying"].includes(t.status),
  ).length;

  return (
    <div
      className="library"
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
      }}
    >
      {dragOver && <div className="drop-overlay">松开以导入视频文件</div>}
      <div className="toolbar">
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">全部类型</option>
          {typeOptions.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
          <option value="">全部来源</option>
          <option value="local">本地</option>
          <option value="douyin">抖音</option>
        </select>
        <label className="check">
          <input
            type="checkbox"
            checked={worthyOnly}
            onChange={(e) => setWorthyOnly(e.target.checked)}
          />
          仅有价值
        </label>
        <input
          placeholder="按文件名筛选…"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <div className="spacer" />
        <span className="muted">{activeCount > 0 ? `${activeCount} 个任务进行中` : ""}</span>
        <button
          disabled={!douyinEnabled}
          title={douyinEnabled ? "" : "需先在设置中配置抖音 Cookie"}
          onClick={() => setPasteOpen((v) => !v)}
        >
          粘贴链接
        </button>
        <button onClick={onRefresh}>刷新</button>
        <a className="button" href={api.csvUrl()}>
          导出 CSV
        </a>
      </div>

      {pasteOpen && (
        <div className="paste-box">
          <textarea
            rows={3}
            placeholder="每行一个抖音链接或分享口令（支持 v.douyin.com 短链）…"
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
          />
          <button className="primary" onClick={importLinks}>导入链接</button>
        </div>
      )}

      <div className="stats-bar">
        {tasks.length === 0 ? (
          <span className="muted">导入视频后，分类将自动判断分析价值</span>
        ) : (
          <>
            <span className="chip ok">可分析 {stats.analyzable}</span>
            <span className="chip dim">无价值 {stats.noValue}</span>
            <span className="chip accent">分析中 {stats.busy}</span>
            <span className="chip accent">已出报告 {stats.done}</span>
          </>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="empty">
          <div className="empty-card">
            <h3>拖入视频文件</h3>
            <p>支持批量拖拽到窗口任意位置</p>
          </div>
          <div className="empty-card dim">
            <h3>粘贴抖音链接</h3>
            <p>（Phase 2 开放）</p>
          </div>
        </div>
      ) : (
        <div className="grid">
          {filtered.map((t) => (
            <TaskCard
              key={t.id}
              task={t}
              onOpen={() => onOpen(t.id)}
              onStartAnalysis={() => quickAnalyze(t.id)}
            />
          ))}
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function TaskCard({
  task,
  onOpen,
  onStartAnalysis,
}: {
  task: Task;
  onOpen: () => void;
  onStartAnalysis: () => void;
}) {
  const name = (task.file_path || "").split("/").pop() || task.file_path;
  const statusZh = STATUS_ZH[task.status] || task.status;
  const analyzing = ["extracting", "transcribing", "sampling", "analyzing"].includes(task.status);
  const analyzed = task.status === "done";
  // 分析视角的卡片（D58）：主按钮是「分析」，分类结果只是一个小角标。
  return (
    <div className={`card ${analyzing ? "busy" : ""}`} onClick={onOpen}>
      <div className="card-head">
        <span className={`badge src-${task.source_type}`}>
          {task.source_type === "douyin" ? "抖音" : "本地"}
        </span>
        {task.result_type && <span className="badge dim-badge">{task.result_type}</span>}
        {task.status === "done" && <span className="badge ok-badge">已出报告</span>}
      </div>
      <div className="card-title" title={name}>{name}</div>
      <div className={`card-status st-${task.status}`}>
        {analyzed
          ? "报告已生成"
          : analyzing
            ? "分析中…"
            : statusZh}
      </div>
      {task.status === "failed" && task.error_stage && (
        <div className="card-error">失败阶段：{stageZh(task.error_stage)}</div>
      )}
      {task.status === "rejected" && task.error_msg && (
        <div className="card-error">{task.error_msg}</div>
      )}
      {!analyzing && ["classified", "done", "no_value"].includes(task.status) && (
        <button
          className="primary card-action"
          onClick={(e) => {
            e.stopPropagation();
            onStartAnalysis();
          }}
        >
          {analyzed ? "重新分析" : "开始分析"}
        </button>
      )}
    </div>
  );
}

function stageZh(stage: string): string {
  const map: Record<string, string> = {
    probing: "探测",
    classifying: "分类",
    downloading: "下载",
  };
  return map[stage] || stage;
}
