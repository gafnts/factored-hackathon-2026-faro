"""
The replies code gives in fixed text, in each of the chat's languages (POL-50), named as the execution record's reply
entry names them, and the placeholders that fill them. Every reply but the four reads' answers is fixed text: code
chooses it from the tools' results and the confirmation, so the model never reports an action, asks a question, or
declines (ADR-0004, decision 8, and its amendment of 2026-10-01; AI-05). A read's answer is fixed text too when the model
doesn't write it, or writes one the reply check refuses: its fixed reply states the same facts, under the same
placeholders, formatted as formats.py says. An offered handoff and a filed handoff's reference reach the customer here
too (POL-45).
"""

import re
from collections.abc import Mapping
from typing import Any

from faro.agent.formats import (
    STATUSES,
    TRANSACTION_STATUSES,
    amount,
    card_line,
    card_name,
    credit_line,
    day,
    expiration,
    moment,
    reason_line,
    transaction_line,
    transaction_name,
)

FIXED: dict[str, dict[str, str]] = {
    # POL-06: a message with no card request, by what it is. The introduction answers the first greeting and any
    # message aimed at the chat itself, an injection with no request included; a later greeting, thanks, and a
    # goodbye get a line each, and the rest the list.
    "no_request": {
        "es": "Hola, soy Faro, el asistente automático de tarjetas de LATAM Bank. Puedo mostrarle sus tarjetas y el estado de cada una, el crédito disponible de una tarjeta de crédito y sus movimientos recientes, decirle el motivo registrado de un pago rechazado, bloquear una tarjeta y ayudarle con un cargo que no reconoce. ¿En qué le puedo ayudar?",
        "pt": "Olá, eu sou o Faro, o assistente automático de cartões do LATAM Bank. Posso mostrar seus cartões e o status de cada um, o crédito disponível de um cartão de crédito e suas transações recentes, dizer o motivo registrado de um pagamento recusado, bloquear um cartão e ajudar com uma cobrança que você não reconhece. Como posso ajudar?",
    },
    "greeting": {
        "es": "Hola. ¿En qué le puedo ayudar?",
        "pt": "Olá. Como posso ajudar?",
    },
    "capabilities": {
        "es": "En este chat puedo mostrarle sus tarjetas y el estado de cada una, el crédito disponible de una tarjeta de crédito y sus movimientos recientes, decirle el motivo registrado de un pago rechazado, bloquear una tarjeta y ayudarle con un cargo que no reconoce. ¿En qué le puedo ayudar?",
        "pt": "Neste chat posso mostrar seus cartões e o status de cada um, o crédito disponível de um cartão de crédito e suas transações recentes, dizer o motivo registrado de um pagamento recusado, bloquear um cartão e ajudar com uma cobrança que você não reconhece. Como posso ajudar?",
    },
    "thanks": {
        "es": "Con gusto. Quedo a su disposición para cualquier otra consulta sobre sus tarjetas.",
        "pt": "Por nada. Fico à disposição para qualquer outra dúvida sobre seus cartões.",
    },
    "closing": {
        "es": "Quedo a su disposición para cualquier otra consulta sobre sus tarjetas.",
        "pt": "Fico à disposição para qualquer outra dúvida sobre seus cartões.",
    },
    # POL-51: a third language gets Spanish, with one sentence in Portuguese.
    "third_language": {
        "es": "Este chat atiende en español y en portugués; por favor, escríbame en uno de esos idiomas. Este chat atende em espanhol e em português.",
        "pt": "Este chat atiende en español y en portugués; por favor, escríbame en uno de esos idiomas. Este chat atende em espanhol e em português.",
    },
    # POL-05: the requests left for after the one in progress.
    "queued": {
        "es": "Después sigo con {requests}.",
        "pt": "Depois continuo com {requests}.",
    },
    # POL-48; a routed request's failure is followed by handoff_offer.
    "unavailable": {
        "es": "En este momento no puedo ayudarle con eso. Por favor, inténtelo de nuevo en unos minutos.",
        "pt": "No momento não posso ajudar com isso. Por favor, tente novamente em alguns minutos.",
    },
    # POL-08 and POL-49.
    "refused": {
        "es": "Solo puedo atender las tarjetas de la persona que inició sesión.",
        "pt": "Só posso atender os cartões da pessoa que entrou na sessão.",
    },
    # POL-13 to POL-16, for the reads.
    "which_card_read": {
        "es": "¿Sobre cuál de estas tarjetas me pregunta?\n\n{card_list}",
        "pt": "Sobre qual destes cartões você está perguntando?\n\n{card_list}",
    },
    "no_matching_card_read": {
        "es": "No encuentro una tarjeta suya que coincida con lo que me indica. Estas son sus tarjetas:\n\n{card_list}\n\n¿Sobre cuál me pregunta?",
        "pt": "Não encontrei um cartão seu que corresponda ao que você indicou. Estes são os seus cartões:\n\n{card_list}\n\nSobre qual você está perguntando?",
    },
    # POL-01 and POL-21: a status answer's fixed reply, for one card and for each. POL-14's "my cards" lists an only card
    # too, so the chat draws it as it draws several.
    "card_status": {
        "es": "Su {card} está {card.status}; fecha de vencimiento: {card.expiration}.",
        "pt": "Seu {card} está {card.status}; validade: {card.expiration}.",
    },
    "cards_status": {
        "es": "Estas son sus tarjetas y el estado de cada una:\n\n{cards}",
        "pt": "Estes são os seus cartões e o status de cada um:\n\n{cards}",
    },
    "only_card_status": {
        "es": "Esta es su tarjeta y su estado:\n\n{cards}",
        "pt": "Este é o seu cartão e o status dele:\n\n{cards}",
    },
    # POL-01, POL-19, POL-22 to POL-24; POL-14's cards with a figure, when two or more, under one sentence.
    "credits_available": {
        "es": "Al {as_of}, este es el crédito disponible de sus tarjetas:\n\n{credits}",
        "pt": "Em {as_of}, este é o crédito disponível dos seus cartões:\n\n{credits}",
    },
    "credit_available": {
        "es": "Al {as_of}, su {card} tiene {credit.available} de crédito disponible.",
        "pt": "Em {as_of}, seu {card} tem {credit.available} de crédito disponível.",
    },
    "credit_over_limit": {
        "es": "Al {as_of}, su {card} no tiene crédito disponible: su saldo supera el límite en {credit.over_by}.",
        "pt": "Em {as_of}, seu {card} não tem crédito disponível: o saldo ultrapassa o limite em {credit.over_by}.",
    },
    "credit_debit_card": {
        "es": "Su {card} es una tarjeta de débito, que no tiene crédito disponible. Los saldos de sus cuentas no se consultan en este chat.",
        "pt": "Seu {card} é um cartão de débito, que não tem crédito disponível. Os saldos das suas contas não são consultados neste chat.",
    },
    "credit_not_active": {
        "es": "Su {card} está {card.status}. El crédito disponible solo se muestra para tarjetas activas.",
        "pt": "Seu {card} está {card.status}. O crédito disponível só é mostrado para cartões ativos.",
    },
    # POL-24, followed by handoff_offer.
    "credit_no_limit": {
        "es": "El límite de crédito de su {card} no está registrado, así que no puedo darle una cifra de crédito disponible.",
        "pt": "O limite de crédito do seu {card} não está registrado, então não posso informar o crédito disponível.",
    },
    # POL-19 and POL-25.
    "transactions_page": {
        "es": "Estos son los movimientos de su {card} entre el {window.from} y el {window.to}, del más reciente al más antiguo:\n\n{transactions}",
        "pt": "Estas são as transações do seu {card} entre {window.from} e {window.to}, da mais recente à mais antiga:\n\n{transactions}",
    },
    "transactions_next": {
        "es": "Estos son los siguientes movimientos de su {card} entre el {window.from} y el {window.to}:\n\n{transactions}",
        "pt": "Estas são as próximas transações do seu {card} entre {window.from} e {window.to}:\n\n{transactions}",
    },
    "transactions_more": {
        "es": "Si quiere, puede pedirme los 10 siguientes.",
        "pt": "Se quiser, pode me pedir as próximas 10.",
    },
    "transactions_none": {
        "es": "Su {card} no tiene movimientos entre el {window.from} y el {window.to}.",
        "pt": "Seu {card} não tem transações entre {window.from} e {window.to}.",
    },
    "transactions_no_more": {
        "es": "No hay más movimientos de su {card} entre el {window.from} y el {window.to}.",
        "pt": "Não há mais transações do seu {card} entre {window.from} e {window.to}.",
    },
    "transactions_earlier": {
        "es": "Solo puedo mostrarle los movimientos de los últimos 90 días, entre el {window.from} y el {window.to}.",
        "pt": "Só posso mostrar as transações dos últimos 90 dias, entre {window.from} e {window.to}.",
    },
    # POL-02 and POL-27 to POL-29: the code's meaning and nothing else.
    "decline_explained": {
        "es": "Encontré esta transacción rechazada en su {card}:\n\n{transaction}\n{transaction.meaning}",
        "pt": "Encontrei esta transação recusada no seu {card}:\n\n{transaction}\n{transaction.meaning}",
    },
    "decline_status": {
        "es": "Encontré esta transacción en su {card}:\n\n{transaction}\n\nFigura como {transaction.status}, no como rechazada.",
        "pt": "Encontrei esta transação no seu {card}:\n\n{transaction}\n\nEla consta como {transaction.status}, não como recusada.",
    },
    # POL-32, followed by handoff_offer.
    "decline_no_code": {
        "es": "Encontré esta transacción rechazada en su {card}:\n\n{transaction}\n\nNo hay un motivo registrado para este rechazo.",
        "pt": "Encontrei esta transação recusada no seu {card}:\n\n{transaction}\n\nNão há um motivo registrado para esta recusa.",
    },
    "decline_not_found": {
        "es": "No encontré en los últimos 90 días de su {card} una transacción que coincida con lo que me indica.",
        "pt": "Não encontrei nos últimos 90 dias do seu {card} uma transação que corresponda ao que você indicou.",
    },
    "which_decline": {
        "es": "Encontré más de una transacción en su {card} que podría ser la que me indica. ¿Cuál es?\n\n{transactions}",
        "pt": "Encontrei mais de uma transação no seu {card} que pode ser a que você indicou. Qual é?\n\n{transactions}",
    },
    # POL-30: both facts, neither chosen.
    "code_conflict": {
        "es": "El motivo de este rechazo es tarjeta vencida, aunque la transacción es anterior a la fecha de vencimiento registrada de su {card}: {card.expiration}.",
        "pt": "O motivo desta recusa é cartão vencido, embora a transação seja anterior à data de validade registrada do seu {card}: {card.expiration}.",
    },
    "before_opening": {
        "es": "La fecha de esta transacción es anterior a la fecha de apertura registrada de su {card}.",
        "pt": "A data desta transação é anterior à data de abertura registrada do seu {card}.",
    },
    # POL-31, followed by handoff_offer.
    "conflict_unresolved": {
        "es": "No puedo decirle cuál de los dos datos es el correcto.",
        "pt": "Não posso dizer qual dos dois dados é o correto.",
    },
    # POL-41, followed by handoff_filed.
    "unblock_by_person": {
        "es": "Para desbloquear una tarjeta hace falta una verificación que este chat no puede hacer.",
        "pt": "Para desbloquear um cartão é preciso uma verificação que este chat não pode fazer.",
    },
    # POL-42, followed by handoff_offer.
    "unsupported_service": {
        "es": "Por este chat no puedo gestionar {service}.",
        "pt": "Por este chat não posso fazer {service}.",
    },
    # POL-43: no person offered unless the customer asks for one.
    "outside_cards": {
        "es": "Este chat atiende solo sus tarjetas, así que no puedo ayudarle con esa solicitud.",
        "pt": "Este chat atende apenas os seus cartões, então não posso ajudar com esse pedido.",
    },
    # POL-14.
    "which_card": {
        "es": "¿Cuál de estas tarjetas quiere bloquear?\n\n{card_list}",
        "pt": "Qual destes cartões você quer bloquear?\n\n{card_list}",
    },
    # POL-15: two cards of the same type share the last four digits.
    "ambiguous_card": {
        "es": "Tiene más de una tarjeta del mismo tipo terminada en {last_four}, así que no puedo saber a cuál se refiere.",
        "pt": "Você tem mais de um cartão do mesmo tipo final {last_four}, então não consigo saber a qual se refere.",
    },
    # POL-15.
    "which_type": {
        "es": "Tiene más de una tarjeta terminada en {last_four}. ¿Es la de crédito o la de débito?",
        "pt": "Você tem mais de um cartão final {last_four}. É o de crédito ou o de débito?",
    },
    # POL-16.
    "no_matching_card": {
        "es": "No encuentro una tarjeta suya que coincida con lo que me indica. Estas son sus tarjetas:\n\n{card_list}\n\n¿Cuál quiere bloquear?",
        "pt": "Não encontrei um cartão seu que corresponda ao que você indicou. Estes são os seus cartões:\n\n{card_list}\n\nQual você quer bloquear?",
    },
    # POL-14 and POL-16, for a charge the customer doesn't recognize.
    "which_card_charge": {
        "es": "¿En cuál de estas tarjetas está el cargo que no reconoce?\n\n{card_list}",
        "pt": "Em qual destes cartões está a cobrança que você não reconhece?\n\n{card_list}",
    },
    "no_matching_card_charge": {
        "es": "No encuentro una tarjeta suya que coincida con lo que me indica. Estas son sus tarjetas:\n\n{card_list}\n\n¿En cuál está el cargo que no reconoce?",
        "pt": "Não encontrei um cartão seu que corresponda ao que você indicou. Estes são os seus cartões:\n\n{card_list}\n\nEm qual está a cobrança que você não reconhece?",
    },
    # POL-39, as in POL-27: the charge is looked for in any status, and whether it is fraud is never said.
    "charge_found": {
        "es": "Encontré este cargo en su {card}:\n\n{transaction}",
        "pt": "Encontrei esta cobrança no seu {card}:\n\n{transaction}",
    },
    "which_charge": {
        "es": "Encontré más de un cargo en su {card} que podría ser el que me indica. ¿Cuál es?\n\n{transactions}",
        "pt": "Encontrei mais de uma cobrança no seu {card} que pode ser a que você indicou. Qual é?\n\n{transactions}",
    },
    "charge_not_found": {
        "es": "No encontré en los últimos 90 días de su {card} un cargo que coincida con lo que me indica.",
        "pt": "Não encontrei nos últimos 90 dias do seu {card} uma cobrança que corresponda ao que você indicou.",
    },
    "charge_unread": {
        "es": "En este momento no pude consultar los movimientos de su {card}.",
        "pt": "No momento não consegui consultar as transações do seu {card}.",
    },
    "records_unavailable": {
        "es": "En este momento no pude consultar sus tarjetas.",
        "pt": "No momento não consegui consultar seus cartões.",
    },
    "no_cards": {
        "es": "No encuentro tarjetas a su nombre.",
        "pt": "Não encontrei cartões em seu nome.",
    },
    # POL-35.
    "ask_reason": {
        "es": "¿Por qué quiere bloquear su {card}? Puede ser por pérdida, por robo, por un cargo que no reconoce o por otro motivo; si prefiere no decirlo, también puedo bloquearla.",
        "pt": "Por que você quer bloquear seu {card}? Pode ser por perda, por roubo, por uma cobrança que você não reconhece ou por outro motivo; se preferir não dizer, também posso bloqueá-lo.",
    },
    # POL-17, followed by handoff_offer.
    "clarification_stopped": {
        "es": "No logré precisar su solicitud con estas preguntas, así que no voy a seguir preguntando.",
        "pt": "Não consegui entender seu pedido com estas perguntas, então não vou continuar perguntando.",
    },
    # POL-31: both facts, neither chosen.
    "past_expiration": {
        "es": "Su {card} figura como activa, aunque su fecha de vencimiento registrada ya pasó.",
        "pt": "Seu {card} consta como ativo, embora a data de validade registrada já tenha passado.",
    },
    # POL-36: the control names the card and the reason again.
    "confirm_prompt": {
        "es": "Para bloquear su {card} por {reason}, confirme con el botón. Solo una persona del banco puede deshacer un bloqueo.",
        "pt": "Para bloquear seu {card} por {reason}, confirme no botão. Só uma pessoa do banco pode desfazer um bloqueio.",
    },
    "control_pointer": {
        "es": "Para bloquear la tarjeta, use el botón: un mensaje escrito no confirma el bloqueo. Si prefiere no bloquearla, puede cancelar con el botón.",
        "pt": "Para bloquear o cartão, use o botão: uma mensagem escrita não confirma o bloqueio. Se preferir não bloqueá-lo, pode cancelar no botão.",
    },
    "charge_reviewed": {
        "es": "El banco revisará el cargo que no reconoce, bloquee o no la tarjeta.",
        "pt": "O banco vai analisar a cobrança que você não reconhece, bloqueando ou não o cartão.",
    },
    "confirmation_cancelled": {
        "es": "De acuerdo: no bloqueé su {card}.",
        "pt": "Certo: não bloqueei seu {card}.",
    },
    "confirmation_lapsed": {
        "es": "La confirmación para bloquear su {card} ya no está vigente, así que no la bloqueé.",
        "pt": "A confirmação para bloquear seu {card} não está mais válida, então não o bloqueei.",
    },
    # POL-37: blocked only when the read-back shows it.
    "block_verified": {
        "es": "Listo: su {card} quedó bloqueada.",
        "pt": "Pronto: seu {card} está bloqueado.",
    },
    "block_not_verified": {
        "es": "No pude confirmar que su {card} quedara bloqueada, así que no puedo darla por bloqueada.",
        "pt": "Não consegui confirmar que seu {card} foi bloqueado, então não posso dá-lo como bloqueado.",
    },
    # POL-38, followed by handoff_offer.
    "replacement_by_person": {
        "es": "La reposición de la tarjeta la gestiona una persona del banco.",
        "pt": "A reposição do cartão é feita por uma pessoa do banco.",
    },
    # POL-34.
    "already_blocked": {
        "es": "Su {card} ya está bloqueada.",
        "pt": "Seu {card} já está bloqueado.",
    },
    "not_blockable": {
        "es": "Su {card} está {card.status}, así que no se puede bloquear.",
        "pt": "Seu {card} está {card.status}, então não pode ser bloqueado.",
    },
    # POL-45: a person follows up, with no outcome or time promised.
    "handoff_filed": {
        "es": "Pasé su caso a una persona del banco, que le dará seguimiento. La referencia de su caso es {reference}.",
        "pt": "Passei seu caso para uma pessoa do banco, que vai dar continuidade a ele. A referência do seu caso é {reference}.",
    },
    # POL-45: the handoff control shows below it, and only the control accepts.
    "handoff_offer": {
        "es": "Si lo prefiere, puedo pasar su caso a una persona del banco: acéptelo con el botón.",
        "pt": "Se preferir, posso encaminhar seu caso para uma pessoa do banco: aceite no botão.",
    },
    "offer_pointer": {
        "es": "Para pasar su caso a una persona del banco, use el botón: un mensaje escrito no lo acepta. Si prefiere no hacerlo, puede rechazarlo con el botón.",
        "pt": "Para encaminhar seu caso para uma pessoa do banco, use o botão: uma mensagem escrita não o aceita. Se preferir não fazer isso, pode recusar no botão.",
    },
    "offer_declined": {
        "es": "De acuerdo: no pasé su caso a una persona del banco.",
        "pt": "Certo: não encaminhei seu caso para uma pessoa do banco.",
    },
    # POL-09: an offer lapses with the session.
    "offer_lapsed": {
        "es": "La oferta de pasar su caso a una persona del banco ya no está vigente.",
        "pt": "A oferta de encaminhar seu caso para uma pessoa do banco não está mais válida.",
    },
    # POL-48: the case couldn't be filed.
    "handoff_failed": {
        "es": "Este caso lo debe atender una persona del banco, pero en este momento no pude pasárselo. Por favor, inténtelo de nuevo en unos minutos.",
        "pt": "Este caso precisa ser atendido por uma pessoa do banco, mas no momento não consegui encaminhá-lo. Por favor, tente novamente em alguns minutos.",
    },
}

# A reply that failed the check is replaced by the unavailable text, under its own name.
FIXED["reply_fallback"] = FIXED["unavailable"]
# POL-06: a message that doesn't answer the agent's question gets its reply without the reply's own closing question,
# since the agent's question follows it.
CLOSING = {"es": " ¿En qué le puedo ayudar?", "pt": " Como posso ajudar?"}
for _name in ("no_request", "greeting", "capabilities"):
    FIXED[f"{_name}_aside"] = {
        language: text.removesuffix(CLOSING[language])
        for language, text in FIXED[_name].items()
    }

LANGUAGE_NAMES = {
    "es": "Spanish, addressing the customer as “usted”",
    "pt": "Brazilian Portuguese, addressing the customer as “você”",
}

REASONS = {
    "es": {
        "lost": "pérdida",
        "stolen": "robo",
        "unrecognized_charge": "un cargo que no reconoce",
        "customer_request": "su solicitud",
    },
    "pt": {
        "lost": "perda",
        "stolen": "roubo",
        "unrecognized_charge": "uma cobrança que você não reconhece",
        "customer_request": "seu pedido",
    },
}
SERVICES = {
    "es": {
        "replacement": "la reposición de una tarjeta",
        "pin": "un cambio de PIN",
        "limit_increase": "un aumento de límite",
        "other_card_service": "ese trámite de su tarjeta",
    },
    "pt": {
        "replacement": "a reposição de um cartão",
        "pin": "uma alteração de PIN",
        "limit_increase": "um aumento de limite",
        "other_card_service": "esse serviço do seu cartão",
    },
}
REQUESTS = {
    "es": {
        "card_status": "el estado de su tarjeta",
        "available_credit": "su crédito disponible",
        "recent_transactions": "sus movimientos recientes",
        "decline_reason": "el rechazo de su tarjeta",
        "block_card": "el bloqueo de su tarjeta",
        "unrecognized_charge": "el cargo que no reconoce",
        "unsupported": "su otra solicitud",
        "talk_to_human": "pasarle con una persona del banco",
    },
    "pt": {
        "card_status": "o status do seu cartão",
        "available_credit": "seu crédito disponível",
        "recent_transactions": "suas transações recentes",
        "decline_reason": "a recusa do seu cartão",
        "block_card": "o bloqueio do seu cartão",
        "unrecognized_charge": "a cobrança que você não reconhece",
        "unsupported": "seu outro pedido",
        "talk_to_human": "encaminhar você para uma pessoa do banco",
    },
}
AND = {"es": "y", "pt": "e"}
PLACEHOLDER = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)?)\}")
# What comes before a placeholder that opens a sentence or a line.
OPENING = re.compile(r"(?:\A|\n|[.!?] )[ ¿¡]*\Z")


def joined(items: list[str], language: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {AND[language]} {items[-1]}"


def values(language: str, facts: Mapping[str, Any]) -> dict[str, str]:
    """
    Each placeholder's text, from the facts a node read: a card, a transaction, or a list of either, the credit, the
    window, and the date they are as of, formatted for the conversation's language and the customer's country.
    """
    country = facts.get("country", "")
    filled: dict[str, str] = {}
    if "card" in facts:
        card = facts["card"]
        filled["card"] = card_name(card, language)
        filled["card.status"] = STATUSES[language][card["product_status"]]
        if "expiration_date" in card:
            filled["card.expiration"] = expiration(card["expiration_date"], language)
    if "cards" in facts:
        filled["card_list"] = "\n".join(
            f"- {card_name(card, language).capitalize()}" for card in facts["cards"]
        )
    if "statuses" in facts:
        filled["cards"] = "\n".join(
            card_line(card, language) for card in facts["statuses"]
        )
    if "reason" in facts:
        filled["reason"] = REASONS[language][facts["reason"]]
    if "last_four" in facts:
        filled["last_four"] = facts["last_four"]
    if "reference" in facts:
        filled["reference"] = facts["reference"]
    if "transaction" in facts:
        transaction = facts["transaction"]
        filled["transaction"] = f"- {transaction_name(transaction, language, country)}"
        if "transaction_status" in transaction:
            filled["transaction.status"] = TRANSACTION_STATUSES[language][
                transaction["transaction_status"]
            ]
        if transaction.get("response_meaning") is not None:
            filled["transaction.meaning"] = reason_line(
                transaction["response_meaning"], language
            )
    if "transactions" in facts:
        filled["transactions"] = "\n".join(
            f"- {transaction_name(t, language, country)}" for t in facts["transactions"]
        )
    if "page" in facts:
        filled["transactions"] = "\n".join(
            transaction_line(t, language, country) for t in facts["page"]
        )
    if "credit" in facts:
        credit = facts["credit"]
        if "available_credit" in credit:
            filled["credit.available"] = amount(
                credit["available_credit"], credit["currency"], country
            )
            filled["credit.over_by"] = amount(
                credit["over_limit_by"], credit["currency"], country
            )
    if "credits" in facts:
        filled["credits"] = "\n".join(
            credit_line(c["card"], c["credit"], language, country)
            for c in facts["credits"]
        )
    if "as_of" in facts:
        filled["as_of"] = day(facts["as_of"])
    if "window" in facts:
        filled["window.from"] = moment(facts["window"]["from"])
        filled["window.to"] = moment(facts["window"]["to"])
    if "service" in facts:
        filled["service"] = SERVICES[language][facts["service"]]
    if "requests" in facts:
        filled["requests"] = joined(
            [REQUESTS[language][label] for label in facts["requests"]], language
        )
    return filled


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER.findall(text)


def fill(text: str, filled: dict[str, str]) -> str:
    """
    Each placeholder replaced by its value, with a capital where it opens a sentence or a line, as the model may write
    the card first (ADR-0004, Facts and outcomes in replies).
    """

    def value(match: re.Match[str]) -> str:
        found = filled[match.group(1)]
        opens = OPENING.search(text[: match.start()]) is not None
        return found[:1].upper() + found[1:] if opens else found

    return PLACEHOLDER.sub(value, text)


def render(name: str, language: str, facts: Mapping[str, Any]) -> str:
    return fill(FIXED[name][language], values(language, facts))
