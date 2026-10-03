import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { api, configureSidecar, type SidecarInfo, type SettingsView, type Task } from "./api";
import Library from "./pages/Library";
import Analyses from "./pages/Analyses";
import Settings from "./pages/Settings";
import Onboarding from "./pages/Onboarding";
import TaskDetail from "./pages/TaskDetail";

type Tab = "analyses" | "library" | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("analyses");
  const [ready, setReady] = useState(false);
  const [fatal, setFatal] = useState("");
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const configured = settings?.configured?.dashscope === true;
  const hadActive = useRef(false);

  const refresh = useCallback(async () => {
    try {
      setTasks(await api.listTasks());
    } catch {
      /* sidecar restarting */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const boot = async () => {
      try {
        // The sidecar (PyInstaller onefile) needs seconds to extract on
        // first launch: poll sidecar_info instead of failing fast.
        let info: SidecarInfo | null = null;
        for (let i = 0; i < 120 && !cancelled; i++) {
          try {
            info = await invoke<SidecarInfo>("sidecar_info");
            break;
          } catch {
            await new Promise((r) => setTimeout(r, 250));
          }
        }
        if (cancelled || !info) {
          if (!cancelled) setFatal("分类服务未能在 30 秒内就绪（详见 应用数据目录/sidecar.log）");
          return;
        }
        configureSidecar(info);
        let healthy = false;
        for (let i = 0; i < 40; i++) {
          try {
            await api.health();
            healthy = true;
            break;
          } catch {
            await new Promise((r) => setTimeout(r, 250));
          }
        }
        if (cancelled) return;
        if (!healthy) {
          setFatal("分类服务已启动但 HTTP 未就绪（详见 应用数据目录/sidecar.log）");
          return;
        }
        let s: SettingsView | null = null;
        for (let i = 0; i < 12; i++) {
          try {
            s = await api.settings();
            break;
          } catch {
            await new Promise((r) => setTimeout(r, 250));
          }
        }
        if (cancelled) return;
        if (!s) {
          setFatal("无法读取设置（分类服务 HTTP 未就绪）");
          return;
        }
        setSettings(s);
        setReady(true);
        await refresh();
      } catch (e) {
        if (!cancelled) setFatal(String(e));
      }
    };
    boot();
    const unlisten = listen("sidecar-ready", () => boot());
    const unlistenFail = listen<string>("sidecar-failed", (event) => {
      setFatal(`分类服务启动失败：${event.payload}`);
    });
    return () => {
      cancelled = true;
      unlisten.then((f) => f());
      unlistenFail.then((f) => f());
    };
  }, [refresh]);

  // Poll while any task is in flight (batch progress), slow otherwise.
  // Batch completion fires one system notification (D36).
  useEffect(() => {
    if (!ready) return;
    const active = tasks.some((t) =>
      ["created", "probing", "classifying", "downloading"].includes(t.status),
    );
    if (active) {
      hadActive.current = true;
    } else if (hadActive.current) {
      hadActive.current = false;
      const classified = tasks.filter((t) => t.status === "classified").length;
      const failed = tasks.filter((t) => ["failed", "rejected"].includes(t.status)).length;
      invoke("notify", {
        title: "分类完成",
        body: `成功 ${classified} 个${failed ? `，失败 ${failed} 个` : ""}`,
      }).catch(() => {});
    }
    const timer = setInterval(refresh, active ? 1500 : 6000);
    return () => clearInterval(timer);
  }, [ready, tasks, refresh]);

  const reloadSettings = useCallback(async () => {
    setSettings(await api.settings());
  }, []);

  if (fatal) {
    return (
      <div className="fatal">
        <h2>启动失败</h2>
        <p>{fatal}</p>
      </div>
    );
  }
  if (!ready) return <div className="loading">正在启动分类服务…</div>;

  if (!configured) {
    return <Onboarding onDone={reloadSettings} />;
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          视频内容分析 <span className="brand-en">video-tagger</span>
        </div>
        <nav>
          <button
            className={tab === "analyses" ? "active" : ""}
            onClick={() => setTab("analyses")}
          >
            分析
          </button>
          <button className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}>
            素材库
          </button>
          <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>
            设置
          </button>
        </nav>
      </header>
      {tab === "analyses" ? (
        <Analyses onOpen={(id) => setSelected(id)} />
      ) : tab === "library" ? (
        <Library
          tasks={tasks}
          onRefresh={refresh}
          onOpen={(id) => setSelected(id)}
          douyinEnabled={settings?.configured?.douyin === true}
        />
      ) : (
        <Settings onSaved={reloadSettings} />
      )}
      {selected && (
        <TaskDetail
          taskId={selected}
          onClose={() => {
            setSelected(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}
