// Generated from src/faro/contracts/chat.schema.json by pnpm contracts.

/**
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "product_id".
 */
export type ProductId = string;
/**
 * The conversation's language (POL-50).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "language".
 */
export type Language = "es" | "pt";
/**
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "product_type".
 */
export type ProductType = "Tarjeta Crédito" | "Tarjeta Débito";
/**
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "block_reason".
 */
export type BlockReason = "lost" | "stolen" | "unrecognized_charge" | "customer_request";
/**
 * The handoff reasons the policy offers rather than requires (POL-45).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "offered_reason_code".
 */
export type OfferedReasonCode =
  "unsupported_request" | "clarification_failed" | "record_conflict" | "missing_data" | "tool_failure";
/**
 * An identifier the chat draws, such as a thread, run, or message ID. Seven characters at least: assistant-ui draws its run and message IDs as seven characters from a 62-symbol alphabet, which keeps a thread's message IDs apart.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "id".
 */
export type Id = string;
/**
 * What a control sends. The graph accepts it only when the ID is the thread's pending one and the request comes from the sign-in that saw it, and a confirm only before its time limit (POL-09, POL-36, POL-45).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "control_answer".
 */
export type ControlAnswer =
  | {
      kind: "confirm" | "cancel";
      confirmation_id: string;
    }
  | {
      kind: "accept" | "decline";
      offer_id: string;
    };
/**
 * What the graph's confirm and offer nodes receive: a control's answer, or a message typed while a control was pending, which the entrypoint turns into a resume, masked, since the wrapper would otherwise drop it (POL-06). Typed text can end a confirmation or an offer and never grants either.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "resume".
 */
export type Resume =
  | ControlAnswer
  | {
      kind: "message";
      message_id: Id;
      text: string;
    };
/**
 * One control, or both, never two of a kind.
 *
 * @minItems 1
 * @maxItems 2
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "controls".
 */
export type Controls = Controls1;
export type Controls1 =
  [BlockConfirmation | HandoffOffer] | [BlockConfirmation | HandoffOffer, BlockConfirmation | HandoffOffer];
/**
 * Milliseconds since the Unix epoch, wall clock.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "timestamp".
 */
export type Timestamp = number;
/**
 * Every event the chat can receive. Anything else the wrapper emits (state snapshots and deltas, steps, tool calls, reasoning, custom and raw events) is dropped, and an event type added by a later version of the wrapper is dropped too. Replies arrive whole, once checked: one TEXT_MESSAGE_CONTENT per reply, never token by token (decision 8).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "event".
 */
export type Event =
  RunStarted | RunFinished | RunError | TextMessageStart | TextMessageContent | TextMessageEnd | MessagesSnapshot;

/**
 * What passes between the chat and the Runtime over AG-UI (ADR-0004: A turn, end to end; The confirmation; What the chat receives). The chat's request is read, not trusted: the entrypoint builds the run the wrapper sees from the fields below and drops the rest, since ag-ui-langgraph 0.0.45 would otherwise write a client's state, forwarded props, and earlier messages into the graph. Every event the chat receives is rebuilt by the entrypoint to one of the shapes under event, so no field the wrapper adds reaches the browser. AG-UI's own fields are camelCase; this contract's payloads are snake_case.
 */
export interface CardSupportChat {
  [k: string]: unknown;
}
/**
 * The chat's RunAgentInput. A run carries a new message, a resume, or neither (the warm-up), never a message and a resume together. The new message is the last one in messages when it is a user_message whose ID the thread's checkpoint doesn't hold; the earlier messages are ignored, since the checkpoint is the conversation's source of truth. The thread ID is replaced by a key derived from the token's sub before the wrapper sees it (decision 12). A request that carries none of them, such as a resend of a message the checkpoint already holds, is refused as invalid_request, and so is a resume that doesn't answer the thread's pending control: one sent while none is pending, or naming another interrupt or another confirmation or offer. A refused resume never reaches the graph, so the pending control stays as it was.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "request".
 */
export interface Request {
  threadId: Id;
  runId: Id;
  /**
   * @maxItems 500
   */
  messages: {}[];
  /**
   * An answer to the thread's pending interrupt, from the confirm or the handoff control.
   *
   * @maxItems 1
   */
  resume?: [] | [AgUiResume];
  forwardedProps?: {
    /**
     * Opens the runtime session at sign-in: the entrypoint binds it to the token's sub and ends the run without running the graph (decision 20).
     */
    warmup?: true;
  };
  /**
   * Never read; a client that sends state gets invalid_request.
   */
  state?: null | {};
  /**
   * @maxItems 0
   */
  tools?: [];
  /**
   * @maxItems 0
   */
  context?: [];
}
/**
 * AG-UI's ResumeEntry, always resolved: a cancel is an answer from the control, not AG-UI's cancelled status.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "ag_ui_resume".
 */
export interface AgUiResume {
  interruptId: string;
  status: "resolved";
  payload: ControlAnswer;
}
/**
 * A customer's message. The entrypoint masks any run of 13 or more digits to its last four before anything stores it (POL-11), and the masked text is what the snapshot sends back.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "user_message".
 */
export interface UserMessage {
  id: Id;
  role: "user";
  content: string;
}
/**
 * The confirm control, created with its confirmation record (POL-36). The chat names the card and the reason, and says that only a person can undo a block, in fixed text in the interrupt's language.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "block_confirmation".
 */
export interface BlockConfirmation {
  kind: "block_confirmation";
  confirmation_id: string;
  card: {
    type: ProductType;
    last_four: string;
  };
  reason: BlockReason;
  /**
   * Wall clock, five minutes after creation (decision 10). The chat disables the control then; the server decides.
   */
  expires_at: string;
}
/**
 * The handoff control, shown after the reply that offers the handoff (POL-45).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "handoff_offer".
 */
export interface HandoffOffer {
  kind: "handoff_offer";
  offer_id: string;
  reason_code: OfferedReasonCode;
}
/**
 * What confirm and offer pass to LangGraph's interrupt(). One interrupt carries both controls when POL-36 offers a handoff while its confirmation is pending; accepting the handoff then ends the confirmation unused.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "interrupt_value".
 */
export interface InterruptValue {
  reason: "controls";
  language: Language;
  controls: Controls;
}
/**
 * Without the input echo AG-UI allows.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "run_started".
 */
export interface RunStarted {
  type: "RUN_STARTED";
  timestamp?: Timestamp;
  threadId: Id;
  runId: Id;
}
/**
 * A run that ends waiting on a control carries one interrupt, whose ID the control's resume entry names. Without the run's token usage.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "run_finished".
 */
export interface RunFinished {
  type: "RUN_FINISHED";
  timestamp?: Timestamp;
  threadId: Id;
  runId: Id;
  outcome?:
    | {
        type: "success";
      }
    | {
        type: "interrupt";
        /**
         * @minItems 1
         * @maxItems 1
         */
        interrupts: [Interrupt];
      };
}
/**
 * AG-UI's Interrupt, with the controls under metadata and nothing of LangGraph's (its namespace names the graph's nodes).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "interrupt".
 */
export interface Interrupt {
  id: string;
  reason: "controls";
  metadata: {
    language: Language;
    controls: Controls;
  };
}
/**
 * A run the entrypoint refused or that failed. The chat shows fixed text per code in its own language; message is for logs, a fixed sentence per code with no detail. A request the entrypoint refuses gets this event alone, with no RUN_STARTED, since no run started. A missing, expired, or foreign token is turned away by the Runtime's authorizer before this contract applies, with HTTP 401, and the chat asks the customer to sign in again (POL-09).
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "run_error".
 */
export interface RunError {
  type: "RUN_ERROR";
  timestamp?: Timestamp;
  message: string;
  /**
   * invalid_request: the request failed this contract. session_refused: the runtime session belongs to another user. no_customer: the token carries no customer_id, or isn't a customer's (POL-07). rate_limited, daily_limit: decision 21's limits. internal: anything else, recorded.
   */
  code: "invalid_request" | "session_refused" | "no_customer" | "rate_limited" | "daily_limit" | "internal";
}
/**
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "text_message_start".
 */
export interface TextMessageStart {
  type: "TEXT_MESSAGE_START";
  timestamp?: Timestamp;
  messageId: Id;
  role: "assistant";
}
/**
 * The whole checked reply, with its facts and outcomes filled in by code.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "text_message_content".
 */
export interface TextMessageContent {
  type: "TEXT_MESSAGE_CONTENT";
  timestamp?: Timestamp;
  messageId: Id;
  delta: string;
}
/**
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "text_message_end".
 */
export interface TextMessageEnd {
  type: "TEXT_MESSAGE_END";
  timestamp?: Timestamp;
  messageId: Id;
}
/**
 * The graph's public state (ADR-0004, What the chat receives): the customer's masked messages and the agent's checked replies, and nothing else. A reply keeps the ID its TEXT_MESSAGE events carried.
 *
 * This interface was referenced by `CardSupportChat`'s JSON-Schema
 * via the `definition` "messages_snapshot".
 */
export interface MessagesSnapshot {
  type: "MESSAGES_SNAPSHOT";
  timestamp?: Timestamp;
  /**
   * @maxItems 500
   */
  messages: {
    id: Id;
    role: "user" | "assistant";
    content: string;
  }[];
}
