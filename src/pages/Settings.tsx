import { useEffect, useState } from "react";
import { api, type SettingsView } from "../api";
import type { Taxonomy, TaxonomyType } from "../contracts";

type RoutingTable = Record<string, { template_id: string; model: string }>;

const MODEL_CHOICES = ["qwen3-vl-flash", "qwen3-vl-plus", "glm-4.6v-flash"];

export default function Settings({ onSaved }: { onSaved: () => void }) {
  const [view, setView] = useState<SettingsView | null>(null);
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [msg, setMsg] = useState("");
  const [frameCount, setFrameCount] = useState("10");
  const [threshold, setThreshold] = useState("0.85");
  const [cookieValue, setCookieValue] = useState("");
  const [cookieState, setCookieState] = useState("");
  const [routing, setRouting] = useState<RoutingTable | null>(null);
  const [tplList, setTplList] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    (async () => {
      const v = await api.settings();
      setView(v);
      if (v.stored?.frame_count) setFrameCount(v.stored.frame_count);
      if (v.stored?.confidence_threshold) setThreshold(v.stored.confidence_threshold);
      setTaxonomy(await api.taxonomy());
      setRouting(await api.routing());
      api.templates().then(setTplList).catch(() => {});
    })();
  }, []);

  const flash = (text: string) => {
    setMsg(text);
    setTimeout(() => setMsg(""), 3000);
  };

  if (!view || !taxonomy) return <div className="muted">加载设置中…</div>;

  return (
    <div className="settings">
      {msg && <div className="toast">{msg}</div>}

      <section>
        <h3>模型与密钥</h3>
        <p className="muted">
          密钥仅写入本地 .env.local（权限 600），界面永不回显。百炼（DASHSCOPE_API_KEY）
          与 TypeSafe（TYPESAFE_API_KEY）必填；抖音 cookie 可跳过。
        </p>
        <KeyRow label="百炼 API Key" field="DASHSCOPE_API_KEY" ok={view.configured?.dashscope}
          onSaved={() => { onSaved(); api.settings().then(setView); }} />
        <KeyRow label="TypeSafe (Jev) Key" field="TYPESAFE_API_KEY" ok={view.configured?.typesafe}
          onSaved={() => { onSaved(); api.settings().then(setView); }} />
        <div className="form-row key-row">
          <label>
            抖音 Cookie（可跳过）{" "}
            {view.configured?.douyin ? "✅ 已配置" : ""}
          </label>
          <div className="row">
            <input
              type="password"
              placeholder="粘贴完整 Cookie 字符串…"
              value={cookieValue}
              onChange={(e) => setCookieValue(e.target.value)}
            />
            <button
              onClick={async () => {
                if (cookieValue.trim()) {
                  await api.putKeys({ DOUYIN_COOKIE: cookieValue.trim() });
                  setCookieValue("");
                  const v = await api.settings();
                  setView(v);
                }
                setCookieState("检测中…");
                setCookieState(JSON.stringify(await api.checkDouyinCookie()));
              }}
            >
              保存并检测
            </button>
            {cookieState && <span className="muted">{cookieState}</span>}
          </div>
        </div>
      </section>

      <section>
        <h3>分类参数</h3>
        <div className="form-row">
          <label>抽样帧数（默认 10）</label>
          <input value={frameCount} onChange={(e) => setFrameCount(e.target.value)} />
        </div>
        <div className="form-row">
          <label>L2 触发置信度阈值（默认 0.85）</label>
          <input value={threshold} onChange={(e) => setThreshold(e.target.value)} />
        </div>
        <button
          onClick={async () => {
            await api.putParams({ frame_count: frameCount, confidence_threshold: threshold });
            flash("分类参数已保存");
          }}
        >
          保存参数
        </button>
      </section>

      <section>
        <h3>分类体系（taxonomy v{taxonomy.version}）</h3>
        <p className="muted">
          改「有价值」标记即时生效、不重跑模型；改类型/特征描述只影响后续任务（重新分类生效）。
        </p>
        <table className="tax-table">
          <thead>
            <tr>
              <th>中文名</th>
              <th>稳定键 name_en</th>
              <th>有价值</th>
              <th>特征描述 hint</th>
            </tr>
          </thead>
          <tbody>
            {taxonomy.types.map((t, i) => (
              <TaxRow
                key={i}
                type_={t}
                onChange={(next) => {
                  const types = taxonomy.types.slice();
                  types[i] = next;
                  setTaxonomy({ ...taxonomy, types });
                }}
              />
            ))}
          </tbody>
        </table>
        <div className="row">
          <button
            onClick={async () => {
              try {
                const next = await api.putTaxonomy({
                  ...taxonomy,
                  version: taxonomy.version + 1,
                });
                setTaxonomy(next);
                flash("taxonomy 已保存（版本 +1，新任务用新版本）");
              } catch (e) {
                flash(`保存失败: ${e}`);
              }
            }}
          >
            保存 taxonomy
          </button>
          <button
            onClick={async () => {
              setTaxonomy(await api.resetTaxonomy());
              flash("已恢复内置默认");
            }}
          >
            恢复默认
          </button>
        </div>
      </section>

      <section>
        <h3>分析路由（分类 → 提示词 + 模型）</h3>
        <p className="muted">
          D58：分类是分析的前置——按内容类型预选分析用的提示词模板与模型档；
          分析时可临时更改。`_default` 兜底未知类型。
        </p>
        {routing && (
          <table className="tax-table">
            <thead>
              <tr>
                <th>内容类型</th>
                <th>提示词模板</th>
                <th>模型</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(routing).map(([typeKey, entry]) => (
                <tr key={typeKey}>
                  <td>{typeKey}</td>
                  <td>
                    <select
                      value={entry.template_id}
                      onChange={(e) =>
                        setRouting({
                          ...routing,
                          [typeKey]: { ...entry, template_id: e.target.value },
                        })
                      }
                    >
                      {(tplList.length ? tplList : [{ id: "tpl-general", name: "通用总结" }]).map(
                        (tpl) => (
                          <option key={tpl.id} value={tpl.id}>{tpl.name}</option>
                        ),
                      )}
                    </select>
                  </td>
                  <td>
                    <select
                      value={entry.model}
                      onChange={(e) =>
                        setRouting({
                          ...routing,
                          [typeKey]: { ...entry, model: e.target.value },
                        })
                      }
                    >
                      {MODEL_CHOICES.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <button
          onClick={async () => {
            if (!routing) return;
            try {
              setRouting(await api.putRouting(routing));
              flash("分析路由已保存");
            } catch (e) {
              flash(`保存失败: ${e}`);
            }
          }}
        >
          保存路由
        </button>
      </section>

      <section>
        <h3>数据</h3>
        <a className="button" href={api.csvUrl()}>
          导出全部任务 CSV（UTF-8 BOM）
        </a>
      </section>

      <section>
        <h3>隐私说明</h3>
        <p className="muted">
          分类过程会上传：均匀抽样的视频帧（默认 10 帧、512px）；低置信时 3 段 10 秒音频样本；
          以及平台元数据（抖音来源）。不上传完整视频。密钥仅存本地。
        </p>
      </section>
    </div>
  );
}

function KeyRow({
  label,
  field,
  ok,
  onSaved,
}: {
  label: string;
  field: string;
  ok?: boolean;
  onSaved: () => void;
}) {
  const [value, setValue] = useState("");
  const [state, setState] = useState("");
  return (
    <div className="form-row key-row">
      <label>
        {label} {ok === true ? "✅" : ok === false ? "❌ 未配置" : ""}
      </label>
      <div className="row">
        <input
          type="password"
          placeholder="粘贴密钥…"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button
          onClick={async () => {
            if (!value.trim()) return;
            await api.putKeys({ [field]: value.trim() });
            setValue("");
            setState("已保存");
            onSaved();
          }}
        >
          保存
        </button>
        {state && <span className="muted">{state}</span>}
      </div>
    </div>
  );
}

function TaxRow({
  type_,
  onChange,
}: {
  type_: TaxonomyType;
  onChange: (t: TaxonomyType) => void;
}) {
  return (
    <tr>
      <td>
        <input value={type_.name} onChange={(e) => onChange({ ...type_, name: e.target.value })} />
      </td>
      <td>
        <input
          value={type_.name_en ?? ""}
          onChange={(e) => onChange({ ...type_, name_en: e.target.value })}
        />
      </td>
      <td>
        <input
          type="checkbox"
          checked={type_.worthy}
          onChange={(e) => onChange({ ...type_, worthy: e.target.checked })}
        />
      </td>
      <td>
        <input value={type_.hint} onChange={(e) => onChange({ ...type_, hint: e.target.value })} />
      </td>
    </tr>
  );
}
