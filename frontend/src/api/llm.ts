import { requestJson } from "./client";
import type { LLMConfigurationInput, LLMConfigStatus, LLMTestResponse } from "./types";

export function getLLMConfig(): Promise<LLMConfigStatus> {
  return requestJson<LLMConfigStatus>("/api/llm/config");
}

export function updateLLMConfig(payload: LLMConfigurationInput): Promise<LLMConfigStatus> {
  return requestJson<LLMConfigStatus>("/api/llm/config", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function testLLMConfig(payload: LLMConfigurationInput): Promise<LLMTestResponse> {
  return requestJson<LLMTestResponse>("/api/llm/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
