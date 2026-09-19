import { requestJson } from "./client";
import type { LLMConfigurationInput, WikiGraph, WikiLint, WikiPage, WikiPagePage, WikiQueryResponse, WikiStatus } from "./types";
function pageUrl(knowledgeBaseId: number, path: string): string {
  return `/api/knowledge-bases/${knowledgeBaseId}/wiki/pages/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export function getWikiStatus(knowledgeBaseId: number): Promise<WikiStatus> {
  return requestJson<WikiStatus>(`/api/knowledge-bases/${knowledgeBaseId}/wiki/status`);
}

export function listWikiPages(knowledgeBaseId: number, page = 1, pageSize = 200): Promise<WikiPagePage> {
  return requestJson<WikiPagePage>(`/api/knowledge-bases/${knowledgeBaseId}/wiki/pages?page=${page}&page_size=${pageSize}`);
}

/**
 * Every page in the workspace. The API caps a single request at 200 rows, so the
 * catalog keeps paging instead of silently showing a truncated目录.
 */
export async function listAllWikiPages(knowledgeBaseId: number, pageSize = 200): Promise<WikiPage[]> {
  const first = await listWikiPages(knowledgeBaseId, 1, pageSize);
  const items = [...first.items];
  let page = 1;
  while (items.length < first.total && page < 50) {
    page += 1;
    const next = await listWikiPages(knowledgeBaseId, page, pageSize);
    if (!next.items.length) break;
    items.push(...next.items);
  }
  return items;
}

export function readWikiPage(knowledgeBaseId: number, path: string): Promise<WikiPage> {
  return requestJson<WikiPage>(pageUrl(knowledgeBaseId, path));
}

export function saveWikiPage(knowledgeBaseId: number, path: string, content: string): Promise<WikiPage> {
  return requestJson<WikiPage>(pageUrl(knowledgeBaseId, path), {
    method: "PUT",
    body: JSON.stringify({ path, content }),
  });
}

export interface RetrievalStrategy {
  id: string;
  label: string;
  description: string;
  hops: number | string;
  neighbour_limit: number;
}

/** The retrieval configurations a query may ask for. The set is fixed by the backend. */
export function listRetrievalStrategies(): Promise<{ items: RetrievalStrategy[] }> {
  return requestJson<{ items: RetrievalStrategy[] }>("/api/retrieval-strategies");
}

export function queryWiki(knowledgeBaseId: number, payload: { query: string; top_k?: number; save_as?: string; strategy?: string; llm?: LLMConfigurationInput }): Promise<WikiQueryResponse> {
  return requestJson<WikiQueryResponse>(`/api/knowledge-bases/${knowledgeBaseId}/wiki/query`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function lintWiki(knowledgeBaseId: number): Promise<WikiLint> {
  return requestJson<WikiLint>(`/api/knowledge-bases/${knowledgeBaseId}/wiki/lint`, { method: "POST" });
}

export function getWikiGraph(knowledgeBaseId: number): Promise<WikiGraph> {
  return requestJson<WikiGraph>(`/api/knowledge-bases/${knowledgeBaseId}/wiki/graph`);
}
