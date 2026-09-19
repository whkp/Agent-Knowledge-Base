export interface KnowledgeBase {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentChunk {
  id: number;
  document_id: number;
  knowledge_base_id: number;
  chunk_index: number;
  content: string;
  vector_id: string | null;
  created_at: string;
}

export interface KnowledgeDocument {
  id: number;
  knowledge_base_id: number;
  title: string;
  source_type: "text" | "file" | "source_snapshot";
  file_name: string | null;
  content: string;
  source_url: string | null;
  source_platform: string | null;
  source_author: string | null;
  source_account: string | null;
  source_published_at: string | null;
  source_captured_at: string | null;
  source_policy: "full_text" | "excerpt" | "link_only";
  source_disclosures: string[];
  content_hash: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  chunks: DocumentChunk[];
}

export interface SearchResult {
  document_id: number;
  title: string;
  chunk: string;
  score: number;
  chunk_id: number | null;
  chunk_index: number | null;
  source_url: string | null;
  source_platform: string | null;
  source_author: string | null;
  source_account: string | null;
  source_published_at: string | null;
  source_captured_at: string | null;
  source_policy: "full_text" | "excerpt" | "link_only" | null;
  content_hash: string | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  answer: string | null;
  answer_mode: "retrieval" | "llm";
  model: string | null;
  model_error: string | null;
}

export type StreamEvent =
  | { type: "start" }
  | { type: "delta"; content: string }
  | ({ type: "result" } & SearchResult)
  | { type: "error"; message: string }
  | { type: "done" };

export interface WikiPage {
  path: string;
  title: string;
  page_type: string;
  summary: string;
  content: string;
  updated_at: string;
  inbound_links: number;
  outbound_links: number;
}

export interface WikiPagePage {
  items: WikiPage[];
  total: number;
  page: number;
  page_size: number;
}

export interface WikiStatus {
  workspace_path: string;
  initialized: boolean;
  source_count: number;
  page_count: number;
  topic_count: number;
  orphan_count: number;
  broken_link_count: number;
  recent_activity: string[];
}

export interface WikiQueryResult {
  path: string;
  title: string;
  page_type: string;
  summary: string;
  snippet: string;
  score: number;
  citations: string[];
  /** Pulled in by following a wiki link from a direct match, not matched itself. */
  related?: boolean;
}

export interface WikiQueryResponse {
  query: string;
  answer: string;
  results: WikiQueryResult[];
  saved_path: string | null;
  answer_mode: "deterministic" | "llm";
  /** Which named retrieval strategy ran, and how many link hops it used. */
  strategy?: string;
  hops?: number;
  model: string | null;
  model_error: string | null;
}

export interface LLMConfigurationInput {
  enabled?: boolean;
  base_url?: string;
  api_key?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  timeout_seconds?: number;
}

export interface LLMConfigStatus {
  provider: "openai-compatible";
  enabled: boolean;
  ready: boolean;
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
  api_key_configured: boolean;
}

export interface LLMTestResponse {
  ready: boolean;
  model: string;
  message: string;
}

export interface WikiLintIssue {
  severity: string;
  code: string;
  path: string;
  message: string;
}

export interface WikiLint {
  healthy: boolean;
  checked_pages: number;
  issues: WikiLintIssue[];
}

export interface WikiGraph {
  nodes: Array<{
    id: string;
    label: string;
    path: string;
    page_type: string;
    inbound_links: number;
    outbound_links: number;
  }>;
  edges: Array<{ source: string; target: string }>;
}
