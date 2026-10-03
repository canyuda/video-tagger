import { useEffect, useMemo, useState } from "react";
import { api, type Task } from "../api";

const PROGRESS_ZH: Record<string, string> = {
  extracting: "抽帧",
  transcribing: "云端转写",
  sampling: "关键帧",
  analyzing: "生成报告",
  downloading: "下载",
  probing: "探测",
  classifying: "分类",
};

// 分析主页（D58：分析是产品主线）。列出进行中的分析与全部报告；
// 素材的导入与分类在「素材库」。
export default function Analyses({ onOpen }: { onOpen: (id: string) => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [emptyHint, setEmptyHint] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const all = await api.listTasks();
        if (alive) setTasks(all);
      } catch {
        /* sidecar restarting */
      }
    };
    tick();
    const timer = setInterval(tick, 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const running = useMemo(
    () => tasks.filter((t) =>
      ["extracting", "transcribing", "sampling", "analyzing", "downloading"].includes(t.status),
    ),
    [tasks],
  );
  const reports = useMemo(
    () => tasks.filter((t) => t.status === "done"),
    [tasks],
  );

  useEffect(() => {
    if (tasks.length > 0) setEmptyHint(false);
  }, [tasks]);

  if (running.length === 0 && reports.length === 0) {
    return (
      <div className="analyses">
        <div className="empty-card">
          <h3>还没有分析</h3>
          <p>到「素材库」导入视频并开始分析；分类会自动判断价值并推荐提示词与模型。</p>
          {emptyHint && <p className="muted">（暂无任何素材）</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="analyses">
      {running.length > 0 && (
        <>
          <h3 className="section">进行中（{running.length}）</h3>
          <div className="run-list">
            {running.map((t) => (
              <div key={t.id} className="run-card" onClick={() => onOpen(t.id)}>
                <div className="run-title">{fileName(t.file_path)}</div>
                <div className="run-stage">
                  <span className="spinner" /> {PROGRESS_ZH[t.status] || t.status}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      <h3 className="section">分析报告（{reports.length}）</h3>
      {reports.length === 0 ? (
        <p className="muted">暂无报告——对「素材库」中的素材点「开始分析」生成。</p>
      ) : (
        <div className="grid">
          {reports.map((t) => (
            <ReportCard key={t.id} task={t} onOpen={() => onOpen(t.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ReportCard({ task, onOpen }: { task: Task; onOpen: () => void }) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  useEffect(() => {
    api
      .getReport(task.id)
      .then((r) => {
        setTitle(r.report_json.title || "");
        setSummary(r.report_json.summary || "");
      })
      .catch(() => {});
  }, [task.id]);
  return (
    <div className="card report-card" onClick={onOpen}>
      <div className="card-head">
        <span className="badge type">{task.result_type || "已分析"}</span>
      </div>
      <div className="card-title">{title || fileName(task.file_path)}</div>
      <p className="report-summary">{summary.slice(0, 80)}{summary.length > 80 ? "…" : ""}</p>
      <div className="muted">{day(task.updated_at)}</div>
    </div>
  );
}

function fileName(path: string | null): string {
  return (path || "").split("/").pop() || path || "";
}

function day(iso: string): string {
  return (iso || "").slice(0, 10);
}
