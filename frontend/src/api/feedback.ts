import { requestJson } from "./client";

export interface QueryFeedbackInput {
  /** Which answer was rated: the page-first answer or the raw-source one. */
  mode: "wiki" | "rag";
  query: string;
  rating: 1 | -1;
  note?: string | null;
  answer?: string | null;
  answer_mode?: string | null;
  model?: string | null;
  strategy_id?: string | null;
  source_paths?: string[];
  /** Citations the person marked as misleading; only with rating -1. */
  bad_paths?: string[];
}

export interface QueryFeedback {
  id: number;
  knowledge_base_id: number;
  mode: string;
  query: string;
  rating: number;
  note: string | null;
  answer: string | null;
  answer_mode: string | null;
  model: string | null;
  strategy_id: string | null;
  source_paths: string[];
  /** Citations the person marked as misleading when rating -1. */
  bad_paths: string[];
  created_at: string;
}

/** Records whether one answer was useful. This is the signal retrieval evolves on. */
export function submitQueryFeedback(
  knowledgeBaseId: number,
  payload: QueryFeedbackInput,
): Promise<QueryFeedback> {
  return requestJson<QueryFeedback>(`/api/knowledge-bases/${knowledgeBaseId}/feedback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface QueryFeedbackPage {
  items: QueryFeedback[];
  total: number;
  page: number;
  page_size: number;
  positive: number;
  negative: number;
}

/** Read the recorded ratings back: the only signal retrieval strategies are judged on. */
export function listQueryFeedback(
  knowledgeBaseId: number,
  page = 1,
  pageSize = 50,
): Promise<QueryFeedbackPage> {
  return requestJson<QueryFeedbackPage>(
    `/api/knowledge-bases/${knowledgeBaseId}/feedback?page=${page}&page_size=${pageSize}`,
  );
}
