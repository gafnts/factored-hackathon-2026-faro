// Generated from src/faro/contracts/console.schema.json by pnpm contracts.

/**
 * What the console API answers a human agent (ADR-0007, The console API, and its amendments of 2026-09-30): a page of a queue's cases, one case with the tool calls its evidence names, or a refusal. It reads the demo's filed cases only and changes none. A case's fields and its payload are the case record's and the handoff schema's own, referred to by their IDs rather than copied, so the console's types follow them.
 */
export type CardSupportConsole = CaseList | CaseDetail | Refusal;
/**
 * Where the next page starts: the queue index's key of the page's last case, encoded. The API refuses one that names another queue or status.
 */
export type QueueCursor = string;
/**
 * @maxItems 60
 */
export type ValidationErrors = ValidationError[];
/**
 * What the card support agent hands a person (CTL-05), under docs/policy/card-support.md (POL-45 to POL-47). Facts come from tool calls; what the customer says stays apart from them. No transcript, and no full card number.
 */
export type CardSupportHandoff = {
  schema_version: 1;
  handoff_id: string;
  /**
   * Wall clock.
   */
  created_at: string;
  session_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "customer_id".
   */
  customer_id: string;
  versions: {
    policy: number;
    snapshot: string;
  };
  /**
   * The frozen bank's today, which banking logic reads (POL-19).
   */
  business_date: string;
  /**
   * The language to answer the customer in (POL-50).
   */
  language: "es" | "pt";
  queue: "customer_service" | "dispute_intake";
  priority: "urgent" | "normal";
  /**
   * Whether the policy required the handoff or the customer accepted an offer (POL-45).
   */
  trigger: "required" | "accepted_offer";
  reason_code:
    | "unrecognized_charge"
    | "customer_request"
    | "complaint"
    | "unblock_request"
    | "customer_not_active"
    | "ambiguous_card"
    | "action_not_verified"
    | "block_lapsed"
    | "unsupported_request"
    | "clarification_failed"
    | "record_conflict"
    | "missing_data"
    | "tool_failure";
  /**
   * The policy rules that required or offered the handoff.
   *
   * @minItems 1
   */
  rules: [string, ...string[]];
  request: {
    label:
      | "card_status"
      | "available_credit"
      | "recent_transactions"
      | "decline_reason"
      | "block_card"
      | "unrecognized_charge"
      | "unsupported"
      | "talk_to_human";
    /**
     * Free text, in Spanish (POL-46). A run of 13 or more digits, spaced or not, could be a card number, so it is rejected (POL-11).
     */
    summary: string;
  };
  /**
   * @maxItems 60
   */
  verified_facts: Fact[];
  /**
   * Actions taken, and blocks offered that the customer didn't confirm (POL-36).
   *
   * @maxItems 10
   */
  actions:
    | []
    | [Action]
    | [Action, Action]
    | [Action, Action, Action]
    | [Action, Action, Action, Action]
    | [Action, Action, Action, Action, Action]
    | [Action, Action, Action, Action, Action, Action]
    | [Action, Action, Action, Action, Action, Action, Action]
    | [Action, Action, Action, Action, Action, Action, Action, Action]
    | [Action, Action, Action, Action, Action, Action, Action, Action, Action]
    | [Action, Action, Action, Action, Action, Action, Action, Action, Action, Action];
  /**
   * @maxItems 60
   */
  evidence: ToolCall[];
  /**
   * What the customer says and no tool verified.
   *
   * @maxItems 5
   *
   * Items: Free text, in Spanish (POL-46). A run of 13 or more digits, spaced or not, could be a card number, so it is rejected (POL-11).
   */
  customer_statements:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string];
  /**
   * @maxItems 5
   *
   * Items: Free text, in Spanish (POL-46). A run of 13 or more digits, spaced or not, could be a card number, so it is rejected (POL-11).
   */
  unresolved_questions:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string];
};
/**
 * One field of one record, as a tool read it. A null value means the record doesn't hold it (POL-32).
 *
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "fact".
 */
export type Fact = {
  subject: "customer" | "card" | "transaction";
  id: string;
  field: string;
  value: string | number | boolean | null;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "call_id".
   */
  evidence: string;
};

/**
 * A page of one queue's cases of one status: urgent cases first, and each priority's newest first, the queue index's own order (ADR-0007's amendment of 2026-09-30). next_cursor is null on the last page.
 */
export interface CaseList {
  queue: "customer_service" | "dispute_intake";
  status: "filed" | "claimed" | "resolved";
  /**
   * @maxItems 50
   */
  cases: CaseRow[];
  next_cursor: QueueCursor | null;
}
/**
 * One case in a queue, from the queue index's projection alone: the list never reads a payload.
 */
export interface CaseRow {
  /**
   * A handoff's reference: eight characters of Crockford's base32, in two groups of four (ADR-0007).
   */
  reference: string;
  priority: "urgent" | "normal";
  reason_code:
    | "unrecognized_charge"
    | "customer_request"
    | "complaint"
    | "unblock_request"
    | "customer_not_active"
    | "ambiguous_card"
    | "action_not_verified"
    | "block_lapsed"
    | "unsupported_request"
    | "clarification_failed"
    | "record_conflict"
    | "missing_data"
    | "tool_failure";
  /**
   * The conversation's language (POL-50).
   */
  language: "es" | "pt";
  /**
   * Wall clock, UTC (ADR-0004, Two clocks).
   */
  filed_at: string;
  flagged: boolean;
}
/**
 * One case, found by its reference with strongly consistent reads, so a case filed a moment ago is found at once: its status, the payload as filed, which never changes, and the calls its evidence names. A flagged case names each part left out or replaced by its path and the rule it broke, never its value (ADR-0004, The handoff).
 */
export interface CaseDetail {
  case: {
    /**
     * A handoff's reference: eight characters of Crockford's base32, in two groups of four (ADR-0007).
     */
    reference: string;
    status: "filed" | "claimed" | "resolved";
    queue: "customer_service" | "dispute_intake";
    priority: "urgent" | "normal";
    reason_code:
      | "unrecognized_charge"
      | "customer_request"
      | "complaint"
      | "unblock_request"
      | "customer_not_active"
      | "ambiguous_card"
      | "action_not_verified"
      | "block_lapsed"
      | "unsupported_request"
      | "clarification_failed"
      | "record_conflict"
      | "missing_data"
      | "tool_failure";
    /**
     * The conversation's language (POL-50).
     */
    language: "es" | "pt";
    /**
     * Wall clock, UTC (ADR-0004, Two clocks).
     */
    saved_at?: string;
    /**
     * Wall clock, UTC (ADR-0004, Two clocks).
     */
    filed_at: string;
    flagged: boolean;
    validation_errors: ValidationErrors;
    payload: CardSupportHandoff;
  };
  /**
   * @maxItems 60
   */
  calls: RecordedCall[];
}
/**
 * A path and the rule it broke, never the value (POL-11).
 */
export interface ValidationError {
  path: string;
  rule: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "action".
 */
export interface Action {
  action: "block_card";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "product_id".
   */
  card_id: string;
  reason: "lost" | "stolen" | "unrecognized_charge" | "customer_request";
  /**
   * The confirmation the server created when it showed the confirm control, used or not (POL-36).
   */
  confirmation_id: string;
  /**
   * verified: the sandbox read back Blocked. not_verified: it didn't, so the card's state is unknown (POL-37). declined_by_customer: the customer cancelled with the confirm control. lapsed: the confirmation ended unused otherwise: on a new request, an accepted handoff, its time limit, or the session's end (POL-36).
   */
  outcome: "verified" | "not_verified" | "declined_by_customer" | "lapsed";
  confirmed_at: string | null;
  /**
   * Items: This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "call_id".
   */
  evidence: string[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "tool_call".
 */
export interface ToolCall {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "call_id".
   */
  call_id: string;
  tool: string;
  called_at: string;
  outcome: "ok" | "error" | "denied";
}
/**
 * A tool call the case's evidence names, as the execution record holds it (OPS-02): its last attempt, and the rows of its result that the case's facts and actions name, never the whole result or the call's input. recorded is false while the record doesn't hold the call yet, which only file_handoff's own call can be, in the moment after the filing.
 */
export interface RecordedCall {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "call_id".
   */
  call_id: string;
  recorded: boolean;
  tool?: "list_cards" | "get_card" | "get_available_credit" | "find_transactions" | "block_card" | "file_handoff";
  /**
   * gateway for the reads and the block; direct for file_handoff, which the Runtime invokes with IAM (ADR-0004, Where the tools run).
   */
  via?: "gateway" | "direct";
  /**
   * A call's attempts share its call_id; only a failed attempt is tried again, never a denied one (POL-48, POL-49).
   */
  attempt?: number;
  /**
   * Wall clock, UTC (ADR-0004, Two clocks).
   */
  called_at?: string;
  latency_ms?: number;
  /**
   * The Gateway's request ID, or the Lambda's for a direct call; null when the call never reached either.
   */
  request_id?: string | null;
  outcome?: "ok" | "not_found" | "refused" | "invalid_input" | "failed" | "denied";
  error_code?: "timeout" | "throttled" | "lambda_error" | "transport" | "denied";
  /**
   * @maxItems 60
   */
  rows?: RecordedRow[];
}
/**
 * One record a tool call's recorded result holds, with its scalar fields as the tool returned them: the customer, a card, a transaction, or a block's outcome.
 */
export interface RecordedRow {
  [k: string]: string | number | boolean | null;
}
/**
 * The console API's own refusals: a caller who isn't a human agent (forbidden), a reference no demo case holds (not_found, the same for a draft or an evaluation's case), or a query or path outside this contract (invalid_request). A missing, expired, or foreign token, or an ID token, never reaches our code: the authorizer turns it away with API Gateway's own body.
 */
export interface Refusal {
  error: "forbidden" | "not_found" | "invalid_request";
}
