const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function authorizationHeaders(forceRefresh = false): Promise<Record<string, string>> {
  if (typeof window === "undefined") return {};
  const { auth } = await import("./firebase");
  if (!auth) return {};
  const token = await auth.currentUser?.getIdToken(forceRefresh);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type ChatCitation = {
  title: string;
  document_number: string;
  section_title: string;
  quote: string;
  source_url: string;
  source_checked_at: string;
};

export type ChatResponse = {
  response: string;
  citations: ChatCitation[];
  request_id?: string;
  conversation_id?: string;
  turn_id?: string;
};

export type ReviewQueueItem = {
  review_id: string;
  domain: "legal_document" | "hospital_fee_ocr";
  source_id: string;
  title: string;
  status: "pending" | "accepted" | "rejected";
  confidence: number;
  summary: string;
  payload: Record<string, unknown>;
  submitted_by: string;
  assigned_to: string;
  decision_note: string;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  audit: Array<Record<string, unknown>>;
};

export type ChatStreamEvent =
  | { type: "status"; stage: string }
  | { type: "final"; response: string; citations: ChatCitation[]; request_id?: string; conversation_id?: string; turn_id?: string }
  | { type: "done"; ok: boolean }
  | { type: "error"; code: string; message: string };

export type ChatTurnContext = {
  conversationId?: string;
  turnId?: string;
};

function idempotencyKey(context: ChatTurnContext): string {
  if (context.turnId && context.turnId.length >= 8) return `turn-${context.turnId}`;
  return globalThis.crypto?.randomUUID?.() ?? `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

type ApiError = {
  code?: string;
  message?: string;
};

async function adminRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const authHeaders = await authorizationHeaders();
  let response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders, ...(init.headers ?? {}) },
  });
  if (response.status === 401) {
    const refreshedHeaders = await authorizationHeaders(true);
    response = await fetch(`${apiUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...refreshedHeaders, ...(init.headers ?? {}) },
    });
  }
  return response;
}

export async function fetchDocumentHtml(documentNumber: string): Promise<string> {
  const path = `/api/v1/documents/${documentNumber.split("/").map(encodeURIComponent).join("/")}/html`;
  const response = await adminRequest(path, { headers: { Accept: "text/html" } });
  if (!response.ok) throw new Error("Không thể tải văn bản nguồn");
  return response.text();
}

export type LegalTimelineDocument = {
  document_number: string;
  title: string;
  issued_at: string;
  effective_from: string;
  effective_to: string;
  status: string;
  source_url: string;
  viewer_url: string;
  state_at_date: "not_yet_effective" | "effective" | "expired" | "unknown";
};

export type LegalTimelineEvent = {
  relation: string;
  source_document_number: string;
  target_document_number: string;
  adverse: boolean;
};

export type LegalTimelineResponse = {
  query_document: LegalTimelineDocument;
  as_of: string;
  documents: LegalTimelineDocument[];
  events: LegalTimelineEvent[];
  degraded: boolean;
};

export async function fetchLegalTimeline(
  documentNumber: string,
  asOf?: string,
): Promise<LegalTimelineResponse> {
  const query = new URLSearchParams({ document_number: documentNumber });
  if (asOf) query.set("as_of", asOf);
  const response = await adminRequest(`/api/v1/legal/timeline?${query.toString()}`);
  if (!response.ok) throw new Error("Không thể tải dòng thời gian pháp lý");
  return response.json() as Promise<LegalTimelineResponse>;
}

export type EligibilityTopic = "benefit" | "five_year" | "referral" | "emergency" | "student_contribution";

export type EligibilityChecklistField = {
  key: string;
  label: string;
  reason: string;
  input_type: "text" | "date" | "number" | "boolean" | "select";
  options: string[];
};

export type EligibilityChecklistResponse = {
  topic: EligibilityTopic;
  complete: boolean;
  missing: EligibilityChecklistField[];
  accepted_fact_keys: string[];
  next_question: string;
  legal_retrieval_required: boolean;
  conversation_id: string;
  facts_persisted: boolean;
};

export async function fetchEligibilityChecklist(
  topic: EligibilityTopic,
  facts: Record<string, string | boolean>,
  conversationId = "",
): Promise<EligibilityChecklistResponse> {
  const response = await adminRequest("/api/v1/eligibility/checklist", {
    method: "POST",
    body: JSON.stringify({ topic, facts, conversation_id: conversationId }),
  });
  if (!response.ok) throw new Error("Không thể tạo checklist điều kiện");
  return response.json() as Promise<EligibilityChecklistResponse>;
}

export type BenefitCalculationInput = {
  covered_cost: string;
  base_rate_percent: string;
  copayment_spend?: string;
  copayment_threshold?: string | null;
  continuous_years?: string | null;
  required_years?: string;
  threshold_rate_percent?: string;
  rule_provenance?: string[];
};

export async function compareBenefitScenarios(
  scenarios: Array<{ label: string; calculation: BenefitCalculationInput }>,
): Promise<{ results: Array<{ label: string; calculation: Record<string, unknown> }> }> {
  const response = await adminRequest("/api/v1/calculator/bhyt/scenarios", {
    method: "POST",
    body: JSON.stringify({ scenarios }),
  });
  if (!response.ok) throw new Error("Không thể tính các kịch bản BHYT");
  return response.json() as Promise<{ results: Array<{ label: string; calculation: Record<string, unknown> }> }>;
}

export async function fetchAdminReviews(status = "pending", domain = "all"): Promise<ReviewQueueItem[]> {
  const response = await adminRequest(`/api/v1/auth/admin/reviews?status=${encodeURIComponent(status)}&domain=${encodeURIComponent(domain)}`);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể tải hàng đợi kiểm duyệt");
  }
  return (await response.json()) as ReviewQueueItem[];
}

export async function decideAdminReview(reviewId: string, status: "accepted" | "rejected", note = ""): Promise<ReviewQueueItem> {
  const response = await adminRequest(`/api/v1/auth/admin/reviews/${encodeURIComponent(reviewId)}`, {
    method: "PATCH",
    body: JSON.stringify({ status, note }),
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể cập nhật bản kiểm duyệt");
  }
  return (await response.json()) as ReviewQueueItem;
}

export async function sendChatMessage(
  message: string,
  context: ChatTurnContext = {},
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const authHeaders = await authorizationHeaders();
  const requestIdempotencyKey = idempotencyKey(context);
  let response = await fetch(`${apiUrl}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": requestIdempotencyKey, ...authHeaders },
    body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
    signal,
  });
  if (response.status === 401) {
    const refreshedHeaders = await authorizationHeaders(true);
    response = await fetch(`${apiUrl}/api/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": requestIdempotencyKey, ...refreshedHeaders },
      body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
      signal,
    });
  }

  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể kết nối MediPay Agent");
  }

  const payload: unknown = await response.json();
  if (!isChatResponse(payload)) {
    throw new Error("API trả dữ liệu chat không đúng định dạng");
  }
  return payload;
}

/** Consume the safe SSE envelope; raw provider tokens are never exposed. */
export async function sendChatMessageStream(
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  context: ChatTurnContext = {},
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const authHeaders = await authorizationHeaders();
  const requestIdempotencyKey = idempotencyKey(context);
  let response = await fetch(`${apiUrl}/api/v1/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "Idempotency-Key": requestIdempotencyKey,
      ...authHeaders,
    },
    body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
    signal,
  });
  if (response.status === 401) {
    const refreshedHeaders = await authorizationHeaders(true);
    response = await fetch(`${apiUrl}/api/v1/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "Idempotency-Key": requestIdempotencyKey,
        ...refreshedHeaders,
      },
      body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
      signal,
    });
  }
  if (!response.ok || !response.body) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    if (response.status === 401) {
      throw new Error("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại rồi thử lại.");
    }
    throw new Error(error?.message ?? `MediPay Agent không khả dụng (HTTP ${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: ChatResponse | null = null;
  const streamStartedAt = typeof performance === "undefined" ? 0 : performance.now();
  let ttftRecorded = false;
  const consumeFrame = (frame: string) => {
    const eventLine = frame.split("\n").find((line) => line.startsWith("event:"));
    const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
    if (!dataLine) return;
    const eventType = eventLine?.slice(6).trim();
    if (eventType !== "status" && eventType !== "final" && eventType !== "done" && eventType !== "error") {
      return;
    }
    // The backend follows SSE's standard envelope: event name in `event:` and
    // event body in `data:`.  Reconstruct the discriminated client event here
    // instead of incorrectly expecting a duplicate `type` field in JSON.
    const payload = { type: eventType, ...JSON.parse(dataLine.slice(5).trimStart()) } as ChatStreamEvent;
    if (!ttftRecorded) {
      ttftRecorded = true;
      const durationMs = streamStartedAt
        ? Math.max(0, Math.round(performance.now() - streamStartedAt))
        : 0;
      // Local browser instrumentation only.  The event carries no prompt,
      // answer, user identity, or credential; an app shell may consume it for
      // a TTFT histogram without adding another network request to chat.
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("medipay:stream-ttft", { detail: { durationMs } }),
        );
      }
    }
    onEvent(payload);
    if (payload.type === "final") {
      final = {
        response: payload.response,
        citations: payload.citations,
        request_id: payload.request_id,
        conversation_id: payload.conversation_id,
        turn_id: payload.turn_id,
      };
    }
    if (payload.type === "error") {
      throw new Error(
        payload.code === "retrieval_timeout"
          ? "Kho dữ liệu đang phản hồi chậm. Vui lòng thử lại sau ít giây."
          : payload.message,
      );
    }
  };
  while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done });
    // SSE permits CRLF line endings. Normalize before looking for event
    // boundaries because proxies may rewrite the backend's LF-only framing.
    buffer = buffer.replace(/\r\n/g, "\n");
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      consumeFrame(frame);
    }
    if (chunk.done) break;
  }
  // Some reverse proxies end a response immediately after the final `data:`
  // line. Accept that valid terminal frame even if its blank SSE delimiter was
  // stripped during transport.
  if (buffer.trim()) consumeFrame(buffer);
  if (!final || !isChatResponse(final)) throw new Error("API trả dữ liệu stream không đúng định dạng");
  return final;
}

function isChatResponse(payload: unknown): payload is ChatResponse {
  if (!payload || typeof payload !== "object") return false;
  const candidate = payload as Record<string, unknown>;
  return (
    typeof candidate.response === "string" &&
    candidate.response.trim().length > 0 &&
    Array.isArray(candidate.citations)
  );
}
