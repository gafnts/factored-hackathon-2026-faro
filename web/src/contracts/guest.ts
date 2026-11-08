// Generated from src/faro/contracts/guest.schema.json by pnpm contracts.

/**
 * What the guest entry answers the customers' sign-in (ADR-0007, The guest entry, and its amendment of 2026-11-08): a new visitor's tokens with the welcome, or a refusal. A full day and the pause are answers, so they come as a 200, as the chat's refusals come in a 200 stream; a throttle or a fault comes as an error status with API Gateway's or Lambda's own body, never this contract.
 */
export type GuestEntry = Entered | Refused;

/**
 * A new user in the customer and visitor groups, signed in through the customers' app client: the tokens Cognito issued, which the tab puts where the form's sign-in puts them, and the welcome.
 */
export interface Entered {
  access_token: string;
  id_token: string;
  refresh_token: string;
  welcome: Welcome;
}
/**
 * Who the visitor entered as: the customer's synthetic name, from the curated list, and each card as list_cards returned it with the visitor's own token, in its order. No status, since a block changes it during the visit. cards is null when that read failed, and the welcome names the customer alone.
 */
export interface Welcome {
  name: string;
  cards: WelcomeCard[] | null;
}
export interface WelcomeCard {
  product_type: "Tarjeta Crédito" | "Tarjeta Débito";
  /**
   * The last four digits of the card's number, the only part the tools' data holds (POL-11).
   */
  last_four: string;
}
/**
 * No user was made: the day's guests reached their cap (full), or the chat is paused, which pauses the guest entry too (paused). The page says when a full day ends, in the visitor's own time; the pause names no time.
 */
export interface Refused {
  refused: "full" | "paused";
}
