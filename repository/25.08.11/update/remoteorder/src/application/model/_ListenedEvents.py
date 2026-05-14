# -*- coding: utf-8 -*-


class ListenedEvents(object):
    # Evento recebido quando uma Order deve ser guardada na loja
    LOGISTIC_ORDER_CONFIRM = "LogisticOrderConfirm"
    # Evento recebido quando uma Order (offline) deve ser guardada na loja
    LOGISTIC_ORDER_CONFIRM_OFFLINE = "LogisticOrderConfirmOffline"
    # Evento para o componente de logística para atualizar o tempo de entrega de uma order
    UPDATE_PICKUP_TIME = "UpdatePickupTime"
    # Evento com o novo tempo de entrega da uma Order
    LOGISTIC_PICKUP_TIME_UPDATED = "LogisticPickupTimeUpdated"
    # Evento recebido do servidor quando a atualização do status foi recebida com sucesso
    SAC_STORE_STATUS_UPDATE_ACK = "SacStoreStatusUpdateAck"
    # Evento indicando que o Sac cancelou um pedido e o mesmo precisa ser cancelado na loja
    SAC_ORDER_CANCEL = "SacOrderCancel"
    # Evento recebido quando o serviço remoto confirmou que um pedido foi integrado
    POS_ORDER_CONFIRM_ACK = "PosOrderConfirmAck"
    # Evento recebido quando o serviço remoto confirmou que um pedido foi produzido
    POS_ORDER_PRODUCED_ACK = "PosOrderProducedAck"
    # Evento de Ping recebido pelo servico remoto
    PING = "Ping"

    # Evento de recebido pelo serviço de logística informando que a busca foi iniciada
    LOGISTIC_SEARCHING = "LogisticSearching"
    # Evento de recebido pelo serviço de logística informando que a busca foi efetuada com sucesso
    LOGISTIC_FOUND = "LogisticFound"
    # Evento de recebido pelo serviço de logística informando que não foi encontrada
    LOGISTIC_NOT_FOUND = "LogisticNotFound"
    # Evento de recebido pelo serviço de logística informando que a logistica foi cancelada
    LOGISTIC_CANCELED = "LogisticCanceled"
    # Evento de recebido pelo serviço de logística informando que a logistica foi finalizadas
    LOGISTIC_FINISHED = "LogisticFinished"
    # Evento de recebido pelo serviço de logística informando que a logistica foi confirmada
    POS_LOGISTIC_CONFIRM_ACK = "PosLogisticConfirmAck"

    # Evento recebido quando o SAC envia uma nova mensagem a loja
    SAC_CHAT_MESSAGE = "SacChatMessage"
    # Evento recebido quando o SAC realiza a confirmação das mensagens enviadas pelo PDV
    POS_CHAT_MESSAGE_ACK = "PosChatMessageAck"

    # Evento de recebido pelo new-production informando que o pedido foi produzido
    KDS_ORDER_PRODUCED = "KdsOrderProduced"
    # Evento de recebido pelo serviço de delivery confirmando o recebimento que o pedido foi produzido
    POS_ORDER_READY_TO_DELIVERY_ACK = "PosOrderReadyToDeliveryAck"

    # Evento recebido pelo serviço de logística informando que o pedido saiu para entrega
    ORDER_LOGISTIC_DISPATCHED = "OrderLogisticDispatched"
    # Evento para confirmação que o servidor recebeu o evento do pedido que saiu para entrega
    POS_LOGISTIC_DISPATCHED_ACK = "PosLogisticDispatchedAck"
    # Evento para confirmação que o servidor recebeu o evento do pedido entregue
    POS_LOGISTIC_DELIVERED_ACK = "PosLogisticDeliveredAck"
    # Evento de recebido pelo serviço de logística informando que o pedido foi entregue
    ORDER_LOGISTIC_DELIVERED = "OrderLogisticDelivered"
    # Event received whenever there is an update of the delivery man status, position, eta
    LOGISTIC_EVENT = "LogisticEvent"
    # Evento para informar o PDV que a Logistica nao foi confirmada
    POS_LOGISTIC_CONFIRM_ERROR = "PosLogisticConfirmError"
    # Evento para informar o PDV que a Logistica nao foi confirmada
    LOGISTIC_CANCELED_BY_PARTNER = "LogisticCanceledByPartner"
    ORDER_MODIFIED = "ORDER_MODIFIED"
