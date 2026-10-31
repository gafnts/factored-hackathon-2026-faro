"""
The deterministic baseline (ADR-0005, Baselines; decision 4; EVL-01, DSN-03): a model factory whose runnables return
the typed outputs the provider's model returns, from word lists, patterns, and templates, so the graph, the tools, the
confirmation, and the handoff builder run unchanged and no model runs anywhere. Its words are authored from the
development families only, once, and frozen: where it loses to the agent, the loss is what the models add.

The language a message is mostly in (POL-50, POL-51) is read by the word lists the agent used until 2026-10-02: a
message that leans to Spanish or Portuguese by a clear margin is in that language, one with no word of either and two
of another language is other, and anything else is unclear, which keeps the conversation's language in the graph.

The router labels a message by keyword lists, one per label, matched as whole words in the message without case or
accents, and returns every label that matched in POL-05's order; a message no list matches holds no request (S5). A
complaint's words label it talk_to_human and mark it a complaint (POL-44). A request for the next page is a request for
transactions only when the chat's last reply offered one.

The extraction reads the card by its type's words and by four digits standing alone, as in "terminada en 4821" or a
bare "la 4821", never a date's year or an amount's (POL-13 to POL-16); a block's reason by the words for a loss, a
theft, a charge not recognized, or a reason kept to oneself (POL-35); all the cards (POL-14), the next page (POL-25), a
question about conflicting facts (POL-31), and what an unsupported request asks for (POL-41 to POL-43). The development
families ask for no earlier period and about no one else's card, so it reads neither.

The choice of a transaction keeps the listed ones that fit every amount, date, and merchant the message gives, a date
read as written or as today, yesterday, or the day before from the business date the listing states (POL-19, POL-27);
the newest when the customer says so, every one when the message tells none apart, and none when nothing fits.

A read's answer is a fixed template per request and language, chosen by the placeholders the graph lists, each written
once and no other (decision 8), so code fills it, the reply check reads it, and the oracle's facts compare with it as
they do with the model's; "usted" in Spanish and "você" in Portuguese (POL-50). A handoff's free text, in Spanish
(POL-46), states why the case goes to a person, as the graph gives it, and quotes the customer's latest messages.
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda

from faro.agent.models import (
    ORDER,
    Declared,
    HandoffText,
    Label,
    Language,
    RequestDetails,
    RouterOutput,
    TransactionChoice,
)
from faro.agent.texts import LANGUAGE_NAMES, placeholders
from faro.evaluation.scripted import listed

MODEL = "baseline"
USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}

MARGIN = 1
PORTUGUESE = frozenset(
    {
        "você", "vocês", "voce", "não", "nao", "cartão", "cartões", "cartao",
        "cartoes", "meu", "meus", "minha", "minhas", "olá", "oi", "obrigado",
        "obrigada", "quero", "gostaria", "estão", "são", "também", "isso", "esse",
        "essa", "qual", "quais", "é", "em", "um", "uma", "ajuda", "preciso", "tudo",
    }
)  # fmt: skip
SPANISH = frozenset(
    {
        "usted", "ustedes", "tarjeta", "tarjetas", "mis", "mi", "hola", "gracias",
        "quiero", "están", "son", "también", "eso", "ese", "esa", "cuál", "cuáles",
        "cual", "cuales", "qué", "el", "los", "las", "un", "una", "en", "del",
        "ayuda", "necesito", "puedo",
    }
)  # fmt: skip
# Words neither Spanish nor Portuguese uses, in the languages a judge is likeliest to try.
OTHER = frozenset(
    {
        "the", "is", "my", "what", "why", "how", "was", "were", "can", "could",
        "please", "you", "your", "card", "cards", "to", "of", "and", "with",
        "have", "hello", "hi", "thanks", "declined", "block", "balance",
        "pourquoi", "carte", "je", "vous", "bonjour", "merci", "avec", "été",
        "est", "ma", "il", "perché", "ciao", "grazie", "sono", "della",
        "non", "der", "die", "mein", "meine", "warum", "ist", "karte",
        "ich", "sie", "hallo", "danke", "nicht", "und", "mit",
    }
)  # fmt: skip
THIRD = 2
PORTUGUESE_LETTERS = "ãõç"
SPANISH_LETTERS = "ñ¿¡"

_WORD = re.compile(r"[^\W\d_]+")


def scores(text: str) -> tuple[int, int, int]:
    lowered = text.lower()
    words = _WORD.findall(lowered)
    portuguese = sum(w in PORTUGUESE for w in words) + sum(
        lowered.count(c) for c in PORTUGUESE_LETTERS
    )
    spanish = sum(w in SPANISH for w in words) + sum(
        lowered.count(c) for c in SPANISH_LETTERS
    )
    return spanish, portuguese, sum(w in OTHER for w in words)


def language(text: str) -> Language:
    spanish, portuguese, other = scores(text)
    if spanish == 0 and portuguese == 0 and other >= THIRD:
        return "other"
    if portuguese - spanish >= MARGIN:
        return "pt"
    if spanish - portuguese >= MARGIN:
        return "es"
    return "unclear"


# Each list holds words and phrases as normalized() leaves them: lower case, no accents, no punctuation.
LOST = (
    "perdi", "perdio", "perdida", "perdido", "perda", "extravie", "extravio",
    "extraviei", "sumiu", "no encuentro", "no la encuentro", "no aparece",
    "se me cayo", "desaparecio", "no se donde", "no tengo idea de donde",
    "nao acho", "nao aparece", "caiu", "nao sei onde", "nao faco ideia de onde",
)  # fmt: skip
# "robo" alone is also Portuguese's robot.
STOLEN = (
    "robaron", "un robo", "robo de", "de robo", "por robo", "asaltaron",
    "me sacaron", "roubaram", "roubo", "assaltado", "assaltada", "levaram",
)  # fmt: skip
BLOCKING = (
    "bloquear", "bloquearla", "bloquearlo", "bloqueo", "bloquee", "bloqueela",
    "bloqueen", "bloqueenla", "bloqueie", "bloqueiem", "bloqueio",
)  # fmt: skip
UNRECOGNIZED = (
    "no reconozco", "no lo reconozco", "no la reconozco", "nao reconheco",
    "desconozco", "desconheco", "desconocido", "desconhecido", "no hice",
    "no lo hice", "no la hice", "nao fiz", "no es mio", "no es mia", "nao e meu",
    "nao e minha", "no fui yo", "yo no fui", "nao fui eu", "sin permiso",
    "sem permissao", "extrano", "extrana", "estranho", "estranha", "raro",
    "no compre", "nao comprei", "sin que yo comprara", "sem eu comprar",
    "hecha por otra persona", "feita por outra pessoa",
)  # fmt: skip
TALK = (
    "asesor", "asesora", "agente", "una persona", "uma pessoa", "una pessoa",
    "humano", "ejecutivo", "operador", "supervisor", "superior", "encargado",
    "jefe", "chefe", "alguien del banco", "alguem do banco", "alguien real",
    "alguem de verdade", "hablar con", "falar com", "comuniqueme", "paseme",
    "me passe", "me passa para", "atendente", "robot", "um robo", "o robo",
    "maquina", "bot", "carne y hueso", "carne e osso",
)  # fmt: skip
# POL-44: a complaint is handed off under its own reason code.
COMPLAINTS = (
    "queja", "quejas", "quejarme", "reclamo", "reclamar", "reclamacion",
    "inconforme", "insatisfecho", "insatisfecha", "harto", "harta", "pesimo",
    "mal servicio", "es una verguenza", "exijo", "molesto", "molesta",
    "falta de seriedad", "paciencia", "no puede ser", "me han tratado",
    "nadie me", "nadie resuelve", "me han ignorado", "dando vueltas",
    "nunca me llego", "no me ha llegado", "no llego", "sigo esperando",
    "llevo semanas", "reclamacao", "queixa", "insatisfeito", "insatisfeita",
    "pessimo", "mau atendimento", "e uma vergonha", "cansado", "cansada",
    "chateado", "chateada", "falta de seriedade", "nao e possivel",
    "me trataram", "ninguem me", "ninguem resolve", "fui ignorado",
    "fui ignorada", "enrolando", "nunca chegou", "nao chegou",
    "continuo esperando", "estou ha semanas",
)  # fmt: skip
DECLINED = (
    "rechazaron", "rechazo", "rechazada", "rechazado", "rechazar", "declinada",
    "declinado", "declinaron", "negaron", "negar", "negado", "negada", "no paso",
    "no me aprobaron", "no aprobaron", "no se aprobo", "no fue aprobada",
    "no fue aprobado", "no funciono", "rebotaron", "no me dejaron",
    "no me aceptaron", "recusaram", "recusado", "recusada", "recusa", "recusou",
    "negaram", "negarem", "nao passou", "nao aprovaram", "nao foi aprovada",
    "nao foi aprovado", "nao funcionou", "rejeitado", "rejeitada",
    "nao aceitaram", "nao me deixaram",
)  # fmt: skip
STATUS = (
    "estado", "activa", "activo", "habilitada", "habilitado", "vence",
    "vencimiento", "vencer", "valida", "expira", "bloqueada", "bloqueado",
    "funciona", "funcionando", "como esta mi", "como esta la tarjeta",
    "como aparece", "todavia sirve", "situacao", "status", "ativo", "ativa",
    "liberado", "liberada", "validade", "valido", "como esta o meu",
    "como esta meu", "como ele esta", "ainda serve",
)  # fmt: skip
CREDIT = (
    "disponible", "disponivel", "cuanto cupo", "cupo disponible",
    "me queda cupo", "tengo cupo", "cupo libre", "me queda", "le queda",
    "me sobra", "sobra", "resta", "puedo gastar", "puedo usar", "posso gastar",
    "posso usar", "cuanto tengo", "quanto tenho", "limite livre",
    "quanto limite", "quanto de limite", "tenho limite", "tope", "teto",
    "alcanza", "aguenta", "da para", "estourei", "me pase del limite",
    "llevo usado", "he gastado", "ja usei", "quanto gastei",
)  # fmt: skip
TRANSACTIONS = (
    "movimientos", "movimentacoes", "transacciones", "transacoes", "historial",
    "historico", "mis compras", "minhas compras", "las compras", "as compras",
    "ultimas compras", "consumos", "gastos", "extracto", "extrato",
    "recientes", "recentes", "recientemente", "recentemente", "ultimamente",
    "lo ultimo", "o ultimo", "que compras", "se ha pagado", "foi pago",
    "que pague", "o que paguei", "se me fue", "onde foi parar",
)  # fmt: skip
FOLLOWING = (
    "siguientes", "los que siguen", "siguiente pagina", "mas movimientos",
    "los demas", "demas movimientos", "vienen despues", "proximos",
    "proxima pagina", "seguintes", "vem depois", "tem mais",
    "outras movimentacoes", "mais movimentacoes",
)  # fmt: skip
# What an unsupported request asks for, in the order a message naming two is read (POL-41 to POL-43).
SERVICES = (
    ("unblock", (
        "desbloquear", "desbloqueo", "desbloqueio", "reactiven", "reactivar",
        "reativem", "reativar", "quitarle el bloqueo", "tirar o bloqueio",
        "de nuevo", "de novo", "volver a usar", "volver a usarla",
        "voltar a usar", "volver a activar", "ya no quiero que",
        "nao quero mais que", "por error", "por engano", "recupere", "recuperei",
        "la encontre", "achei o cartao", "sin avisarme", "sem avisar", "frenada",
        "travado", "la necesito", "preciso usar", "preciso dele",
    )),
    ("replacement", (
        "reposicion", "tarjeta nueva", "duplicado", "danada", "reemplazo",
        "plastico nuevo", "plastico novo", "segunda via", "cartao novo",
        "via nova", "danificado", "substituicao",
    )),
    ("pin", ("pin", "clave", "contrasena", "senha")),
    ("limit_increase", (
        "aumentar", "aumentarme", "suban", "ampliacion", "ampliacao",
        "mas limite", "mais limite", "incrementar", "limite mas alto",
        "limite maior", "aumentem",
    )),
    ("other_card_service", (
        "estado de cuenta", "extracto mensual", "fecha de corte", "cuotas",
        "diferir", "adicional", "certificado", "fatura", "parcelar", "declaracao",
    )),
    ("outside_cards", (
        "mi cuenta", "cuenta de ahorros", "cuenta corriente", "cuenta bancaria",
        "minha conta", "conta corrente", "conta bancaria", "poupanca",
        "prestamo", "emprestimo",
    )),
)  # fmt: skip
UNSUPPORTED = tuple(word for _, words in SERVICES for word in words)
KEYWORDS: dict[Label, tuple[str, ...]] = {
    "block_card": (*BLOCKING, *LOST, *STOLEN),
    "unrecognized_charge": UNRECOGNIZED,
    "talk_to_human": TALK,
    "decline_reason": DECLINED,
    "card_status": STATUS,
    "available_credit": CREDIT,
    "recent_transactions": TRANSACTIONS,
    "unsupported": UNSUPPORTED,
}


def normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]", " ", plain).split())


def has(plain: str, words: Iterable[str]) -> bool:
    """
    Whether a normalized text holds any of the words, each as whole words.
    """
    padded = f" {plain} "
    return any(f" {word} " in padded for word in words)


def route(text: str, context: str | None = None) -> RouterOutput:
    """
    context is what the chat's last reply offered, as the graph gives the router.
    """
    plain = normalized(text)
    found = {label for label, words in KEYWORDS.items() if has(plain, words)}
    complaint = has(plain, COMPLAINTS)
    if complaint:
        found.add("talk_to_human")
    if context is not None and has(plain, FOLLOWING):
        found.add("recent_transactions")
    requests = [label for label in ORDER if label in found]
    return RouterOutput(
        language=language(text),
        requests=requests,
        has_request=bool(requests),
        complaint=complaint,
    )


CREDIT_CARD = ("de credito",)
DEBIT_CARD = ("debito",)
ALL_CARDS = (
    "todas mis tarjetas", "todas las tarjetas", "cada una de mis tarjetas",
    "cada tarjeta", "mis tarjetas", "de todas", "todos os meus cartoes",
    "todos os cartoes", "cada um dos meus cartoes", "cada cartao",
    "meus cartoes", "de todos",
)  # fmt: skip
KEPT_TO_ONESELF = (
    "prefiero no", "prefiro nao", "personal", "personales", "pessoal",
    "pessoais", "sin indicar", "sem informar", "explicaciones", "explicacoes",
    "decision mia", "decisao minha",
)  # fmt: skip
# The model's names for POL-35's codes (models.REASON_CODES), in the order a message naming two is read.
REASONS = (
    ("stolen", STOLEN),
    ("lost", LOST),
    ("unrecognized_charge", UNRECOGNIZED),
    ("other_reason", KEPT_TO_ONESELF),
)
CONFLICTS = (
    "cual es la verdad", "quien tiene razon", "que vale", "cual dato",
    "cual de las dos", "todavia sirve", "puedo usarla o no",
    "aunque haya pasado", "como que vencida", "no cuadra", "expliqueme cual",
    "qual e a verdade", "quem tem razao", "o que vale", "qual informacao",
    "qual dos dois", "ainda serve", "posso usar ou nao", "mesmo depois",
    "como assim vencido", "nao bate", "me explique qual",
)  # fmt: skip
# Not part of a longer number, a date, or an amount: "4821", but not "2026" in "14/06/2026" or "1177" in "1177.00".
LAST_FOUR = re.compile(r"(?<![0-9/-])(?<![0-9][.,])[0-9]{4}(?![0-9/-])(?![.,][0-9])")


def first[T](plain: str, lists: Iterable[tuple[T, tuple[str, ...]]]) -> T | None:
    return next((name for name, words in lists if has(plain, words)), None)


def last_four(text: str) -> str | None:
    found = set(LAST_FOUR.findall(text))
    return found.pop() if len(found) == 1 else None


def extract(text: str) -> RequestDetails:
    plain = normalized(text)
    every = has(plain, ALL_CARDS)
    credit, debit = has(plain, CREDIT_CARD), has(plain, DEBIT_CARD)
    return RequestDetails.model_validate(
        {
            "language": language(text),
            "card_type": (
                None if every or credit == debit else "credit" if credit else "debit"
            ),
            "last_four": last_four(text),
            "block_reason": first(plain, REASONS),
            "cards": "all" if every else None,
            "page": "next" if has(plain, FOLLOWING) else None,
            "owner": None,
            "conflict": "asks_which" if has(plain, CONFLICTS) else None,
            "service": first(plain, SERVICES),
            "question": None,
        }
    )


NEWEST = (
    "la mas reciente", "el mas reciente", "la ultima", "a mais recente",
    "o mais recente", "a ultima",
)  # fmt: skip
# Days back from the business date, the day before first, since "antes de ayer" holds "ayer".
DAYS_BACK = (
    (2, ("anteayer", "antier", "antes de ayer", "anteontem", "antes de ontem")),
    (1, ("ayer", "ontem")),
    (0, ("hoy", "hoje")),
)
TODAY = re.compile(r"Today is \w+ ([0-9]{4}-[0-9]{2}-[0-9]{2})")
WRITTEN_DAY = re.compile(r"\b([0-9]{2})/([0-9]{2})/([0-9]{4})\b")
ISO_DAY = re.compile(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b")
AMOUNT = re.compile(r"[0-9][0-9.,]*[.,][0-9]{2}(?![0-9])")


def amounts(text: str) -> set[Decimal]:
    """
    Every amount the text gives with its cents, grouped either way ("1,234.56" or "1.234,56").
    """
    found = set()
    for written in AMOUNT.findall(text):
        whole = re.sub(r"[.,]", "", written[:-3])
        found.add(Decimal(f"{whole}.{written[-2:]}"))
    return found


def days(text: str, plain: str, listing: str) -> set[str]:
    found = {f"{y}-{m}-{d}" for d, m, y in WRITTEN_DAY.findall(text)}
    found |= set(ISO_DAY.findall(text))
    today = TODAY.search(listing)
    back = first(plain, DAYS_BACK)
    if today is not None and back is not None:
        found.add(
            (date.fromisoformat(today.group(1)) - timedelta(days=back)).isoformat()
        )
    return found


def choose(text: str, listing: str) -> TransactionChoice:
    """
    listing is what the graph gives the choice: the request, the business date, and the transactions, numbered.
    """
    shown = listed(listing)
    plain = normalized(text)
    if has(plain, NEWEST):
        fitting = [n for n, *_ in shown[:1]]
    else:
        given, on = amounts(text), days(text, plain, listing)
        named = {
            n
            for n, _, _, merchant in shown
            if normalized(merchant) and has(plain, [normalized(merchant)])
        }
        fitting = [
            n
            for n, day, value, _ in shown
            if (not given or value in given)
            and (not on or day in on)
            and (not named or n in named)
        ]
    return TransactionChoice(
        language=language(text), fitting=fitting[:10], question=None
    )


REPLIES: dict[str, tuple[dict[str, str], ...]] = {
    "card_status": (
        {
            "es": "Su {card} está {card.status}. Vencimiento: {card.expiration}.",
            "pt": "Seu {card} está {card.status}. Validade: {card.expiration}.",
        },
        {
            "es": "El estado de sus tarjetas es el siguiente:\n\n{cards}",
            "pt": "Este é o status dos seus cartões:\n\n{cards}",
        },
    ),
    "available_credit": (
        {
            "es": "Su {card} tiene {credit.available} de crédito disponible al {as_of}.",
            "pt": "Seu {card} tem {credit.available} de crédito disponível em {as_of}.",
        },
        {
            "es": "Su {card} no tiene crédito disponible al {as_of}: el saldo supera el límite en {credit.over_by}.",
            "pt": "Seu {card} não tem crédito disponível em {as_of}: o saldo ultrapassa o limite em {credit.over_by}.",
        },
    ),
    "recent_transactions": (
        {
            "es": "Movimientos de su {card} del {window.from} al {window.to}:\n\n{transactions}",
            "pt": "Transações do seu {card} de {window.from} a {window.to}:\n\n{transactions}",
        },
    ),
    "decline_reason": (
        {
            "es": "La transacción de su {card} fue rechazada:\n\n{transaction}\n{transaction.meaning}",
            "pt": "A transação do seu {card} foi recusada:\n\n{transaction}\n{transaction.meaning}",
        },
        {
            "es": "La transacción de su {card} figura como {transaction.status}:\n\n{transaction}",
            "pt": "A transação do seu {card} consta como {transaction.status}:\n\n{transaction}",
        },
    ),
}
REQUEST = re.compile(r"^Request: ([a-z_]+)\.$", re.MULTILINE)
LISTED = re.compile(r"^- \{([a-z_.]+)\}:", re.MULTILINE)


def reply(instructions: str, facts: str) -> str:
    """
    instructions is the reply's prompt, which names the language; facts is the request, the records in words, and the
    placeholders to write. A request the templates don't cover gets its placeholders alone, one per paragraph.
    """
    language = next(c for c, name in LANGUAGE_NAMES.items() if name in instructions)
    label = REQUEST.search(facts)
    wanted = list(dict.fromkeys(LISTED.findall(facts)))
    for template in REPLIES.get(label.group(1) if label else "", ()):
        if set(placeholders(template[language])) == set(wanted):
            return template[language]
    return "\n\n".join(f"{{{name}}}" for name in wanted)


# What a staff member reads, in the bank's working language whatever the customer's (POL-46).
QUOTED = "El cliente escribió: «{}»"
STATEMENTS = 5


def handoff_text(conversation: str, context: str) -> HandoffText:
    """
    conversation is the transcript the graph sends, its turns apart by a blank line; context says why the case goes to
    a person.
    """
    said = [
        turn.removeprefix("Customer: ").strip()
        for turn in conversation.split("\n\n")
        if turn.startswith("Customer: ")
    ]
    return HandoffText(
        summary=context.partition(": ")[2].strip() or context.strip(),
        customer_statements=[QUOTED.format(s) for s in said[-STATEMENTS:]],
        unresolved_questions=[],
    )


def raw(content: str = "{}") -> AIMessage:
    return AIMessage(
        content=content, usage_metadata=USAGE, response_metadata={"model_name": MODEL}
    )


def structured(parsed: Any) -> dict[str, Any]:
    return {"raw": raw(), "parsed": parsed, "parsing_error": None}


def blocks(message: BaseMessage) -> list[str]:
    """
    The text blocks of a system message: the step's instructions first, then what the graph adds.
    """
    if isinstance(message.content, str):
        return [message.content]
    return [b["text"] for b in message.content if isinstance(b, dict)]


def added(messages: list[BaseMessage]) -> str | None:
    """
    What the graph adds after a step's instructions: the router's offer, the extraction's request, the listing.
    """
    given = blocks(messages[0])
    return given[1] if len(given) > 1 else None


async def routed(messages: list[BaseMessage]) -> dict[str, Any]:
    return structured(route(messages[-1].text, added(messages)))


async def extracted(messages: list[BaseMessage]) -> dict[str, Any]:
    return structured(extract(messages[-1].text))


async def chosen(messages: list[BaseMessage]) -> dict[str, Any]:
    return structured(choose(messages[-1].text, added(messages) or ""))


async def handed(messages: list[BaseMessage]) -> dict[str, Any]:
    return structured(handoff_text(messages[-1].text, added(messages) or ""))


async def written(messages: list[BaseMessage]) -> AIMessage:
    return raw(reply(blocks(messages[0])[0], added(messages) or ""))


STRUCTURED = {
    "route": routed,
    "extract": extracted,
    "choose": chosen,
    "handoff_text": handed,
}


def runnable(purpose: str) -> Runnable[Any, Any]:
    """
    The baseline's runnable for a model call's purpose, in place of the provider's (models.Factory): a reply for any
    purpose without a structured output, as the provider's factory gives plain text.
    """
    if purpose in STRUCTURED:
        return RunnableLambda(STRUCTURED[purpose])
    return RunnableLambda(written)


factory = Declared(runnable, None, MODEL)
