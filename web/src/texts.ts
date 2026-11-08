import type {
  BlockReason,
  Language,
  OfferedReasonCode,
  ProductType,
  RunError,
} from "./contracts/chat";
import { WORDS } from "./contracts/words";

export type RunErrorCode = RunError["code"];
export type Problem = RunErrorCode | "unreachable";
// Refusals that last until the UTC day ends, which the chat names in the browser's own time (decision 21).
type DayLong = "daily_limit" | "global_limit";
export type ProblemTexts = {
  [P in Problem]: P extends DayLong ? (time: string) => string : string;
};

export interface Texts {
  notice: string;
  // Signed in, the footer keeps to two sentences: this label (SEC-02) and the session's end.
  noticeBrief: string;
  notFound: {
    code: string;
    title: string;
    home: string;
  };
  broken: string;
  signIn: {
    title: string;
    username: string;
    password: string;
    submit: string;
    submitting: string;
    refused: string;
    unreachable: string;
    ended: string;
    signOut: string;
    endsAt: (time: string) => string;
    // The customers' sign-in's two ways in, as tabs on its card: the guest entry first, then the form (ADR-0007, The
    // guest entry).
    tabs: {
      label: string;
      guest: string;
      account: string;
    };
    // The guest entry's tab. Past the day's guests, the time the UTC day ends, in the browser's own.
    guest: {
      title: string;
      lead: string;
      enter: string;
      entering: string;
      full: (time: string) => string;
      paused: string;
    };
    // What we keep of a visit, in the customers' sign-in's banner and its dialog (ADR-0007, Sign-in; ADR-0004, data
    // retention). The banner takes the place of the notice, so it says the data is synthetic too (SEC-02); its last
    // words, more, open the dialog.
    terms: {
      banner: string;
      more: string;
      title: string;
      lines: readonly string[];
      close: string;
    };
  };
  rail: {
    label: string;
    open: string;
    close: string;
    newChat: string;
  };
  // The bar's language switch (ADR-0007, Routes): each option named in its own language.
  language: {
    label: string;
    es: string;
    pt: string;
  };
  // The rail's foot: who a guest entered as, and behind it their cards, by type and last four digits and never their
  // status, so they know what to ask about (ADR-0007, The guest entry), and the sign-out.
  account: {
    label: string;
    fictional: string;
    cards: string;
    type: Record<ProductType, string>;
    ending: string;
  };
  chat: {
    question: string;
    // Each one a path that works today: the cards, a block, a charge the customer doesn't recognize. Judges send
    // these words as messages, so the split's guards scan this file for a held-out family's words (ADR-0005).
    suggestions: readonly [string, string, string];
    placeholder: string;
    send: string;
    working: string;
    problems: ProblemTexts;
  };
  // The confirm control, in the language of the conversation it confirms (POL-36).
  control: {
    label: string;
    card: (type: ProductType, lastFour: string) => string;
    reason: (reason: BlockReason) => string;
    undo: string;
    confirm: string;
    cancel: string;
    expired: string;
  };
  // The handoff control, which alone accepts an offered handoff (POL-45).
  offer: {
    label: string;
    reason: (reason: OfferedReasonCode) => string;
    accept: string;
    decline: string;
  };
}

// Spanish says "la 1:00" and Portuguese "da 01:00", the one hour named in the singular.
const FROM: Record<Language, (time: string) => string> = {
  es: (time) => `a partir de ${time.startsWith("1:") ? "la" : "las"} ${time}`,
  pt: (time) => `a partir ${time.startsWith("01:") ? "da" : "das"} ${time}`,
};

const CARD_TYPES: Record<
  Language,
  Record<ProductType, string>
> = WORDS.product_type;

// What a person can do for each offered handoff; the reply before the control says why it's offered.
const OFFERS: Record<Language, Record<OfferedReasonCode, string>> = {
  es: {
    unsupported_request:
      "Una persona del banco puede ayudarle con esta solicitud.",
    clarification_failed:
      "Una persona del banco puede ayudarle a precisar su solicitud.",
    record_conflict:
      "Una persona del banco puede revisar los datos de su tarjeta.",
    missing_data: "Una persona del banco puede revisar el dato que falta.",
    tool_failure: "Una persona del banco puede atender su solicitud.",
  },
  pt: {
    unsupported_request: "Uma pessoa do banco pode ajudar com este pedido.",
    clarification_failed:
      "Uma pessoa do banco pode ajudar a esclarecer seu pedido.",
    record_conflict: "Uma pessoa do banco pode revisar os dados do seu cartão.",
    missing_data: "Uma pessoa do banco pode revisar o dado que falta.",
    tool_failure: "Uma pessoa do banco pode atender seu pedido.",
  },
};

// Each stands after "Motivo:" as a label's value, so it takes a capital.
const REASONS: Record<Language, Record<BlockReason, string>> = {
  es: {
    lost: "Pérdida",
    stolen: "Robo",
    unrecognized_charge: "Cargo no reconocido",
    customer_request: "Solicitud del cliente",
  },
  pt: {
    lost: "Perda",
    stolen: "Roubo",
    unrecognized_charge: "Cobrança não reconhecida",
    customer_request: "Pedido do cliente",
  },
};

export const TEXTS: Record<Language, Texts> = {
  es: {
    notice:
      "Prototipo sobre datos sintéticos. LATAM Bank y sus clientes son ficticios.",
    noticeBrief: "Prototipo sobre datos sintéticos.",
    notFound: {
      code: "Error 404",
      title: "Página no encontrada",
      home: "Volver al inicio",
    },
    broken: "No pudimos cargar la aplicación. Recargue la página.",
    signIn: {
      title: "Iniciar sesión",
      username: "Usuario",
      password: "Contraseña",
      submit: "Entrar",
      submitting: "Entrando…",
      refused: "No pudimos iniciar su sesión. Revise su usuario y contraseña.",
      unreachable:
        "No pudimos comunicarnos con el banco. Inténtelo de nuevo en unos minutos.",
      ended: "Su sesión terminó. Inicie sesión de nuevo para continuar.",
      signOut: "Cerrar sesión",
      endsAt: (time) => `Su sesión terminará a las ${time}.`,
      tabs: {
        label: "Formas de entrar",
        guest: "Invitado",
        account: "Con usuario",
      },
      guest: {
        title: "Acceso de invitado",
        lead: "Entre como un cliente ficticio y converse con Faro sobre sus tarjetas.",
        enter: "Entrar como invitado",
        entering: "Entrando…",
        full: (time) =>
          `Por hoy no hay más lugares para invitados. Vuelva a intentarlo ${FROM.es(time)}.`,
        paused: "Faro está en pausa por ahora. Vuelva a intentarlo más tarde.",
      },
      terms: {
        banner: "Prototipo sobre datos sintéticos. Al entrar, acepta",
        more: "estas condiciones",
        title: "Condiciones",
        lines: [
          "LATAM Bank y sus clientes son ficticios. No escriba datos reales.",
          "Las conversaciones se guardan hasta por 12 meses y pueden ser leídas para evaluar y mejorar al agente.",
          "Anthropic, el proveedor del modelo, procesa sus mensajes.",
          "Nadie da seguimiento a los casos que Faro registra.",
          "Su dirección IP se guarda hasta por 90 días.",
          "Al entrar, acepta estas condiciones.",
        ],
        close: "Cerrar",
      },
    },
    rail: {
      label: "Menú",
      open: "Abrir el menú",
      close: "Cerrar el menú",
      newChat: "Nueva conversación",
    },
    account: {
      label: "Su cuenta",
      fictional: "Cliente ficticio",
      cards: "Sus tarjetas",
      type: { "Tarjeta Crédito": "Crédito", "Tarjeta Débito": "Débito" },
      ending: "terminada en",
    },
    language: {
      label: "Idioma",
      es: "Español",
      pt: "Português",
    },
    chat: {
      question: "¿En qué le puedo ayudar?",
      suggestions: [
        "Muéstreme el estado de mis tarjetas",
        "Quiero bloquear una tarjeta",
        "No reconozco una compra en mi tarjeta",
      ],
      placeholder: "Escriba su mensaje",
      send: "Enviar",
      working: "Faro está preparando su respuesta.",
      problems: {
        invalid_request:
          "No pude procesar ese mensaje. Por favor, escríbalo de nuevo.",
        session_refused:
          "No pudimos continuar esta conversación. Cierre la sesión y vuelva a iniciarla.",
        no_customer:
          "Su usuario no tiene tarjetas que este chat pueda atender.",
        rate_limited:
          "Envió muchos mensajes seguidos. Espere un momento e inténtelo de nuevo.",
        daily_limit: (time) =>
          `Alcanzó el límite de mensajes de hoy. Vuelva a intentarlo ${FROM.es(time)}.`,
        global_limit: (time) =>
          `Faro alcanzó su límite de mensajes de hoy. Vuelva a intentarlo ${FROM.es(time)}.`,
        paused: "Faro está en pausa por ahora y no puede responder.",
        internal:
          "En este momento no puedo responder. Por favor, inténtelo de nuevo en unos minutos.",
        unreachable: "No pudimos comunicarnos con Faro. Inténtelo de nuevo.",
      },
    },
    control: {
      label: "Confirmar el bloqueo",
      card: (type, lastFour) =>
        `Bloquear ${CARD_TYPES.es[type]} terminada en ${lastFour}`,
      reason: (reason) => `Motivo: ${REASONS.es[reason]}`,
      undo: "Solo una persona del banco puede deshacer un bloqueo.",
      confirm: "Confirmar el bloqueo",
      cancel: "Cancelar",
      expired: "El tiempo para confirmar terminó.",
    },
    offer: {
      label: "Pasar su caso a una persona",
      reason: (reason) => OFFERS.es[reason],
      accept: "Pasar a una persona",
      decline: "Ahora no",
    },
  },
  pt: {
    notice:
      "Protótipo com dados sintéticos. O LATAM Bank e seus clientes são fictícios.",
    noticeBrief: "Protótipo com dados sintéticos.",
    notFound: {
      code: "Erro 404",
      title: "Página não encontrada",
      home: "Voltar ao início",
    },
    broken: "Não foi possível carregar o aplicativo. Recarregue a página.",
    signIn: {
      title: "Iniciar sessão",
      username: "Usuário",
      password: "Senha",
      submit: "Entrar",
      submitting: "Entrando…",
      refused: "Não foi possível entrar. Confira seu usuário e sua senha.",
      unreachable:
        "Não foi possível falar com o banco. Tente novamente em alguns minutos.",
      ended: "Sua sessão terminou. Entre de novo para continuar.",
      signOut: "Sair",
      endsAt: (time) => `Sua sessão terminará às ${time}.`,
      tabs: {
        label: "Formas de entrar",
        guest: "Convidado",
        account: "Com usuário",
      },
      guest: {
        title: "Acesso de convidado",
        lead: "Entre como um cliente fictício e converse com o Faro sobre seus cartões.",
        enter: "Entrar como convidado",
        entering: "Entrando…",
        full: (time) =>
          `Por hoje não há mais vagas para convidados. Tente novamente ${FROM.pt(time)}.`,
        paused: "O Faro está pausado no momento. Tente novamente mais tarde.",
      },
      terms: {
        banner: "Protótipo com dados sintéticos. Ao entrar, você aceita",
        more: "estas condições",
        title: "Condições",
        lines: [
          "O LATAM Bank e seus clientes são fictícios. Não escreva dados reais.",
          "As conversas são guardadas por até 12 meses e podem ser lidas para avaliar e melhorar o agente.",
          "A Anthropic, fornecedora do modelo, processa suas mensagens.",
          "Ninguém acompanha os casos que o Faro registra.",
          "Seu endereço IP é guardado por até 90 dias.",
          "Ao entrar, você aceita estas condições.",
        ],
        close: "Fechar",
      },
    },
    rail: {
      label: "Menu",
      open: "Abrir o menu",
      close: "Fechar o menu",
      newChat: "Nova conversa",
    },
    account: {
      label: "Sua conta",
      fictional: "Cliente fictício",
      cards: "Seus cartões",
      type: { "Tarjeta Crédito": "Crédito", "Tarjeta Débito": "Débito" },
      ending: "final",
    },
    language: {
      label: "Idioma",
      es: "Español",
      pt: "Português",
    },
    chat: {
      question: "Como posso ajudar?",
      suggestions: [
        "Mostre o status dos meus cartões",
        "Quero bloquear um cartão",
        "Não reconheço uma compra no meu cartão",
      ],
      placeholder: "Escreva sua mensagem",
      send: "Enviar",
      working: "O Faro está preparando sua resposta.",
      problems: {
        invalid_request:
          "Não consegui processar essa mensagem. Por favor, escreva de novo.",
        session_refused:
          "Não foi possível continuar esta conversa. Saia e entre de novo.",
        no_customer: "Seu usuário não tem cartões que este chat possa atender.",
        rate_limited:
          "Você enviou muitas mensagens seguidas. Aguarde um momento e tente novamente.",
        daily_limit: (time) =>
          `Você atingiu o limite de mensagens de hoje. Tente novamente ${FROM.pt(time)}.`,
        global_limit: (time) =>
          `O Faro atingiu o limite de mensagens de hoje. Tente novamente ${FROM.pt(time)}.`,
        paused: "O Faro está pausado no momento e não pode responder.",
        internal:
          "No momento não consigo responder. Por favor, tente novamente em alguns minutos.",
        unreachable: "Não foi possível falar com o Faro. Tente novamente.",
      },
    },
    control: {
      label: "Confirmar o bloqueio",
      card: (type, lastFour) =>
        `Bloquear ${CARD_TYPES.pt[type]} final ${lastFour}`,
      reason: (reason) => `Motivo: ${REASONS.pt[reason]}`,
      undo: "Só uma pessoa do banco pode desfazer um bloqueio.",
      confirm: "Confirmar o bloqueio",
      cancel: "Cancelar",
      expired: "O tempo para confirmar terminou.",
    },
    offer: {
      label: "Encaminhar seu caso para uma pessoa",
      reason: (reason) => OFFERS.pt[reason],
      accept: "Encaminhar para uma pessoa",
      decline: "Agora não",
    },
  },
};

// The UTC day ends less than a day from now, so its time alone names it.
export function dayEnds(language: Language, now: Date = new Date()): string {
  const end = new Date(now);
  end.setUTCHours(24, 0, 0, 0);
  return new Intl.DateTimeFormat(language, { timeStyle: "short" }).format(end);
}

export function problemText(
  language: Language,
  problem: Problem,
  now: Date = new Date(),
): string {
  const text = TEXTS[language].chat.problems[problem];
  return typeof text === "string" ? text : text(dayEnds(language, now));
}

export function isRunErrorCode(code: unknown): code is RunErrorCode {
  return (
    typeof code === "string" &&
    code !== "unreachable" &&
    Object.hasOwn(TEXTS.es.chat.problems, code)
  );
}
