import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  FilePlus2,
  FileText,
  GitBranch,
  HeartPulse,
  Loader2,
  Network,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Upload,
  ExternalLink,
  X,
} from "lucide-react";
import { uploadTextDocument, uploadTxtDocument } from "./api/documents";
import { createKnowledgeBase, listKnowledgeBases } from "./api/knowledge";
import { getLLMConfig, testLLMConfig, updateLLMConfig } from "./api/llm";
import { submitQueryFeedback } from "./api/feedback";
import { searchKnowledgeBase } from "./api/search";
import { getWikiGraph, getWikiStatus, lintWiki, listAllWikiPages, queryWiki, readWikiPage, saveWikiPage } from "./api/wiki";
import type { KnowledgeBase, LLMConfigurationInput, LLMConfigStatus, SearchResponse, WikiGraph, WikiLint, WikiPage, WikiQueryResponse, WikiStatus } from "./api/types";

type Notice = { type: "success" | "error"; message: string } | null;
type PageFilter = "all" | "source" | "topic" | "query";
type QueryMode = "wiki" | "rag";
type LLMFormState = Required<Pick<LLMConfigurationInput, "enabled" | "base_url" | "api_key" | "model" | "temperature" | "max_tokens" | "timeout_seconds">>;

const pageLabels: Record<string, string> = {
  overview: "概览",
  source: "来源",
  topic: "主题",
  entity: "实体",
  query: "查询",
  page: "页面",
};

/** The folio states what kind of record this page is in the wiki-first model. */
const pageKinds: Record<string, string> = {
  overview: "自持知识 · 综合入口",
  source: "外部来源 · 快照摘要",
  topic: "自持知识 · 持续维护",
  entity: "自持知识 · 实体条目",
  query: "自持知识 · 结晶回答",
  page: "自持知识 · 自由页面",
};

const filters: Array<{ id: PageFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "source", label: "来源" },
  { id: "topic", label: "主题" },
  { id: "query", label: "查询" },
];

type CanvasView = "page" | "graph" | "lint";

/** The canvas shows one sheet at a time: the page, its link structure, or a health check. */
const canvasViews: Array<{ id: CanvasView; label: string; icon: typeof FileText }> = [
  { id: "page", label: "页面", icon: FileText },
  { id: "graph", label: "图谱", icon: Network },
  { id: "lint", label: "检查", icon: ShieldCheck },
];

const issueLabels: Record<string, string> = {
  broken_link: "断链",
  orphan_page: "孤立页",
  missing_summary: "缺摘要",
};

/** Front matter values are quoted so titles containing `:` stay parseable. */
function yamlText(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function defaultLLMForm(status?: LLMConfigStatus | null): LLMFormState {
  return {
    enabled: status?.enabled ?? false,
    base_url: status?.base_url ?? "https://api.openai.com/v1",
    api_key: "",
    model: status?.model ?? "",
    temperature: status?.temperature ?? 0.2,
    max_tokens: status?.max_tokens ?? 1200,
    timeout_seconds: status?.timeout_seconds ?? 60,
  };
}

export default function App() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [status, setStatus] = useState<WikiStatus | null>(null);
  const [pages, setPages] = useState<WikiPage[]>([]);
  const [activePage, setActivePage] = useState<WikiPage | null>(null);
  const [pageFilter, setPageFilter] = useState<PageFilter>("all");
  const [query, setQuery] = useState("");
  const [queryMode, setQueryMode] = useState<QueryMode>("wiki");
  const [queryResult, setQueryResult] = useState<WikiQueryResponse | null>(null);
  const [ragResult, setRagResult] = useState<SearchResponse | null>(null);
  const [lintResult, setLintResult] = useState<WikiLint | null>(null);
  const [graph, setGraph] = useState<WikiGraph | null>(null);
  const [view, setView] = useState<CanvasView>("page");
  const [creatingPath, setCreatingPath] = useState<string | null>(null);
  const [serverLLM, setServerLLM] = useState<LLMConfigStatus | null>(null);
  const [llmForm, setLLMForm] = useState<LLMFormState>(() => defaultLLMForm());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showIngest, setShowIngest] = useState(false);
  const [showModelSettings, setShowModelSettings] = useState(false);

  const notify = useCallback((next: Notice) => {
    setNotice(next);
    if (next) window.setTimeout(() => setNotice(null), 4200);
  }, []);

  const refresh = useCallback(async (knowledgeBase: KnowledgeBase) => {
    setLoading(true);
    try {
      const [nextStatus, nextPages, nextGraph] = await Promise.all([
        getWikiStatus(knowledgeBase.id),
        listAllWikiPages(knowledgeBase.id),
        getWikiGraph(knowledgeBase.id),
      ]);
      setStatus(nextStatus);
      setPages(nextPages);
      setGraph(nextGraph);
      setActivePage((current) =>
        nextPages.find((item) => item.path === current?.path)
        ?? nextPages.find((item) => item.path === "wiki/overview.md")
        ?? nextPages[0]
        ?? null,
      );
      setLintResult(null);
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "知识库加载失败" });
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    void listKnowledgeBases(1, 50)
      .then((page) => {
        setKnowledgeBases(page.items);
        setSelected((current) => current ?? page.items[0] ?? null);
      })
      .catch((error: unknown) => notify({
        type: "error",
        message: error instanceof Error ? error.message : "知识库加载失败",
      }));
  }, [notify]);

  useEffect(() => {
    void getLLMConfig()
      .then((config) => {
        setServerLLM(config);
        setLLMForm((current) => ({ ...defaultLLMForm(config), api_key: current.api_key }));
      })
      .catch(() => setServerLLM(null));
  }, []);

  useEffect(() => {
    setQueryResult(null);
    setRagResult(null);
    if (selected) {
      void refresh(selected);
    } else {
      setStatus(null);
      setPages([]);
      setActivePage(null);
    }
  }, [refresh, selected]);

  const visiblePages = useMemo(() => {
    const items = pages.filter((page) => pageFilter === "all" || page.page_type === pageFilter);
    // purpose.md states why the workspace exists, so it leads the full list.
    return [...items].sort((a, b) => Number(b.path === "purpose.md") - Number(a.path === "purpose.md"));
  }, [pageFilter, pages]);

  const pageCounts = useMemo(() => {
    const counts: Record<PageFilter, number> = { all: pages.length, source: 0, topic: 0, query: 0 };
    for (const page of pages) {
      if (page.page_type === "source" || page.page_type === "topic" || page.page_type === "query") {
        counts[page.page_type] += 1;
      }
    }
    return counts;
  }, [pages]);

  const llmReady = Boolean(
    llmForm.enabled
      && llmForm.base_url.trim()
      && llmForm.model.trim()
      && (llmForm.api_key.trim() || serverLLM?.api_key_configured),
  );

  async function openPage(path: string) {
    if (!selected) return;
    try {
      setActivePage(await readWikiPage(selected.id, normalizePagePath(path)));
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "页面加载失败" });
    }
  }

  async function handleCreate(name: string, description: string) {
    try {
      const created = await createKnowledgeBase({ name, description });
      setKnowledgeBases((current) => [created, ...current]);
      setSelected(created);
      setShowCreate(false);
      notify({ type: "success", message: "知识库已创建，工作区已准备好" });
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "创建失败" });
    }
  }

  async function handleIngest(title: string, content: string, file?: File) {
    if (!selected) return false;
    setLoading(true);
    try {
      if (file) {
        await uploadTxtDocument(selected.id, title, file);
      } else {
        await uploadTextDocument(selected.id, { title, content });
      }
      await refresh(selected);
      notify({ type: "success", message: "来源已纳入知识库" });
      return true;
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "来源摄取失败" });
      return false;
    } finally {
      setLoading(false);
    }
  }

  async function handleQuery(save = false) {
    if (!selected || !query.trim()) return;
    setLoading(true);
    try {
      if (queryMode === "wiki") {
        const result = await queryWiki(selected.id, {
          query,
          top_k: 8,
          ...(save ? { save_as: query } : {}),
        });
        setQueryResult(result);
        setRagResult(null);
        if (result.saved_path) await refresh(selected);
        notify({ type: "success", message: result.answer_mode === "llm" ? "模型已基于知识页面生成回答" : result.saved_path ? "回答已保存为知识页面" : "已从知识页面完成检索" });
      } else {
        const result = await searchKnowledgeBase({ knowledge_base_id: selected.id, query, top_k: 8 });
        setRagResult(result);
        setQueryResult(null);
        notify({
          type: "success",
          message: result.answer_mode === "llm" ? "模型已基于原始资料生成回答" : result.results.length ? "已找到相关原始资料" : "没有找到匹配的原始资料",
        });
      }
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "查询失败" });
    } finally {
      setLoading(false);
    }
  }

  function handleQueryModeChange(mode: QueryMode) {
    setQueryMode(mode);
    setQueryResult(null);
    setRagResult(null);
  }

  async function handleLint() {
    if (!selected) return;
    setLoading(true);
    try {
      setLintResult(await lintWiki(selected.id));
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "检查失败" });
    } finally {
      setLoading(false);
    }
  }

  function handleViewChange(next: CanvasView) {
    setView(next);
    if (next === "lint" && selected) {
      void handleLint();
    }
  }

  /** Turns an unresolved [[link]] into a real page so the wiki keeps its shape. */
  async function handleCreatePage(target: { label: string; path: string }) {
    if (!selected) return;
    setCreatingPath(target.path);
    try {
      const content = [
        "---",
        "type: page",
        `title: ${yamlText(target.label)}`,
        `summary: ${yamlText("由断链创建，内容待补充。")}`,
        `date: ${new Date().toISOString().slice(0, 10)}`,
        "---",
        "",
        `# ${target.label}`,
        "",
        "这一页由知识库检查从断链创建，等待补充来源与内容。",
        "",
      ].join("\n");
      await saveWikiPage(selected.id, target.path, content);
      await refresh(selected);
      await openPage(target.path);
      setView("page");
      notify({ type: "success", message: `已创建 ${target.path}` });
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "创建页面失败" });
    } finally {
      setCreatingPath(null);
    }
  }

  async function handleSavePage(content: string) {
    if (!selected || !activePage) return;
    setSaving(true);
    try {
      setActivePage(await saveWikiPage(selected.id, activePage.path, content));
      await refresh(selected);
      notify({ type: "success", message: "页面已保存" });
    } catch (error) {
      notify({ type: "error", message: error instanceof Error ? error.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  function handleLLMFormChange(next: LLMFormState) {
    setLLMForm(next);
  }

  return (
    <main className="app-shell">
      <header className="app-topbar">
        <div className="product-lockup">
          <div className="brand-mark"><BookOpen size={15} /></div>
          <span className="product-wordmark">AgentKB</span>
        </div>
        <div className="workspace-identity">
          <h1>{selected?.name ?? "知识库工作台"}</h1>
          <span className="identity-path">{activePage?.path ?? "未选择页面"}</span>
        </div>
        <div className="kpi-strip" aria-label="工作区状态">
          <div><strong>{status?.source_count ?? 0}</strong><span>来源</span></div>
          <div><strong>{status?.page_count ?? 0}</strong><span>页面</span></div>
          <div><strong>{status?.topic_count ?? 0}</strong><span>主题</span></div>
          <div className={status?.broken_link_count ? "attention" : ""}><strong>{status?.broken_link_count ?? 0}</strong><span>断链</span></div>
        </div>
        <div className="topbar-actions">
          <button className={`model-status-button ${llmReady ? "ready" : ""}`} onClick={() => setShowModelSettings(true)}>
            <span className="model-status-dot" />
            <span>{llmReady ? (llmForm.model || "模型已就绪") : "配置模型"}</span>
            <Settings2 size={15} />
          </button>
          <button className="icon-button" title="刷新工作区" onClick={() => selected && void refresh(selected)} disabled={!selected || loading}>
            <RefreshCw size={17} className={loading ? "spin" : ""} />
          </button>
          <button className="secondary-button compact" onClick={() => setShowIngest(true)} disabled={!selected}>
            <Upload size={16} /> 添加来源
          </button>
          <button className="primary-button compact" onClick={() => setShowCreate(true)}>
            <Plus size={17} /> 新建知识库
          </button>
        </div>
      </header>

      {notice ? (
        <div className={`notice ${notice.type}`}>
          {notice.type === "success" ? <Check size={16} /> : <AlertCircle size={16} />}
          <span>{notice.message}</span>
          <button className="notice-close" title="关闭提示" onClick={() => setNotice(null)}><X size={14} /></button>
        </div>
      ) : null}

      <div className="workspace-frame">
        <aside className="library-sidebar">
          <div className="rail-label"><span>知识库</span><em>{knowledgeBases.length}</em></div>

          <div className="library-list">
            {knowledgeBases.map((knowledgeBase) => (
              <button
                className={`library-item ${selected?.id === knowledgeBase.id ? "selected" : ""}`}
                key={knowledgeBase.id}
                onClick={() => setSelected(knowledgeBase)}
              >
                <span className="library-item-copy">
                  <strong>{knowledgeBase.name}</strong>
                  <small>{knowledgeBase.description || "未添加描述"}</small>
                </span>
                <ChevronRight size={15} />
              </button>
            ))}
            {!knowledgeBases.length && !loading ? (
              <div className="empty-copy">还没有知识库。<br />创建一个空间开始整理。</div>
            ) : null}
          </div>

          <button className="new-library-button" onClick={() => setShowCreate(true)}>
            <Plus size={16} /> 新建知识库
          </button>

          <div className="workspace-health">
            <div className="health-heading"><Activity size={13} /><span>最近活动</span></div>
            <div className="activity-copy">
              <span>{status?.recent_activity[0] ?? "等待第一条知识活动"}</span>
            </div>
          </div>
        </aside>

        <section className="page-explorer" aria-label="页面导航">
          <div className="explorer-header">
            <div className="rail-label"><span>目录</span><em>{pages.length}</em></div>
          </div>
          <div className="page-filters" role="tablist" aria-label="页面分类">
            {filters.map((filter) => (
              <button
                key={filter.id}
                role="tab"
                aria-selected={pageFilter === filter.id}
                className={pageFilter === filter.id ? "active" : ""}
                onClick={() => setPageFilter(filter.id)}
              >
                {filter.label}<em>{pageCounts[filter.id]}</em>
              </button>
            ))}
          </div>
          <div className="page-list">
            {visiblePages.map((page) => (
              <button
                className={`page-row type-${page.page_type} ${activePage?.path === page.path ? "active" : ""}`}
                key={page.path}
                onClick={() => void openPage(page.path)}
              >
                <span className={`page-type type-${page.page_type}`}><FileText size={15} /></span>
                <span className="page-row-copy">
                  <strong>{page.title}</strong>
                  <small>{page.summary || "暂无摘要"}</small>
                  <em>{page.path}</em>
                </span>
                <span className="page-links"><strong>{page.inbound_links}</strong><small>链接</small></span>
              </button>
            ))}
            {!visiblePages.length ? <div className="empty-copy">这个分类还没有页面。</div> : null}
          </div>
          <div className="explorer-footer">
            <GitBranch size={14} /> Markdown 页面之间通过链接组织
          </div>
        </section>

        <section className={`knowledge-canvas ${loading ? "is-loading" : ""}`}>
          <div className="canvas-toolbar">
            <div className="canvas-context">
              {view === "page" ? (
                <>
                  <div className="path-label"><GitBranch size={15} />{activePage?.path ?? "选择一个页面"}</div>
                  <div className="canvas-actions">
                    {activePage ? <span className={`page-kind kind-${activePage.page_type}`}>{pageLabelOf(activePage)}</span> : null}
                    <button className="secondary-button compact" onClick={() => activePage && void handleSavePage(activePage.content)} disabled={!activePage || saving}>
                      {saving ? <Loader2 size={15} className="spin" /> : <Check size={15} />} 保存
                    </button>
                  </div>
                </>
              ) : view === "graph" ? (
                <div className="path-label">
                  <Network size={15} />
                  知识图谱 · {graph?.nodes.length ?? 0} 页面 · {graph?.edges.length ?? 0} 链接 · 由 Markdown 链接派生
                </div>
              ) : (
                <div className="path-label">
                  <ShieldCheck size={15} />
                  知识库检查 · {lintResult?.checked_pages ?? 0} 页面 · {lintResult?.issues.length ?? 0} 项问题
                </div>
              )}
            </div>
            <div className="view-switch" role="tablist" aria-label="画布视图">
              {canvasViews.map((item) => (
                <button
                  key={item.id}
                  role="tab"
                  aria-selected={view === item.id}
                  className={view === item.id ? "active" : ""}
                  onClick={() => handleViewChange(item.id)}
                  disabled={!selected}
                >
                  <item.icon size={14} />{item.label}
                </button>
              ))}
            </div>
          </div>

          {view === "page" ? (
            <div className="reading-stage">
              {activePage ? (
                <PageReader
                  key={activePage.path}
                  page={activePage}
                  graph={graph}
                  creatingPath={creatingPath}
                  onSave={handleSavePage}
                  onOpenPage={(path) => void openPage(path)}
                  onCreatePage={handleCreatePage}
                  saving={saving}
                />
              ) : (
                <div className="reader-empty">
                  <BookOpen size={32} />
                  <h3>选择一个知识页面</h3>
                  <p>从左侧目录进入概览、来源或主题页面。</p>
                </div>
              )}
            </div>
          ) : view === "graph" ? (
            <GraphSheet graph={graph} onOpenPage={(path) => { setView("page"); void openPage(path); }} />
          ) : (
            <LintSheet
              report={lintResult}
              creatingPath={creatingPath}
              onOpenPage={(path) => { setView("page"); void openPage(path); }}
              onCreatePage={handleCreatePage}
              onRecheck={() => void handleLint()}
            />
          )}

          {view === "page" ? (
            <QueryDock
              disabled={!selected || loading}
              mode={queryMode}
              onModeChange={handleQueryModeChange}
              query={query}
              setQuery={setQuery}
              onSubmit={() => void handleQuery(false)}
              onSave={() => void handleQuery(true)}
              llmReady={llmReady}
              model={llmForm.model}
            >
              {queryResult ? <QueryResult knowledgeBaseId={selected?.id ?? null} result={queryResult} onOpenPage={(path) => void openPage(path)} /> : null}
              {ragResult ? <RagResult knowledgeBaseId={selected?.id ?? null} result={ragResult} /> : null}
            </QueryDock>
          ) : null}
        </section>
      </div>

      {showCreate ? <CreateDialog onClose={() => setShowCreate(false)} onSubmit={handleCreate} /> : null}
      {showIngest && selected ? <IngestDialog disabled={loading} onClose={() => setShowIngest(false)} onSubmit={handleIngest} /> : null}
      {showModelSettings ? <ModelSettingsDialog form={llmForm} serverConfig={serverLLM} onClose={() => setShowModelSettings(false)} onChange={handleLLMFormChange} onNotice={notify} onServerConfig={setServerLLM} /> : null}
    </main>
  );
}

function QueryDock({ children, disabled, llmReady, mode, model, onModeChange, onSave, onSubmit, query, setQuery }: {
  children: React.ReactNode;
  disabled: boolean;
  llmReady: boolean;
  mode: QueryMode;
  model: string;
  onModeChange: (mode: QueryMode) => void;
  onSave: () => void;
  onSubmit: () => void;
  query: string;
  setQuery: (value: string) => void;
}) {
  const wikiMode = mode === "wiki";
  return (
    <section className="query-dock" aria-label="知识库查询">
      {children ? <div className="query-results">{children}</div> : null}
      <div className="query-surface">
        <div className="query-topline">
          <div className="query-mode-control" role="group" aria-label="查询模式">
            <button type="button" className={wikiMode ? "active" : ""} onClick={() => onModeChange("wiki")} disabled={disabled}><BookOpen size={14} /> 页面优先</button>
            <button type="button" className={!wikiMode ? "active" : ""} onClick={() => onModeChange("rag")} disabled={disabled}><Search size={14} /> 原始资料 RAG</button>
          </div>
          <div className="query-actions">
            {llmReady ? <span className="query-model-chip" title={model ? `使用 ${model} 综合回答` : "使用已配置模型综合回答"}><Sparkles size={13} /> 模型综合</span> : <span className="query-local-chip">本地检索</span>}
            {wikiMode ? <button className="icon-button subtle" title="将本次回答保存为页面" onClick={onSave} disabled={disabled || !query.trim()}><FilePlus2 size={16} /></button> : null}
          </div>
        </div>
        <div className="query-composer">
          <Sparkles size={18} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") onSubmit(); }}
            placeholder={wikiMode ? "向这个知识库提问…" : "从原始资料中检索…"}
            disabled={disabled}
          />
          <button className="query-send" title={wikiMode ? "查询知识库" : "搜索原始资料"} onClick={onSubmit} disabled={disabled || !query.trim()}>
            <ArrowUp size={17} />
          </button>
        </div>
      </div>
    </section>
  );
}

function IngestDialog({ disabled, onClose, onSubmit }: {
  disabled: boolean;
  onClose: () => void;
  onSubmit: (title: string, content: string, file?: File) => Promise<boolean>;
}) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog ingest-dialog" role="dialog" aria-modal="true" aria-labelledby="ingest-title">
        <div className="dialog-header">
          <div><p className="eyebrow">New source</p><h2 id="ingest-title">添加来源</h2></div>
          <button className="icon-button" title="关闭" onClick={onClose}><X size={17} /></button>
        </div>
        <p>来源会保留原始快照，并更新可维护的知识页面。</p>
        <label>来源标题<input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：研究笔记" disabled={disabled} /></label>
        <label>来源内容<textarea value={content} onChange={(event) => setContent(event.target.value)} rows={9} placeholder="粘贴文章、笔记或会议记录…" disabled={disabled} /></label>
        <div className="dialog-actions">
          <label className={`file-button ${disabled ? "disabled" : ""}`}><Upload size={15} /> 导入 txt<input type="file" accept=".txt,text/plain" disabled={disabled} onChange={(event) => { const file = event.target.files?.[0]; if (file) void onSubmit(file.name.replace(/\.txt$/i, ""), "", file).then((complete) => { if (complete) onClose(); }); event.currentTarget.value = ""; }} /></label>
          <button className="primary-button" disabled={disabled || !title.trim() || !content.trim()} onClick={() => { void onSubmit(title, content).then((complete) => { if (complete) onClose(); }); }}><Send size={15} /> 纳入知识库</button>
        </div>
      </section>
    </div>
  );
}

function PageReader({ page, graph, creatingPath, onSave, onCreatePage, onOpenPage, saving }: {
  page: WikiPage;
  graph: WikiGraph | null;
  creatingPath: string | null;
  onSave: (content: string) => Promise<void>;
  onCreatePage: (target: { label: string; path: string }) => Promise<void>;
  onOpenPage: (path: string) => void;
  saving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(page.content);
  useEffect(() => { setDraft(page.content); setEditing(false); }, [page.path, page.content]);
  const body = pageBody(page.content, page.title);
  const summary = page.summary?.trim() ?? "";
  /* Front matter summaries are often just the first sentence of the body; an
     abstract that repeats the opening line reads like a rendering bug. */
  const abstractRepeatsBody = summary.length > 0
    && body.replace(/\s+/g, "").slice(0, 240).includes(summary.replace(/\s+/g, "").slice(0, 24));
  return (
    <article className="reader-sheet">
      <aside className="reader-margin" aria-label="页面信息">
        <div className="margin-block"><dt>入链</dt><dd>{page.inbound_links}</dd></div>
        <div className="margin-block"><dt>出链</dt><dd>{page.outbound_links}</dd></div>
        <div className="margin-block"><dt>更新</dt><dd>{new Date(page.updated_at).toLocaleDateString("zh-CN")}</dd></div>
        <div className="margin-block"><dt>路径</dt><dd className="path">{page.path}</dd></div>
      </aside>
      <div className="reader-page">
        <div className="reader-title">
          <div>
            <p className="folio">{page.path === "purpose.md" ? "宗旨 · 工作区意图" : pageKinds[page.page_type] ?? pageKinds.page}</p>
            <h2>{page.title}</h2>
          </div>
          <button className="text-button" onClick={() => setEditing((current) => !current)}>{editing ? "取消" : "编辑"}</button>
        </div>
        {summary && !abstractRepeatsBody ? <p className="reader-abstract">{summary}</p> : null}
        {editing ? (
          <><textarea className="markdown-editor" value={draft} onChange={(event) => setDraft(event.target.value)} rows={22} /><button className="primary-button save-editor" onClick={() => void onSave(draft)} disabled={saving}><Check size={15} /> 保存页面</button></>
        ) : (
          <>
            <RichText className="reader-body" content={body} onOpenPage={onOpenPage} />
            <div className="reader-meta"><span>{page.inbound_links} 个入链</span><span>{page.outbound_links} 个页面链接</span><span>{page.path}</span></div>
            <PageLinks
              page={page}
              graph={graph}
              creatingPath={creatingPath}
              onCreatePage={onCreatePage}
              onOpenPage={onOpenPage}
            />
          </>
        )}
      </div>
    </article>
  );
}

/** Strips the front matter and the leading H1 when it merely repeats the title. */
function pageBody(content: string, title: string): string {
  const lines = content.replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/, "").split("\n");
  let index = 0;
  while (index < lines.length && !lines[index].trim()) index += 1;
  const heading = lines[index]?.match(/^#\s+(.*)$/);
  if (heading && heading[1].replace(/\*\*/g, "").trim() === title.trim()) {
    lines.splice(0, index + 1);
  }
  return lines.join("\n");
}

function withMarkdownExtension(target: string): string {
  return target.endsWith(".md") ? target : `${target}.md`;
}

type TextBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; lines: string[] };

/** A deliberately small Markdown subset: enough to typeset a knowledge page well. */
function parseBlocks(content: string): TextBlock[] {
  const blocks: TextBlock[] = [];
  let current: TextBlock | null = null;
  const flush = () => { if (current) blocks.push(current); current = null; };

  for (const raw of content.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim() || /^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) { flush(); continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) { flush(); blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] }); continue; }

    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    if (bullet) {
      if (current && current.kind === "list" && !current.ordered) current.items.push(bullet[1]);
      else { flush(); current = { kind: "list", ordered: false, items: [bullet[1]] }; }
      continue;
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ordered) {
      if (current && current.kind === "list" && current.ordered) current.items.push(ordered[1]);
      else { flush(); current = { kind: "list", ordered: true, items: [ordered[1]] }; }
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      if (current && current.kind === "quote") current.lines.push(quote[1]);
      else { flush(); current = { kind: "quote", lines: [quote[1]] }; }
      continue;
    }

    if (current && current.kind === "paragraph") current.lines.push(line);
    else { flush(); current = { kind: "paragraph", lines: [line] }; }
  }

  flush();
  return blocks;
}

const INLINE_TOKEN = /(\*\*[^*]+\*\*|`[^`]+`|\[\[[^\]]+\]\]|\[\d+\])/g;

function renderInline(
  text: string,
  handlers: { onOpenPage?: (path: string) => void; onCite?: (index: number) => void },
): React.ReactNode[] {
  return text.split(INLINE_TOKEN).filter(Boolean).map((part, index) => {
    const key = `${text.slice(0, 8)}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={key}>{part.slice(1, -1)}</code>;

    const cite = handlers.onCite ? part.match(/^\[(\d+)\]$/) : null;
    if (cite) {
      const reference = Number(cite[1]);
      return <button type="button" className="answer-ref" key={key} onClick={() => handlers.onCite?.(reference)}>{reference}</button>;
    }

    const wiki = part.match(/^\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]$/);
    if (wiki) {
      const target = wiki[1].trim();
      const label = (wiki[2] ?? target).trim();
      if (!handlers.onOpenPage) return <span key={key}>{label}</span>;
      return <button type="button" className="wiki-link" key={key} onClick={() => handlers.onOpenPage?.(withMarkdownExtension(target))}>{label}</button>;
    }

    return <span key={key}>{part}</span>;
  });
}

function RichText({ className, content, onOpenPage, onCite }: {
  className: string;
  content: string;
  onOpenPage?: (path: string) => void;
  onCite?: (index: number) => void;
}) {
  return (
    <div className={className}>
      {parseBlocks(content).map((block, index) => {
        if (block.kind === "heading") {
          const Tag = block.level === 1 ? "h1" : block.level === 2 ? "h3" : "h4";
          return <Tag key={index}>{renderInline(block.text, { onOpenPage })}</Tag>;
        }
        if (block.kind === "list") {
          const List = block.ordered ? "ol" : "ul";
          return (
            <List key={index}>
              {block.items.map((item, itemIndex) => <li key={itemIndex}>{renderInline(item, { onOpenPage, onCite })}</li>)}
            </List>
          );
        }
        if (block.kind === "quote") {
          return <blockquote key={index}>{renderInline(block.lines.join(" "), { onOpenPage, onCite })}</blockquote>;
        }
        return <p key={index}>{renderInline(block.lines.join(" "), { onOpenPage, onCite })}</p>;
      })}
    </div>
  );
}

function QueryResult({ knowledgeBaseId, result, onOpenPage }: { knowledgeBaseId: number | null; result: WikiQueryResponse; onOpenPage: (path: string) => void }) {
  const [activeRef, setActiveRef] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const relatedCount = result.results.filter((item) => item.related).length;

  function focusReference(reference: number) {
    setActiveRef(reference);
    listRef.current?.querySelector(`[data-ref-anchor="${reference}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  return <div className="result-block">
    <div className="result-heading"><Sparkles size={15} /><span>{result.answer_mode === "llm" ? "模型综合回答" : "页面回答"}</span><small>{result.answer_mode === "llm" && result.model ? result.model : `${result.results.length} 个引用${relatedCount ? ` · ${relatedCount} 个沿链接关联` : ""}`}</small></div>
    <RichText className="answer-copy" content={result.answer} onOpenPage={onOpenPage} onCite={focusReference} />
    {result.model_error ? <p className="result-fallback"><AlertCircle size={13} /> {result.model_error}</p> : null}
    {result.results.length ? <div className="citation-list" ref={listRef}>{result.results.map((item, index) => <button className={`${activeRef === index + 1 ? "active" : ""} ${item.related ? "related" : ""}`} data-ref-anchor={index + 1} data-ref={index + 1} key={item.path} onClick={() => onOpenPage(item.path)}><span>{item.title}</span>{item.related ? <em className="cite-related">关联</em> : null}<small>{item.path}</small></button>)}</div> : null}
    <FeedbackControl
      knowledgeBaseId={knowledgeBaseId}
      mode="wiki"
      query={result.query}
      answer={result.answer}
      answerMode={result.answer_mode}
      model={result.model}
      sourcePaths={result.results.map((item) => item.path)}
    />
  </div>;
}

function RagResult({ knowledgeBaseId, result }: { knowledgeBaseId: number | null; result: SearchResponse }) {
  const [activeRef, setActiveRef] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  function focusReference(reference: number) {
    setActiveRef(reference);
    listRef.current?.querySelector(`[data-ref-anchor="${reference}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  return <div className="result-block rag-result">
    <div className="result-heading"><Search size={15} /><span>{result.answer_mode === "llm" ? "模型综合回答" : "原始资料匹配"}</span><small>{result.answer_mode === "llm" && result.model ? result.model : `${result.results.length} 个片段`}</small></div>
    {result.answer ? <RichText className="answer-copy" content={result.answer} onCite={focusReference} /> : null}
    {result.model_error ? <p className="result-fallback"><AlertCircle size={13} /> {result.model_error}</p> : null}
    {result.results.length ? <div className="rag-result-list" ref={listRef}>{result.results.map((item, index) => <article className={activeRef === index + 1 ? "active" : ""} data-ref-anchor={index + 1} key={item.chunk_id ?? `${item.document_id}-${item.chunk_index}`}><div><strong>{item.title}</strong><span>{Math.round(item.score * 100)}%</span></div><p>{item.chunk}</p><div className="rag-source-meta"><small>{[item.source_platform, item.source_author, item.source_account ? `@${item.source_account}` : null].filter(Boolean).join(" · ") || `来源 #${item.document_id}`} · 片段 {item.chunk_index ?? "-"}</small>{item.source_policy && item.source_policy !== "full_text" ? <small className="source-policy">{item.source_policy === "excerpt" ? "节选快照" : "链接说明"}</small> : null}{item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> 原帖</a> : null}</div></article>)}</div> : <p className="answer-copy">没有找到匹配的原始资料片段。</p>}
    <FeedbackControl
      knowledgeBaseId={knowledgeBaseId}
      mode="rag"
      query={result.query}
      answer={result.answer ?? ""}
      answerMode={result.answer_mode}
      model={result.model}
      sourcePaths={result.results.map((item) => `document:${item.document_id}#${item.chunk_index ?? "-"}`)}
    />
  </div>;
}

/**
 * One rating per answer. A thumbs down asks why before it is recorded, because a
 * bare negative tells a later evaluation nothing it can act on.
 */
function FeedbackControl({ answer, answerMode, knowledgeBaseId, mode, model, query, sourcePaths }: {
  answer: string;
  answerMode: string | null;
  knowledgeBaseId: number | null;
  mode: "wiki" | "rag";
  model: string | null;
  query: string;
  sourcePaths: string[];
}) {
  const [rating, setRating] = useState<1 | -1 | 0>(0);
  const [note, setNote] = useState("");
  const [asking, setAsking] = useState(false);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const rootRef = useRef<HTMLDivElement>(null);

  // A new or re-run answer starts from a clean rating.
  useEffect(() => {
    setRating(0);
    setNote("");
    setAsking(false);
    setState("idle");
  }, [query, answer]);

  // The rating line sits at the bottom of a scrollable sheet, so the box that
  // opens after a thumbs down would otherwise appear below the fold.
  useEffect(() => {
    if (asking) rootRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [asking]);

  async function send(next: 1 | -1, reason: string) {
    if (!knowledgeBaseId) return;
    setRating(next);
    setAsking(false);
    setState("saving");
    try {
      await submitQueryFeedback(knowledgeBaseId, {
        mode,
        query,
        rating: next,
        note: reason.trim() || null,
        answer: answer.slice(0, 8_000) || null,
        answer_mode: answerMode,
        model,
        source_paths: sourcePaths.slice(0, 50),
      });
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  return (
    <div className="feedback" ref={rootRef}>
      <div className="feedback-ask">
        <span>这个回答有用吗</span>
        <button
          type="button"
          className={`feedback-button positive ${rating === 1 ? "selected" : ""}`}
          title="有用"
          aria-label="有用"
          disabled={state === "saving"}
          onClick={() => void send(1, "")}
        >
          <ThumbsUp size={13} />
        </button>
        <button
          type="button"
          className={`feedback-button negative ${rating === -1 ? "selected" : ""}`}
          title="没用"
          aria-label="没用"
          disabled={state === "saving"}
          onClick={() => { setRating(-1); setState("idle"); setAsking(true); }}
        >
          <ThumbsDown size={13} />
        </button>
        {state === "saved" ? <span className="feedback-saved">已记录</span> : null}
        {state === "failed" ? <span className="feedback-failed">记录失败，请重试</span> : null}
      </div>
      {asking ? (
        <form
          className="feedback-note"
          onSubmit={(event) => { event.preventDefault(); void send(-1, note); }}
        >
          <textarea
            autoFocus
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="哪里不对？可以留空"
            disabled={state === "saving"}
          />
          <div className="feedback-note-actions">
            <button type="button" className="text-button" onClick={() => { setAsking(false); setRating(0); }}>取消</button>
            <button type="submit" className="secondary-button compact" disabled={state === "saving"}>提交反馈</button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------------------
   Link structure: a derived view of the wiki, never a separate source of truth
   -------------------------------------------------------------------------- */

type GraphNode = WikiGraph["nodes"][number];
type PlacedNode = GraphNode & { x: number; y: number; radius: number; degree: number };

/** Deterministic PRNG so the graph keeps the same shape between renders. */
function seededRandom(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Fruchterman-Reingold, hand-rolled. A wiki is normally tens of pages, so an
 * O(n²) pass is cheap and the graph stays free of a WebGL layout dependency.
 */
function layoutGraph(graph: WikiGraph, width: number, height: number): PlacedNode[] {
  const nodes = graph.nodes;
  const count = nodes.length;
  if (!count) return [];

  const index = new Map(nodes.map((node, position) => [node.id, position]));
  const links = graph.edges
    .map((edge) => [index.get(edge.source), index.get(edge.target)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] !== undefined && pair[1] !== undefined);
  const degrees = new Array<number>(count).fill(0);
  for (const [a, b] of links) {
    degrees[a] += 1;
    degrees[b] += 1;
  }

  const random = seededRandom(20260822);
  const xs = new Float64Array(count);
  const ys = new Float64Array(count);
  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    const angle = (i / count) * Math.PI * 2 + random() * 0.6;
    const spread = Math.min(width, height) * (0.2 + random() * 0.2);
    xs[i] = width / 2 + Math.cos(angle) * spread;
    ys[i] = height / 2 + Math.sin(angle) * spread;
  }

  const ideal = Math.sqrt((width * height) / count) * 0.72;
  const padding = 62;
  let temperature = Math.min(width, height) / 6;
  const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

  for (let step = 0; step < 240; step += 1) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        let vx = xs[i] - xs[j];
        let vy = ys[i] - ys[j];
        let distance = Math.hypot(vx, vy);
        if (distance < 0.01) {
          vx = random() - 0.5;
          vy = random() - 0.5;
          distance = 0.01;
        }
        const force = (ideal * ideal) / distance;
        const ux = (vx / distance) * force;
        const uy = (vy / distance) * force;
        dx[i] += ux;
        dy[i] += uy;
        dx[j] -= ux;
        dy[j] -= uy;
      }
    }
    for (const [a, b] of links) {
      const vx = xs[a] - xs[b];
      const vy = ys[a] - ys[b];
      const distance = Math.max(0.01, Math.hypot(vx, vy));
      const force = (distance * distance) / ideal;
      const ux = (vx / distance) * force;
      const uy = (vy / distance) * force;
      dx[a] -= ux;
      dy[a] -= uy;
      dx[b] += ux;
      dy[b] += uy;
    }
    for (let i = 0; i < count; i += 1) {
      dx[i] += (width / 2 - xs[i]) * 0.015;
      dy[i] += (height / 2 - ys[i]) * 0.015;
    }
    temperature *= 0.985;
    for (let i = 0; i < count; i += 1) {
      const distance = Math.hypot(dx[i], dy[i]) || 1;
      const limited = Math.min(distance, temperature);
      // Keep the layout inside the frame; otherwise the graph drifts far past the
      // viewBox and the final fit shrinks every node into a blob.
      xs[i] = clamp(xs[i] + (dx[i] / distance) * limited, padding, width - padding);
      ys[i] = clamp(ys[i] + (dy[i] / distance) * limited, padding, height - padding);
    }
  }

  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  // Already inside the frame, so this only centres the result and nudges a
  // sparse graph up slightly towards the available area.
  const scale = Math.min(1.2, (width - padding * 2) / spanX, (height - padding * 2) / spanY);
  const offsetX = (width - spanX * scale) / 2 - minX * scale;
  const offsetY = (height - spanY * scale) / 2 - minY * scale;

  return nodes.map((node, i) => ({
    ...node,
    x: xs[i] * scale + offsetX,
    y: ys[i] * scale + offsetY,
    radius: Math.min(15, 4.4 + Math.sqrt(degrees[i]) * 2.6),
    degree: degrees[i],
  }));
}

/** Rough advance width, so label collision can be estimated without measuring. */
function labelWidth(label: string): number {
  let width = 6;
  for (const char of label) width += /[\u3400-\u9fff\uff00-\uffef]/.test(char) ? 11 : 6.2;
  return width;
}

function graphTone(pageType: string): "maintained" | "source" | "structure" {  if (pageType === "source") return "source";
  if (pageType === "topic" || pageType === "query" || pageType === "entity") return "maintained";
  return "structure";
}

function GraphSheet({ graph, onOpenPage }: { graph: WikiGraph | null; onOpenPage: (path: string) => void }) {
  const [hovered, setHovered] = useState<string | null>(null);
  const width = 960;
  const height = 620;
  const placed = useMemo(() => (graph ? layoutGraph(graph, width, height) : []), [graph]);

  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const node of graph?.nodes ?? []) map.set(node.id, new Set());
    for (const edge of graph?.edges ?? []) {
      map.get(edge.source)?.add(edge.target);
      map.get(edge.target)?.add(edge.source);
    }
    return map;
  }, [graph]);

  /**
   * Only hubs get a printed label, and only when the text clears every label and
   * node already placed — otherwise a wiki graph becomes a word cloud.
   */
  const labelled = useMemo(() => {
    const boxes = placed.map((node) => {
      const radius = node.radius + 3;
      return { left: node.x - radius, right: node.x + radius, top: node.y - radius, bottom: node.y + radius };
    });
    const keep = new Set<string>();
    for (const node of [...placed].sort((a, b) => b.degree - a.degree)) {
      if (keep.size >= 10 && node.degree > 0) break;
      const left = node.x + node.radius + 5;
      const box = { left, right: left + labelWidth(node.label), top: node.y - 8, bottom: node.y + 8 };
      const collides = boxes.some((other) => box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top);
      if (collides) continue;
      boxes.push(box);
      keep.add(node.id);
    }
    return keep;
  }, [placed]);

  if (!graph || !placed.length) {
    return <div className="sheet-stage"><div className="sheet-note">这个知识库还没有可派生的页面链接。</div></div>;
  }

  const neighbours = hovered ? adjacency.get(hovered) ?? new Set<string>() : null;
  const isActive = (id: string) => !hovered || id === hovered || Boolean(neighbours?.has(id));
  const tones: Array<{ id: string; label: string; count: number }> = [
    { id: "maintained", label: "自持知识", count: graph.nodes.filter((node) => graphTone(node.page_type) === "maintained").length },
    { id: "source", label: "外部来源", count: graph.nodes.filter((node) => graphTone(node.page_type) === "source").length },
    { id: "structure", label: "结构页", count: graph.nodes.filter((node) => graphTone(node.page_type) === "structure").length },
  ];

  const orphanCount = placed.filter((node) => node.degree === 0).length;

  return (
    <div className="sheet-stage graph-stage">
      <svg
        className="graph-canvas"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="页面链接图谱"
        onMouseLeave={() => setHovered(null)}
      >
        <g className="graph-edges">
          {graph.edges.map((edge) => {
            const focused = hovered !== null && (edge.source === hovered || edge.target === hovered);
            return (
              <line
                key={`${edge.source}->${edge.target}`}
                x1={placed.find((node) => node.id === edge.source)?.x}
                y1={placed.find((node) => node.id === edge.source)?.y}
                x2={placed.find((node) => node.id === edge.target)?.x}
                y2={placed.find((node) => node.id === edge.target)?.y}
                className={`graph-edge ${focused ? "focused" : ""} ${hovered && !focused ? "muted" : ""}`}
              />
            );
          })}
        </g>
        <g className="graph-nodes">
          {placed.map((node) => {
            const active = isActive(node.id);
            const labelled_ = labelled.has(node.id) || hovered === node.id || Boolean(neighbours?.has(node.id));
            return (
              <g
                key={node.id}
                className={`graph-node tone-${graphTone(node.page_type)} ${node.degree === 0 ? "is-orphan" : ""} ${active ? "" : "muted"}`}
                onMouseEnter={() => setHovered(node.id)}
                onClick={() => onOpenPage(node.path)}
              >
                <title>{node.degree === 0 ? `${node.label} · 没有页面与它互链` : `${node.label} · ${node.degree} 条链接`}</title>
                <circle cx={node.x} cy={node.y} r={node.radius} />
                {labelled_ ? (
                  <text x={node.x + node.radius + 5} y={node.y + 3.5} className="graph-label">{node.label}</text>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
      <div className="graph-legend">
        {tones.map((tone) => (
          <span key={tone.id} className={`graph-tone tone-${tone.id}`}>
            <i />{tone.label}<em>{tone.count}</em>
          </span>
        ))}
        {orphanCount ? (
          <span className="graph-tone tone-orphan"><i />未互链<em>{orphanCount}</em></span>
        ) : null}
        <p>悬停看相邻页面，点击打开</p>
      </div>
    </div>
  );
}

function LintSheet({ report, creatingPath, onCreatePage, onOpenPage, onRecheck }: {
  report: WikiLint | null;
  creatingPath: string | null;
  onCreatePage: (target: { label: string; path: string }) => Promise<void>;
  onOpenPage: (path: string) => void;
  onRecheck: () => void;
}) {
  if (!report) {
    return <div className="sheet-stage"><div className="sheet-note">正在检查链接、孤立页与摘要…</div></div>;
  }

  const groups = [
    { id: "error", label: "需要处理", items: report.issues.filter((issue) => issue.severity === "error") },
    { id: "warning", label: "建议修复", items: report.issues.filter((issue) => issue.severity !== "error") },
  ].filter((group) => group.items.length > 0);

  if (!groups.length) {
    return (
      <div className="sheet-stage">
        <div className="lint-summary healthy">
          <ShieldCheck size={20} />
          <div>
            <h3>知识库状态良好</h3>
            <p>已检查 {report.checked_pages} 个页面：链接、摘要与页面连接均未发现问题。</p>
          </div>
          <button className="secondary-button compact" onClick={onRecheck}>重新检查</button>
        </div>
      </div>
    );
  }

  return (
    <div className="sheet-stage lint-sheet">
      <div className="lint-summary">
        <ShieldCheck size={20} />
        <div>
          <h3>{report.issues.length} 项待处理</h3>
          <p>已检查 {report.checked_pages} 个页面。断链可以直接补成一页，补完链接关系就会接上。</p>
        </div>
        <button className="secondary-button compact" onClick={onRecheck} disabled={creatingPath !== null}>重新检查</button>
      </div>
      {groups.map((group) => (
        <section className="lint-group" key={group.id}>
          <div className={`rail-label severity-${group.id}`}><span>{group.label}</span><em>{group.items.length}</em></div>
          <ul>
            {group.items.map((issue) => {
              const target = issue.code === "broken_link" ? missingLinkTarget(issue.message) : null;
              return (
                <li key={`${issue.path}-${issue.code}-${issue.message}`}>
                  <span className={`issue-dot ${issue.severity}`} />
                  <div className="issue-body">
                    <div className="issue-head">
                      <em>{issueLabels[issue.code] ?? issue.code}</em>
                      <button type="button" className="issue-path" onClick={() => onOpenPage(issue.path)}>{issue.path}</button>
                    </div>
                    <p>{issue.message}</p>
                  </div>
                  {target ? (
                    <button
                      type="button"
                      className="secondary-button compact"
                      disabled={creatingPath !== null}
                      onClick={() => void onCreatePage(target)}
                    >
                      {creatingPath === target.path ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} 新建这一页
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** Pulls the unresolved target out of a `找不到链接目标：[[…]]` lint message. */
function missingLinkTarget(message: string): { label: string; path: string } | null {
  const match = message.match(/\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]/);
  if (!match) return null;
  const target = match[1].trim();
  return target ? { label: target.split("/").pop() ?? target, path: normalizePagePath(target) } : null;
}

/** Links written in a page that no other page answers to. */
function unresolvedLinks(content: string, nodes: GraphNode[]): Array<{ label: string; path: string }> {
  const known = new Set(nodes.map((node) => node.path));
  const missing = new Map<string, string>();
  for (const match of content.matchAll(/\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]/g)) {
    const target = match[1].trim();
    if (!target) continue;
    const path = normalizePagePath(target);
    if (known.has(path) || missing.has(path)) continue;
    missing.set(path, (match[2] ?? target).trim());
  }
  return [...missing].map(([path, label]) => ({ path, label }));
}

function PageLinks({ page, graph, creatingPath, onCreatePage, onOpenPage }: {
  page: WikiPage;
  graph: WikiGraph | null;
  creatingPath: string | null;
  onCreatePage: (target: { label: string; path: string }) => Promise<void>;
  onOpenPage: (path: string) => void;
}) {
  if (!graph) return null;
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const outgoing = graph.edges.filter((edge) => edge.source === page.path).map((edge) => byId.get(edge.target)).filter(Boolean) as GraphNode[];
  const backlinks = graph.edges.filter((edge) => edge.target === page.path).map((edge) => byId.get(edge.source)).filter(Boolean) as GraphNode[];
  const missing = unresolvedLinks(page.content, graph.nodes);
  if (!outgoing.length && !backlinks.length && !missing.length) return null;

  const row = (node: GraphNode) => (
    <li key={node.id}>
      <button type="button" className="link-row" onClick={() => onOpenPage(node.path)}>
        <span className={`link-dot tone-${graphTone(node.page_type)}`} />
        <strong>{node.label}</strong>
        <small>{node.path}</small>
      </button>
    </li>
  );

  return (
    <section className="page-links-panel" aria-label="页面链接">
      <div className="link-columns">
        <div>
          <div className="rail-label"><span>出链</span><em>{outgoing.length}</em></div>
          {outgoing.length ? <ul>{outgoing.map(row)}</ul> : <p className="link-empty">这一页还没有链接到其他页面。</p>}
        </div>
        <div>
          <div className="rail-label"><span>反向链接</span><em>{backlinks.length}</em></div>
          {backlinks.length ? <ul>{backlinks.map(row)}</ul> : <p className="link-empty">还没有页面链接到这一页。</p>}
        </div>
      </div>
      {missing.length ? (
        <div>
          <div className="rail-label severity-error"><span>断链</span><em>{missing.length}</em></div>
          <ul>
            {missing.map((target) => (
              <li key={target.path}>
                <span className="link-row static">
                  <span className="link-dot tone-missing" />
                  <strong>{target.label}</strong>
                  <small>{target.path}</small>
                </span>
                <button
                  type="button"
                  className="secondary-button compact"
                  disabled={creatingPath !== null}
                  onClick={() => void onCreatePage(target)}
                >
                  {creatingPath === target.path ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} 新建这一页
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function pageLabelOf(page: WikiPage): string {
  return page.path === "purpose.md" ? "宗旨" : pageLabels[page.page_type] ?? "页面";
}

function CreateDialog({ onClose, onSubmit }: { onClose: () => void; onSubmit: (name: string, description: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="create-title"><div className="dialog-header"><div><p className="eyebrow">New knowledge base</p><h2 id="create-title">创建知识库</h2></div><button className="icon-button" title="关闭" onClick={onClose}><X size={17} /></button></div><p>每个知识库都有独立的来源、页面、索引和活动记录。</p><label>名称<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：AI 研究笔记" /></label><label>描述<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} placeholder="这个知识库准备持续追踪什么？" /></label><div className="dialog-actions"><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={!name.trim()} onClick={() => void onSubmit(name, description)}>创建知识库 <ChevronRight size={15} /></button></div></section></div>;
}

function ModelSettingsDialog({ form, onChange, onClose, onNotice, onServerConfig, serverConfig }: {
  form: LLMFormState;
  onChange: (next: LLMFormState) => void;
  onClose: () => void;
  onNotice: (notice: Notice) => void;
  onServerConfig: (config: LLMConfigStatus) => void;
  serverConfig: LLMConfigStatus | null;
}) {
  const [draft, setDraft] = useState(form);
  const [testing, setTesting] = useState(false);
  const serverKeyAvailable = Boolean(serverConfig?.api_key_configured && !draft.api_key.trim());

  function update<K extends keyof LLMFormState>(key: K, value: LLMFormState[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    try {
      const config = await updateLLMConfig(draft);
      onChange(draft);
      onServerConfig(config);
      onNotice({ type: "success", message: draft.enabled ? "模型配置已应用于后续查询" : "已切换为本地检索模式" });
      onClose();
    } catch (error) {
      onNotice({ type: "error", message: error instanceof Error ? error.message : "模型配置保存失败" });
    }
  }

  async function test() {
    setTesting(true);
    try {
      const result = await testLLMConfig(draft);
      onNotice({ type: "success", message: result.message });
    } catch (error) {
      onNotice({ type: "error", message: error instanceof Error ? error.message : "模型连接测试失败" });
    } finally {
      setTesting(false);
    }
  }

  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-title">
      <div className="dialog-header">
        <div><p className="eyebrow">OpenAI-compatible</p><h2 id="model-title">模型设置</h2></div>
        <button className="icon-button" title="关闭" onClick={onClose}><X size={17} /></button>
      </div>
      <p>综合回答仅使用本次查询命中的页面或原始资料。未启用或调用失败时，系统自动保留本地检索结果。</p>
      <label className="model-toggle-row"><span><strong>启用模型综合</strong><small>对页面优先和原始资料 RAG 生效</small></span><input type="checkbox" checked={draft.enabled} onChange={(event) => update("enabled", event.target.checked)} /></label>
      <div className="model-form-grid">
        <label className="model-wide">服务地址<input value={draft.base_url} onChange={(event) => update("base_url", event.target.value)} placeholder="https://api.openai.com/v1" /></label>
        <label>模型名称<input value={draft.model} onChange={(event) => update("model", event.target.value)} placeholder="gpt-4o-mini" /></label>
        <label>API Key<input type="password" value={draft.api_key} onChange={(event) => update("api_key", event.target.value)} placeholder={serverKeyAvailable ? "服务器环境变量已配置" : "sk-..."} autoComplete="new-password" /></label>
        <label>温度<input type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={(event) => update("temperature", Number(event.target.value))} /></label>
        <label>最大输出 tokens<input type="number" min="64" max="16384" step="64" value={draft.max_tokens} onChange={(event) => update("max_tokens", Number(event.target.value))} /></label>
        <label className="model-wide">请求超时（秒）<input type="number" min="5" max="300" step="5" value={draft.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></label>
      </div>
      <div className="model-security-note"><ShieldCheck size={15} /><span>网页端 API Key 不会写入浏览器存储、Markdown 页面或 AgentKB 数据库；重启或刷新页面后需重新输入。服务器可通过 `.env` 长期配置。</span></div>
      <div className="dialog-actions">
        <button className="secondary-button" onClick={() => void test()} disabled={testing || !draft.enabled || !draft.base_url.trim() || !draft.model.trim() || (!draft.api_key.trim() && !serverKeyAvailable)}>{testing ? <Loader2 size={15} className="spin" /> : <HeartPulse size={15} />} 测试连接</button>
        <button className="primary-button" onClick={() => void save()}><Check size={15} /> 应用配置</button>
      </div>
    </section>
  </div>;
}

function normalizePagePath(path: string): string {
  const clean = path.replace(/^\.?\//, "").replace(/\.md$/, "");
  if (clean === "index" || clean === "purpose") return `${clean}.md`;
  return clean.startsWith("wiki/") ? `${clean}.md` : `wiki/${clean}.md`;
}
