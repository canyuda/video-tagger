import { useState } from "react";
import { api } from "../api";

export default function Onboarding({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [dashscope, setDashscope] = useState("");
  const [typesafe, setTypesafe] = useState("");
  const [msg, setMsg] = useState("");

  const steps = [
    {
      title: "1 / 3 · 百炼 API Key",
      body: (
        <>
          <p>阿里百炼提供感知层 VLM（qwen3-vl-flash）与转写（paraformer-v2）。</p>
          <p className="muted">
            有免费额度与按量计费（月预算提醒线默认 ¥20，可在设置调整）。也可从项目根
            .env.local 读取（若已存在将自动使用）。
          </p>
          <input
            type="password"
            placeholder="粘贴 DASHSCOPE_API_KEY"
            value={dashscope}
            onChange={(e) => setDashscope(e.target.value)}
          />
        </>
      ),
      canSkip: false,
      valid: dashscope.trim().length > 0,
      action: async () => {
        await api.putKeys({ DASHSCOPE_API_KEY: dashscope.trim() });
      },
    },
    {
      title: "2 / 3 · TypeSafe（Jev）Key",
      body: (
        <>
          <p>Jev 是分类决策层（typesafe.ai）。缺失或故障时自动降级为「VLM 直出」并标记。</p>
          <p className="muted">可跳过，但建议配置以获得概率分布与更稳的判定。</p>
          <input
            type="password"
            placeholder="粘贴 TYPESAFE_API_KEY"
            value={typesafe}
            onChange={(e) => setTypesafe(e.target.value)}
          />
        </>
      ),
      canSkip: true,
      valid: typesafe.trim().length > 0,
      action: async () => {
        if (typesafe.trim()) await api.putKeys({ TYPESAFE_API_KEY: typesafe.trim() });
      },
    },
    {
      title: "3 / 3 · 抖音 Cookie",
      body: (
        <>
          <p>抖音下载功能需要登录 cookie（Phase 2 开放，现在可跳过）。</p>
          <p className="muted">跳过后抖音导入入口将置灰，稍后可在设置中配置。</p>
        </>
      ),
      canSkip: true,
      valid: true,
      action: async () => {},
    },
  ];

  const current = steps[step];

  return (
    <div className="onboarding">
      <h2>欢迎使用视频分类</h2>
      <div className="onboard-card">
        <h3>{current.title}</h3>
        {current.body}
        {msg && <div className="error-box">{msg}</div>}
        <div className="row">
          {step > 0 && <button onClick={() => setStep(step - 1)}>上一步</button>}
          <div className="spacer" />
          {current.canSkip && (
            <button
              onClick={async () => {
                if (step === steps.length - 1) {
                  onDone();
                } else {
                  setStep(step + 1);
                }
              }}
            >
              {step === steps.length - 1 ? "跳过并完成" : "跳过"}
            </button>
          )}
          {step === steps.length - 1 ? (
            <button
              className="primary"
              onClick={async () => {
                await current.action();
                onDone();
              }}
            >
              完成配置
            </button>
          ) : (
            <button
              className="primary"
              disabled={!current.valid}
              onClick={async () => {
                try {
                  await current.action();
                  setStep(step + 1);
                } catch (e) {
                  setMsg(String(e));
                }
              }}
            >
              下一步
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
