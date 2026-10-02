import { useEffect, useState } from "react";
import {
  api,
  taskEventsUrl,
  type ClassifyResult,
  type FrameRef,
  type ReportView,
  type Task,
} from "../api";

interface Template {
  id: string;
  name: string;
  builtin: boolean;
  user_layer: string;
}

export default function TaskDetail({
  taskId,
  onClose,
}: {
  taskId: string;
  onClose: () => void;
}) {
  const [task, setTask] = useState<Task | null>(null);
  const [result, setResult] = useState<ClassifyResult | null>(null);
  const [frames, setFrames] = useState<FrameRef[]>([]);
  const [error, setError] = useState("");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("tpl-general");
  const [instructions, setInstructions] = useState("");
  const [report, setReport] = useState<ReportView | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    let closed = false;
    const load = async () => {
      try {
        const t = await api.getTask(taskId);
        if (closed) return;
        setTask(t);
        if (t.status === "classified") {
          setResult(await api.getResult(taskId));
          setFrames(await api.listFrames(taskId));
        }
        if (["extracting", "transcribing", "sampling", "analyzing"].includes(t.status)) {
          setAnalyzing(true);
        }
        if (t.status === "done") {
          setReport(await api.getReport(taskId));
        }
      } catch (e) {
        if (!closed && String(e).includes("404")) {
          /* no report yet */
        } else if (!closed) {
          setError(String(e));
        }
      }
    };
    load();
    api.templates().then(setTemplates).catch(() => {});
    const es = new EventSource(taskEventsUrl(taskId));
    es.addEventListener("done", () => {
      setAnalyzing(false);
      load();
    });
    es.addEventListener("error", () => load());
    return () => {
      closed = true;
      es.close();
    };
  }, [taskId]);

  const startAnalysis = async (override = false) => {
    setAnalyzing(true);
    try {
      await api.analyze(taskId, { template_id: templateId, instructions, override });
    } catch (e) {
      setAnalyzing(false);
      setError(String(e));
    }
  };

  const top3 = result
    ? Object.entries(result.probabilities)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
    : [];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{task ? (task.file_path || "").split("/").pop() : taskId}</h3>
          <button className="close" onClick={onClose}>✕</button>
        </div>

        {error && <div className="error-box">{error}</div>}
        {!task && !error && <div className="muted">加载中…</div>}

        {task && (
          <>
            <div className="detail-row">
              <span>状态</span>
              <b>{statusZh(task.status)}</b>
            </div>
            {task.status === "rejected" && task.error_msg && (
              <div className="error-box">{task.error_msg}</div>
            )}
            {task.status === "failed" && (
              <div className="error-box">
                失败阶段：{task.error_stage}
                <div className="muted">{task.error_msg}</div>
                <button onClick={() => api.retry(task.id).then(() => onClose())}>
                  重试本阶段
                </button>
              </div>
            )}

            {result && (
              <>
                <div className="detail-row">
                  <span>类型</span>
                  <b>
                    {result.type_name}（{result.type}）
                    {result.analysis_worthy ? " · 有价值" : " · 无价值"}
                  </b>
                </div>
                <div className="detail-row">
                  <span>置信度</span>
                  <b>
                    {result.confidence.toFixed(2)}
                    <span className="muted">
                      {" "}
                      · 判定层：{result.degraded ? "VLM 直出（降级）" : "Jev"}
                    </span>
                  </b>
                </div>
                {top3.length > 1 && (
                  <div className="prob-dist">
                    {top3.map(([type, p]) => (
                      <div key={type} className="prob-row">
                        <span className="prob-name">{type}</span>
                        <div className="prob-bar">
                          <div className="prob-fill" style={{ width: `${p * 100}%` }} />
                        </div>
                        <span className="prob-val">{p.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="detail-block">
                  <div className="label">证据摘要</div>
                  <p>{result.summary_zh}</p>
                </div>
                <div className="detail-block">
                  <div className="label">观察标签</div>
                  <div className="tags">
                    {result.observations.map((o) => (
                      <span key={o} className="tag">{o}</span>
                    ))}
                  </div>
                </div>
                {result.l2_triggered && (
                  <div className="detail-block">
                    <div className="label">L2 音频补证（已触发）</div>
                    {result.l2_transcript_excerpt && (
                      <p className="excerpt">{result.l2_transcript_excerpt}</p>
                    )}
                  </div>
                )}
                {result.other_guess && (
                  <div className="detail-block">
                    <div className="label">other_guess</div>
                    <p>{result.other_guess}</p>
                  </div>
                )}
                {frames.length > 0 && (
                  <div className="detail-block">
                    <div className="label">感知层抽样帧（模型看到了什么）</div>
                    <div className="frames">
                      {frames.map((f) => (
                        <img key={f.index} src={api.mediaUrl(f.url)} alt={f.name} />
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}

            <div className="modal-actions">
              {["classified", "done", "no_value", "failed", "rejected", "cancelled"].includes(
                task.status,
              ) && (
                <button onClick={() => api.reclassify(task.id).then(() => onClose())}>
                  重新分类
                </button>
              )}
              {["created", "probing", "classifying"].includes(task.status) && (
                <button onClick={() => api.cancel(task.id).then(() => onClose())}>
                  取消任务
                </button>
              )}
              <button
                className="danger"
                onClick={() => {
                  if (confirm("删除任务将连带清理视频帧缓存，确定？")) {
                    api.remove(task.id).then(() => onClose());
                  }
                }}
              >
                删除
              </button>
            </div>

            {task.status === "no_value" && (
              <div className="novalue-box">
                判定为无分析价值（{task.result_type}）。
                <button onClick={() => startAnalysis(true)}>仍然分析</button>
              </div>
            )}

            {!analyzing && ["classified", "done", "no_value"].includes(task.status) && (
              <div className="analysis-panel">
                <div className="label">内容分析（V2）</div>
                <div className="row">
                  <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                    {templates.map((tpl) => (
                      <option key={tpl.id} value={tpl.id}>
                        {tpl.name}
                      </option>
                    ))}
                  </select>
                  <input
                    placeholder="附加指令（可选），如：重点看第二步的操作"
                    value={instructions}
                    onChange={(e) => setInstructions(e.target.value)}
                  />
                  <button className="primary" onClick={() => startAnalysis(false)}>
                    开始分析
                  </button>
                </div>
                <p className="muted">
                  分析会上传完整音频（云端转写）与关键帧；上限 20 分钟。转写与抽帧结果会缓存，
                  重新分析约 1 分钟内完成。
                </p>
              </div>
            )}

            {analyzing && (
              <div className="analysis-progress">
                分析进行中：{statusZh(task.status)}（转写通常 30 秒到几分钟…）
              </div>
            )}

            {report && (
              <div className="report">
                <h4>{report.report_json.title}</h4>
                <p>{report.report_json.summary}</p>
                {(report.report_json.chapters || []).length > 0 && (
                  <>
                    <div className="label">章节</div>
                    <ul>
                      {report.report_json.chapters.map((c, i) => (
                        <li key={i}>
                          <b>{c.title}</b>（{fmtTime(c.start)}–{fmtTime(c.end)}）{c.summary}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {(report.report_json.viewpoints || []).length > 0 && (
                  <>
                    <div className="label">观点</div>
                    {report.report_json.viewpoints.map((v, i) => (
                      <div key={i} className="viewpoint">
                        <b>{v.topic}</b>
                        <ul>
                          {v.claims.map((claim, j) => (
                            <li key={j}>
                              {fmtTime(claim.time)} {claim.text}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </>
                )}
                {(report.report_json.todos || []).length > 0 && (
                  <>
                    <div className="label">待办</div>
                    <ul>
                      {report.report_json.todos.map((td, i) => (
                        <li key={i}>
                          <input type="checkbox" /> {fmtTime(td.time)} {td.text}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {(report.report_json.quotes || []).length > 0 && (
                  <>
                    <div className="label">金句</div>
                    {report.report_json.quotes.map((q, i) => (
                      <blockquote key={i}>
                        {fmtTime(q.time)} {q.text}
                      </blockquote>
                    ))}
                  </>
                )}
                <details>
                  <summary className="muted">Markdown 源码</summary>
                  <pre>{report.markdown}</pre>
                </details>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function statusZh(status: string): string {
  const map: Record<string, string> = {
    created: "排队中",
    probing: "正在探测",
    classifying: "正在分类",
    classified: "已分类",
    rejected: "已拒绝",
    cancelled: "已取消",
    failed: "失败",
    extracting: "正在抽帧",
    transcribing: "正在转写",
    sampling: "正在抽帧",
    analyzing: "正在生成报告",
    done: "分析完成",
    no_value: "无分析价值",
  };
  return map[status] || status;
}

function fmtTime(seconds?: number): string {
  if (seconds == null) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m >= 60
    ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
