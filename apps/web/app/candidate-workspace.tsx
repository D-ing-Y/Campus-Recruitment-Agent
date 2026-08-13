"use client";

import { ChangeEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_CAMPUS_API_URL ?? "http://127.0.0.1:8765";
const SESSION_KEY = "campus-agent-web-session";

type AnyRecord = Record<string, unknown>;

type Envelope<T> = {
  ok: boolean;
  data: T | null;
  error: { type: string; message: string; retryable: boolean } | null;
};

type Workspace = {
  session: AnyRecord & {
    session_id: string;
    status: string;
    current_stage: string;
    next_action: string;
    current_refs: Record<string, string>;
  };
  resume: AnyRecord | null;
  resume_review: {
    request: AnyRecord & { allowed_actions?: string[] };
    view: AnyRecord & {
      section?: string;
      target_kind?: string;
      value?: unknown;
      source_pages?: number[];
      source_excerpts?: { page?: number; text?: string }[];
    };
  } | null;
  candidate_profile: {
    snapshot_id: string;
    version: number;
    created_at: string;
    profile_data: AnyRecord;
  } | null;
  candidate_interaction: (AnyRecord & {
    interaction_type?: string;
    reason?: string;
    allowed_actions?: string[];
    questions?: Array<AnyRecord & {
      question_id: string;
      gap_id: string;
      prompt: string;
      reason?: string;
      required?: boolean;
    }>;
    requested_materials?: Array<AnyRecord & {
      material_id: string;
      gap_id: string;
      description: string;
      reason?: string;
    }>;
  }) | null;
  profile_history: Array<{
    snapshot_id: string;
    version: number;
    created_at: string;
  }>;
  latest_diff: AnyRecord | null;
  model: { provider?: string; model?: string; configured: boolean };
};

const sectionLabels: Record<string, string> = {
  personal_information: "个人信息",
  personal_advantage: "个人优势",
  career_expectations: "求职期望",
  work_experiences: "工作经历",
  project_experiences: "项目经历",
  education_experiences: "教育经历",
  professional_skills: "专业技能",
  custom_sections: "其他信息",
};

const fieldLabels: Record<string, string> = {
  name: "姓名",
  gender: "性别",
  birth_date: "出生日期",
  job_search_status: "求职状态",
  identity: "身份",
  phone: "手机",
  wechat: "微信",
  email: "邮箱",
  birthplace: "籍贯",
  text: "内容",
  employment_type: "就业类型",
  role: "岗位",
  salary: "薪资",
  city: "城市",
  organization: "组织",
  position: "职位",
  start_date: "开始时间",
  end_date: "结束时间",
  content: "经历描述",
  project_name: "项目名称",
  institution: "学校",
  degree: "学历",
  major: "专业",
  graduation_year: "毕业时间",
  skill_name: "技能",
  level: "掌握程度",
  title: "名称",
  description: "描述",
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  let envelope: Envelope<T>;
  try {
    envelope = (await response.json()) as Envelope<T>;
  } catch {
    throw new Error("本地 API 未返回可识别的响应，请确认服务已启动。");
  }
  if (!response.ok || !envelope.ok || envelope.data === null) {
    throw new Error(envelope.error?.message ?? "请求处理失败。");
  }
  return envelope.data;
}

function cnLabel(key: string) {
  return fieldLabels[key] ?? sectionLabels[key] ?? key.replaceAll("_", " ");
}

function hasValue(value: unknown) {
  return value !== null && value !== undefined && value !== "";
}

function CompactValue({ value }: { value: unknown }) {
  if (!hasValue(value)) return <span className="empty-value">未填写</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="empty-value">暂无</span>;
    return (
      <div className="value-list">
        {value.map((item, index) => (
          <div className="value-list-item" key={index}>
            <CompactValue value={item} />
          </div>
        ))}
      </div>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as AnyRecord).filter(
      ([key, item]) => !key.endsWith("_id") && !key.includes("claim") && hasValue(item),
    );
    return (
      <div className="field-grid">
        {entries.map(([key, item]) => (
          <div className="field-item" key={key}>
            <span>{cnLabel(key)}</span>
            <CompactValue value={item} />
          </div>
        ))}
      </div>
    );
  }
  return <>{String(value)}</>;
}

function AppHeader({
  active,
  onReset,
}: {
  active: boolean;
  onReset: () => void;
}) {
  return (
    <header className="app-header">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">
          C
        </span>
        <span>Campus Agent</span>
        <span className="brand-divider" />
        <span className="brand-subtitle">候选人画像</span>
      </div>
      <div className="header-actions">
        <span className="local-badge">
          <i aria-hidden="true" /> 本地模式
        </span>
        {active ? (
          <button className="text-button" onClick={onReset} type="button">
            新建画像
          </button>
        ) : null}
      </div>
    </header>
  );
}

function Landing({ onStart, busy }: { onStart: () => void; busy: string }) {
  return (
    <main className="landing">
      <section className="hero-copy">
        <div className="eyebrow">
          <span>01</span> CANDIDATE PROFILE
        </div>
        <h1>
          把一份简历，变成一份
          <em>可追溯的能力画像。</em>
        </h1>
        <p>
          上传简历，由 Agent 提取证据、逐项请你确认，并在信息不足时决定是否继续追问。你看到的不只是结论，也能看到结论从哪里来。
        </p>
        <div className="hero-actions">
          <button className="primary-button hero-button" disabled={Boolean(busy)} onClick={onStart}>
            {busy || "开始构建画像"} <span aria-hidden="true">→</span>
          </button>
          <span>无需登录 · 数据保存在本机</span>
        </div>
      </section>

      <section className="hero-console" aria-label="候选人画像流程预览">
        <div className="console-bar">
          <span className="console-dots"><i /><i /><i /></span>
          <span>candidate-profile.agent</span>
          <span className="console-status">READY</span>
        </div>
        <div className="console-body">
          <div className="console-kicker">CURRENT WORKFLOW</div>
          <h2>从证据开始，而不是从印象开始</h2>
          <div className="flow-preview">
            {[
              ["01", "上传简历", "PDF 原文归档"],
              ["02", "逐项确认", "8 个标准模块"],
              ["03", "能力判断", "确定性规则 + 可替换 LLM"],
              ["04", "生成画像", "版本化且可比较"],
            ].map(([number, title, note], index) => (
              <div className="flow-row" key={number}>
                <span className={index === 0 ? "flow-number active" : "flow-number"}>{number}</span>
                <div><strong>{title}</strong><small>{note}</small></div>
                <span aria-hidden="true">{index === 0 ? "●" : "○"}</span>
              </div>
            ))}
          </div>
          <div className="console-command">
            <span>agent</span> 等待你的简历 <i aria-hidden="true" />
          </div>
        </div>
      </section>
    </main>
  );
}

function ProgressRail({ workspace }: { workspace: Workspace }) {
  const step = useMemo(() => {
    if (workspace.candidate_profile) return 5;
    if (workspace.candidate_interaction) return 4;
    if (workspace.resume) return 3;
    if (workspace.resume_review) return 2;
    return 1;
  }, [workspace]);
  const steps = ["上传简历", "核对信息", "生成画像", "补充澄清", "画像完成"];

  return (
    <aside className="progress-rail">
      <div className="rail-label">构建进度</div>
      <ol>
        {steps.map((label, index) => {
          const number = index + 1;
          const done = number < step;
          const current = number === step;
          return (
            <li className={done ? "done" : current ? "current" : ""} key={label}>
              <span>{done ? "✓" : number}</span>
              <div><strong>{label}</strong><small>{done ? "已完成" : current ? "进行中" : "待处理"}</small></div>
            </li>
          );
        })}
      </ol>
      <div className="rail-divider" />
      <div className="rail-label">后续能力</div>
      <div className="future-item"><span>岗位研究</span><small>即将接入</small></div>
      <div className="future-item"><span>人岗匹配</span><small>即将接入</small></div>
      <div className="future-item"><span>准备计划</span><small>即将接入</small></div>
    </aside>
  );
}

function SurfaceHeader({
  kicker,
  title,
  copy,
  side,
}: {
  kicker: string;
  title: string;
  copy: string;
  side?: ReactNode;
}) {
  return (
    <div className="surface-header">
      <div>
        <span className="surface-kicker">{kicker}</span>
        <h2>{title}</h2>
        <p>{copy}</p>
      </div>
      {side}
    </div>
  );
}

function UploadResume({ onUpload, busy }: { onUpload: (file: File) => void; busy: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const accept = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onUpload(file);
  };
  return (
    <section className="surface">
      <SurfaceHeader
        kicker="STEP 01 · RESUME EVIDENCE"
        title="先上传你的简历"
        copy="Agent 会解析 PDF，但不会直接把解析结果当作事实。后续每一部分都需要你确认。"
        side={<span className="privacy-pill">仅本机可见</span>}
      />
      <button
        className={`upload-zone ${dragging ? "dragging" : ""}`}
        disabled={Boolean(busy)}
        onClick={() => input.current?.click()}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); setDragging(false); accept(event.dataTransfer.files); }}
        type="button"
      >
        <span className="upload-icon" aria-hidden="true">↑</span>
        <strong>{busy || "拖入 PDF，或点击选择文件"}</strong>
        <small>最大 10 MB · 原文件将进入本地证据仓库</small>
      </button>
      <input
        accept="application/pdf,.pdf"
        className="visually-hidden"
        onChange={(event) => accept(event.target.files)}
        ref={input}
        type="file"
      />
      <div className="guardrail-row">
        <div><span>01</span><strong>原文归档</strong><small>保存输入证据</small></div>
        <div><span>02</span><strong>结构提取</strong><small>解析 8 个模块</small></div>
        <div><span>03</span><strong>人工确认</strong><small>确认后才可引用</small></div>
      </div>
    </section>
  );
}

function ResumeReview({
  review,
  onAction,
  busy,
}: {
  review: NonNullable<Workspace["resume_review"]>;
  onAction: (action: string, patch?: AnyRecord) => void;
  busy: string;
}) {
  const view = review.view;
  const allowed = review.request.allowed_actions ?? [];
  const [editing, setEditing] = useState(false);
  const [patch, setPatch] = useState(() => JSON.stringify(view.value ?? {}, null, 2));
  const [patchError, setPatchError] = useState("");
  const section = sectionLabels[String(view.section)] ?? String(view.section ?? "简历信息");
  const submitPatch = () => {
    try {
      const value = JSON.parse(patch);
      if (!value || Array.isArray(value) || typeof value !== "object") throw new Error();
      setPatchError("");
      onAction("correct", value as AnyRecord);
    } catch {
      setPatchError("请提交一个有效的 JSON 对象。");
    }
  };
  return (
    <section className="surface review-surface">
      <SurfaceHeader
        kicker="STEP 02 · HUMAN CONFIRMATION"
        title={`请核对：${section}`}
        copy="只有确认过的信息会进入 ResumeEvidence。修改内容也应当来自当前 PDF。"
        side={<span className="step-counter">逐项确认中</span>}
      />
      <div className="review-layout">
        <div className="review-main">
          <div className="card-label"><span>提取结果</span><small>{String(view.target_kind ?? "section")}</small></div>
          {editing ? (
            <div className="json-editor">
              <label htmlFor="resume-patch">修改后的结构化内容</label>
              <textarea id="resume-patch" onChange={(event) => setPatch(event.target.value)} value={patch} />
              {patchError ? <p className="inline-error">{patchError}</p> : null}
              <div className="button-row compact">
                <button className="primary-button" disabled={Boolean(busy)} onClick={submitPatch}>提交修改</button>
                <button className="secondary-button" onClick={() => setEditing(false)}>返回</button>
              </div>
            </div>
          ) : (
            <div className="extracted-value"><CompactValue value={view.value} /></div>
          )}
        </div>
        <aside className="source-panel">
          <div className="card-label"><span>PDF 证据</span><small>{(view.source_pages ?? []).map((page) => `P${page}`).join(" · ") || "已归档"}</small></div>
          {(view.source_excerpts ?? []).length ? (
            view.source_excerpts?.map((excerpt, index) => (
              <blockquote key={index}><span>第 {excerpt.page ?? "?"} 页</span>{excerpt.text}</blockquote>
            ))
          ) : (
            <p className="source-empty">当前字段没有可展示的文本截取，请结合原始 PDF 判断。</p>
          )}
        </aside>
      </div>
      {!editing ? (
        <div className="review-actions">
          <div className="button-row">
            {allowed.includes("confirm") ? <button className="primary-button" disabled={Boolean(busy)} onClick={() => onAction("confirm")}>内容正确，继续 <span>→</span></button> : null}
            {allowed.includes("correct") ? <button className="secondary-button" disabled={Boolean(busy)} onClick={() => setEditing(true)}>需要修改</button> : null}
            {allowed.includes("remove") ? <button className="text-button danger" disabled={Boolean(busy)} onClick={() => onAction("remove")}>删除这一项</button> : null}
          </div>
          <div className="button-row subtle-actions">
            {allowed.includes("retry") ? <button className="text-button" disabled={Boolean(busy)} onClick={() => onAction("retry")}>重新解析</button> : null}
            {allowed.includes("cancel") ? <button className="text-button" disabled={Boolean(busy)} onClick={() => onAction("cancel")}>终止本次流程</button> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ResumeReady({ workspace, onBuild, busy }: { workspace: Workspace; onBuild: () => void; busy: string }) {
  const resume = workspace.resume as AnyRecord;
  const data = (resume?.data ?? {}) as AnyRecord;
  return (
    <section className="surface">
      <SurfaceHeader
        kicker="STEP 03 · CANDIDATE GRAPH"
        title="简历证据已经确认"
        copy="接下来将基于已确认的 ResumeEvidence 构建候选人画像，并由现有充分性评价器决定是否需要追问。"
        side={<span className="success-pill">✓ 已确认</span>}
      />
      <div className="resume-ready-grid">
        <div className="resume-mini-preview">
          <div className="document-fold" />
          <span>RESUME EVIDENCE</span>
          <strong>{String(((data.personal_information ?? {}) as AnyRecord).name ?? "候选人简历")}</strong>
          <small>8 个标准模块 · 证据快照不可变</small>
        </div>
        <div className="ready-copy">
          <h3>Agent 将完成三件事</h3>
          <ul>
            <li><span>1</span>把简历事实投影成教育、经历与能力结构</li>
            <li><span>2</span>标出未知项、冲突项和责任边界</li>
            <li><span>3</span>信息不足时，生成最有价值的澄清问题</li>
          </ul>
          <button className="primary-button" disabled={Boolean(busy)} onClick={onBuild}>{busy || "生成候选人画像"} <span>→</span></button>
        </div>
      </div>
    </section>
  );
}

function SessionStopped({ status }: { status: string }) {
  const cancelled = status === "cancelled";
  return (
    <section className="surface stopped-state">
      <span className="stopped-mark" aria-hidden="true">{cancelled ? "×" : "!"}</span>
      <span className="surface-kicker">SESSION {status.toUpperCase()}</span>
      <h2>{cancelled ? "本次画像构建已经终止" : "本次画像构建未能继续"}</h2>
      <p>
        {cancelled
          ? "现有证据和运行记录仍保存在本机。如需重新开始，请点击右上角“新建画像”。"
          : "请保留当前运行记录用于排查，然后点击右上角“新建画像”重新开始。"}
      </p>
    </section>
  );
}

function CandidateQuestions({
  interaction,
  onSubmit,
  onUpload,
  busy,
}: {
  interaction: NonNullable<Workspace["candidate_interaction"]>;
  onSubmit: (action: string, payload?: AnyRecord) => void;
  onUpload: (file: File) => void;
  busy: string;
}) {
  const questions = interaction.questions ?? [];
  const materials = interaction.requested_materials ?? [];
  const allowed = interaction.allowed_actions ?? [];
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const materialInput = useRef<HTMLInputElement>(null);
  const answer = () => {
    const values = questions
      .map((question) => ({ question_id: question.question_id, text: (answers[question.question_id] ?? "").trim() }))
      .filter((item) => item.text);
    onSubmit("answer", { answers: values });
  };
  const skipIds = [
    ...questions.map((question) => question.gap_id),
    ...materials.map((material) => material.gap_id),
  ];
  return (
    <section className="surface">
      <SurfaceHeader
        kicker="STEP 04 · SUFFICIENCY CHECK"
        title="还需要你补充一点信息"
        copy={String(interaction.reason ?? "Agent 发现部分能力结论缺少足够证据。回答后会重新评估，而不是无限追问。")}
        side={<span className="question-pill">Agent 发起澄清</span>}
      />
      {questions.length ? (
        <div className="question-list">
          {questions.map((question, index) => (
            <div className="question-card" key={question.question_id}>
              <div className="question-index">Q{String(index + 1).padStart(2, "0")}</div>
              <div className="question-content">
                <label htmlFor={question.question_id}>{question.prompt}</label>
                {question.reason ? <p>{question.reason}</p> : null}
                <textarea
                  id={question.question_id}
                  onChange={(event) => setAnswers((current) => ({ ...current, [question.question_id]: event.target.value }))}
                  placeholder="请尽量描述你实际负责的范围、使用的方法和产出结果…"
                  value={answers[question.question_id] ?? ""}
                />
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {materials.length ? (
        <div className="material-request">
          <div><span className="upload-icon small" aria-hidden="true">+</span></div>
          <div><strong>也可以上传补充材料</strong><p>{materials.map((item) => item.description).join("；")}</p></div>
          <button className="secondary-button" disabled={Boolean(busy) || !allowed.includes("upload")} onClick={() => materialInput.current?.click()}>选择材料</button>
          <input
            accept=".pdf,.md,.markdown,.txt,text/plain,text/markdown,application/pdf"
            className="visually-hidden"
            onChange={(event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) onUpload(file); }}
            ref={materialInput}
            type="file"
          />
        </div>
      ) : null}
      <div className="review-actions">
        <div className="button-row">
          {allowed.includes("answer") ? <button className="primary-button" disabled={Boolean(busy) || !Object.values(answers).some((value) => value.trim())} onClick={answer}>提交回答并重新评估 <span>→</span></button> : null}
          {allowed.includes("confirm") ? <button className="primary-button" disabled={Boolean(busy)} onClick={() => onSubmit("confirm", { confirmation: true })}>确认当前画像</button> : null}
          {allowed.includes("skip") ? <button className="secondary-button" disabled={Boolean(busy)} onClick={() => onSubmit("skip", { skipped_ids: skipIds })}>暂时跳过</button> : null}
        </div>
        {allowed.includes("cancel") ? <button className="text-button" disabled={Boolean(busy)} onClick={() => onSubmit("cancel")}>终止本次流程</button> : null}
      </div>
    </section>
  );
}

function ListCard({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="profile-card">
      <div className="profile-card-title"><h3>{title}</h3>{count !== undefined ? <span>{count}</span> : null}</div>
      {children}
    </section>
  );
}

function CandidateProfileView({ workspace }: { workspace: Workspace }) {
  const snapshot = workspace.candidate_profile!;
  const profile = snapshot.profile_data;
  const education = (profile.education ?? []) as AnyRecord[];
  const capabilities = (profile.capabilities ?? []) as AnyRecord[];
  const experiences = (profile.experiences ?? []) as AnyRecord[];
  const boundaries = (profile.responsibility_boundaries ?? []) as AnyRecord[];
  const unknowns = (profile.unknowns ?? []) as unknown[];
  const conflicts = (profile.conflicts ?? []) as AnyRecord[];
  const coverage = (profile.evidence_coverage ?? {}) as AnyRecord;
  const latestDiff = workspace.latest_diff;
  const levelLabel: Record<string, string> = { beginner: "入门", basic: "基础", intermediate: "熟练", advanced: "进阶", expert: "专家", unknown: "待确认" };

  return (
    <div className="profile-view">
      <section className="surface profile-hero">
        <div>
          <span className="surface-kicker">CANDIDATE PROFILE · V{snapshot.version}</span>
          <h2>你的候选人能力画像已经生成</h2>
          <p>画像只引用已归档证据；未知与冲突会被明确保留，不会由模型静默补全。</p>
        </div>
        <div className="profile-score">
          <span>证据覆盖</span>
          <strong>{String(coverage.supported_field_count ?? 0)}</strong>
          <small>个已支持字段</small>
        </div>
      </section>
      <div className="coverage-strip">
        <div><span>已支持</span><strong>{String(coverage.supported_field_count ?? 0)}</strong></div>
        <div><span>推断项</span><strong>{String(coverage.inferred_field_count ?? 0)}</strong></div>
        <div><span>未知项</span><strong>{String(coverage.unknown_field_count ?? unknowns.length)}</strong></div>
        <div><span>冲突项</span><strong>{String(coverage.conflicted_field_count ?? conflicts.length)}</strong></div>
      </div>
      <div className="profile-grid">
        <ListCard count={capabilities.length} title="能力与技能">
          <div className="capability-list">
            {capabilities.length ? capabilities.map((item, index) => (
              <div className="capability-row" key={String(item.capability_id ?? item.raw_label ?? index)}>
                <div><strong>{String(item.raw_label ?? item.capability_id ?? "未命名能力")}</strong><small>{String(item.evidence_summary ?? "来自候选人证据")}</small></div>
                <span className={`level-tag ${String(item.status ?? "inferred")}`}>{levelLabel[String(item.level)] ?? String(item.level ?? "待确认")}</span>
              </div>
            )) : <p className="card-empty">暂无可确认的能力项。</p>}
          </div>
        </ListCard>
        <ListCard count={education.length} title="教育背景">
          <div className="timeline-list">
            {education.length ? education.map((item, index) => (
              <div className="timeline-item" key={String(item.education_id ?? index)}>
                <i /><div><strong>{String(item.institution ?? "学校待确认")}</strong><p>{[item.degree, item.major].filter(Boolean).join(" · ") || "学历信息待补充"}</p><small>{String(item.graduation_year ?? "毕业时间待确认")}</small></div>
              </div>
            )) : <p className="card-empty">暂无教育经历。</p>}
          </div>
        </ListCard>
        <ListCard count={experiences.length} title="项目与经历">
          <div className="experience-list">
            {experiences.length ? experiences.map((item, index) => (
              <article className="experience-item" key={String(item.experience_id ?? index)}>
                <div className="experience-top"><div><span>{String(item.kind ?? "experience").toUpperCase()}</span><h4>{String(item.title ?? "未命名经历")}</h4></div><small>{String(item.context ?? "")}</small></div>
                {item.description ? <p>{String(item.description)}</p> : null}
                {Array.isArray(item.responsibilities) && item.responsibilities.length ? <ul>{item.responsibilities.map((value, valueIndex) => <li key={valueIndex}>{String(value)}</li>)}</ul> : null}
                {Array.isArray(item.technologies) && item.technologies.length ? <div className="tag-list">{item.technologies.map((value, valueIndex) => <span key={valueIndex}>{String(value)}</span>)}</div> : null}
              </article>
            )) : <p className="card-empty">暂无项目或经历。</p>}
          </div>
        </ListCard>
        <ListCard count={boundaries.length} title="责任边界">
          <div className="boundary-list">
            {boundaries.length ? boundaries.map((item, index) => <div key={index}><span>{index + 1}</span><p>{String(item.scope ?? "边界待确认")}</p><small>{Math.round(Number(item.confidence ?? 0) * 100)}% 置信</small></div>) : <p className="card-empty">当前没有明确的责任边界。</p>}
          </div>
        </ListCard>
        <ListCard count={unknowns.length} title="仍待明确">
          <ul className="unknown-list">{unknowns.length ? unknowns.map((item, index) => <li key={index}>{String(item)}</li>) : <li className="resolved">当前没有未解决项。</li>}</ul>
        </ListCard>
        <ListCard count={conflicts.length} title="证据冲突">
          <div className="conflict-list">{conflicts.length ? conflicts.map((item, index) => <div key={index}><strong>{String(item.predicate ?? `冲突 ${index + 1}`)}</strong><CompactValue value={item} /></div>) : <p className="card-empty">当前没有冲突证据。</p>}</div>
        </ListCard>
      </div>
      {workspace.profile_history.length > 1 ? (
        <section className="surface version-panel">
          <div><span className="surface-kicker">VERSION HISTORY</span><h3>画像版本变化</h3><p>当前为 V{snapshot.version}，历史版本保持不可变。</p></div>
          <div className="version-stats">
            <div><strong>{Array.isArray(latestDiff?.added_paths) ? latestDiff.added_paths.length : 0}</strong><span>新增字段</span></div>
            <div><strong>{Array.isArray(latestDiff?.changed_paths) ? latestDiff.changed_paths.length : 0}</strong><span>更新字段</span></div>
            <div><strong>{Array.isArray(latestDiff?.resolved_conflicts) ? latestDiff.resolved_conflicts.length : 0}</strong><span>解决冲突</span></div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

export function CandidateWorkspace() {
  const [sessionId, setSessionId] = useState("");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      await Promise.resolve();
      const stored = window.localStorage.getItem(SESSION_KEY) ?? "";
      if (cancelled) return;
      setSessionId(stored);
      if (stored) {
        try {
          const restored = await api<Workspace>(`/api/sessions/${stored}/workspace`);
          if (!cancelled) setWorkspace(restored);
        } catch (reason) {
          if (!cancelled) {
            setError(reason instanceof Error ? reason.message : "无法恢复本地工作区。");
          }
        }
      }
    };
    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const run = async (label: string, action: () => Promise<Workspace>) => {
    setBusy(label);
    setError("");
    try {
      setWorkspace(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请重试。");
    } finally {
      setBusy("");
    }
  };

  const start = () => run("正在创建工作区…", async () => {
    const next = await api<Workspace>("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const id = next.session.session_id;
    window.localStorage.setItem(SESSION_KEY, id);
    setSessionId(id);
    return next;
  });

  const reset = () => {
    window.localStorage.removeItem(SESSION_KEY);
    setSessionId("");
    setWorkspace(null);
    setError("");
  };

  const uploadResume = (file: File) => run("正在解析简历…", async () => {
    const form = new FormData();
    form.append("file", file);
    const data = await api<{ workspace: Workspace }>(`/api/sessions/${sessionId}/resume`, { method: "POST", body: form });
    return data.workspace;
  });

  const reviewResume = (action: string, patch?: AnyRecord) => run("正在保存确认…", async () => {
    const data = await api<{ workspace: Workspace }>(`/api/sessions/${sessionId}/resume/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, patch, response_id: crypto.randomUUID(), attests_pdf_source: action === "correct" }),
    });
    return data.workspace;
  });

  const buildCandidate = () => run("Agent 正在构建画像…", async () => {
    const data = await api<{ workspace: Workspace }>(`/api/sessions/${sessionId}/candidate`, { method: "POST" });
    return data.workspace;
  });

  const submitInteraction = (action: string, payload: AnyRecord = {}) => run("正在更新画像…", async () => {
    const data = await api<{ workspace: Workspace }>(`/api/sessions/${sessionId}/candidate/interaction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, action, response_id: crypto.randomUUID() }),
    });
    return data.workspace;
  });

  const uploadMaterial = (file: File) => run("正在读取补充材料…", async () => {
    const form = new FormData();
    form.append("payload", JSON.stringify({ action: "upload", response_id: crypto.randomUUID() }));
    form.append("file", file);
    const data = await api<{ workspace: Workspace }>(`/api/sessions/${sessionId}/candidate/interaction`, { method: "POST", body: form });
    return data.workspace;
  });

  return (
    <div className="app-shell">
      <AppHeader active={Boolean(sessionId)} onReset={reset} />
      {error ? <div className="error-banner" role="alert"><strong>暂时无法继续</strong><span>{error}</span><button onClick={() => setError("")} type="button">×</button></div> : null}
      {!sessionId ? (
        <Landing busy={busy} onStart={start} />
      ) : !workspace ? (
        <main className="loading-workspace"><div className="loading-mark">C</div><strong>{busy || "正在恢复工作区…"}</strong><small>Session {sessionId.slice(0, 18)}…</small></main>
      ) : (
        <main className="workspace-layout">
          <ProgressRail workspace={workspace} />
          <div className="workspace-content">
            <div className="workspace-topline">
              <div><span>SESSION</span><code>{workspace.session.session_id.slice(0, 22)}…</code></div>
              <div className={workspace.model.configured ? "model-status ready" : "model-status"}><i />{workspace.model.configured ? `${workspace.model.provider ?? "LLM"} · ${workspace.model.model ?? "已配置"}` : "模型配置待检查"}</div>
            </div>
            {workspace.session.status !== "active" && !workspace.session.pending_request ? <SessionStopped status={workspace.session.status} />
              : workspace.resume_review ? <ResumeReview busy={busy} onAction={reviewResume} review={workspace.resume_review} />
              : workspace.candidate_interaction ? <CandidateQuestions busy={busy} interaction={workspace.candidate_interaction} onSubmit={submitInteraction} onUpload={uploadMaterial} />
              : workspace.candidate_profile ? <CandidateProfileView workspace={workspace} />
              : workspace.resume ? <ResumeReady busy={busy} onBuild={buildCandidate} workspace={workspace} />
              : <UploadResume busy={busy} onUpload={uploadResume} />}
          </div>
        </main>
      )}
      <footer><span>Campus Agent · Local Preview</span><span>证据优先 · 人工确认 · 版本可追溯</span></footer>
    </div>
  );
}
