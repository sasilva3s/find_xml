import os
from logging import getLogger

import cfgtools
from RemoteOrderEventHandler import RemoteOrderEventHandler
from application.compositiontree import CompositionTreeBuilder, DbProductRepository
from application.manager._DeliveryEventsManager import DeliveryEventsManager
from application.model import (
    MessageHandler,
    ListenedEvents,
)
from application.repository import (
    OrderRepository,
    PriceRepository,
    ProductRepository,
    StoreRepository,
    CanceledOrderRepository,
    ProducedOrderRepository,
    LogisticRepository,
    DeliveryEventsRepository,
    ChatRepository,
)
from application.servicehandlers import LogisticService
from application.services import (
    RemoteOrderProcessor,
    RemoteOrderParser,
    RemoteOrderValidator,
    CompositionTreeValidator,
    MenuBuilder,
    ProcessedOrderBuilder,
    WarningEmitter,
    StoreService,
    RemoteOrderTaker,
    OrderTakerWrapper,
    RemoteOrderItemCreator,
    PriceService,
    OrderService,
    ItemsCreator,
    StoreStatusManager,
    Printer,
    RemoteOrderPriceEqualizer,
    LoyaltyService,
)
from application.util import read_sw_config
from helper import import_pydevd, config_logger
from pos_model import OrderParser
from application.manager import ProducedOrderManager
from msgbus import MBEasyContext
from pos_api import PosService
from mbcontextmessagehandler import MbContextMessageBus

LOADER_CFG = os.environ["LOADERCFG"]
SERVICE_NAME = "RemoteOrder"


def main():
    import_pydevd(LOADER_CFG, 9139)

    config_logger(LOADER_CFG, SERVICE_NAME)
    config_logger(LOADER_CFG, "ProducedOrdersThread", max_files=1)
    config_logger(LOADER_CFG, "CanceledOrdersThread", max_files=1)
    config_logger(LOADER_CFG, "StoreServiceThread", max_files=1)
    config_logger(LOADER_CFG, "StoreStatusThread", max_files=1)
    config_logger(LOADER_CFG, "LogisticsThreads", max_files=1)
    config_logger(
        loader_path=LOADER_CFG,
        log_name="DeliveryEventsManager",
        max_files=1,
    )

    mb_context = MBEasyContext(SERVICE_NAME)

    config = cfgtools.read(LOADER_CFG)
    pos_id = int(config.find_value("RemoteOrder.PosId"))
    time_to_production_in_minutes = int(config.find_value("RemoteOrder.TimeToProduction") or 20)
    store_status_retry_sync_time = int(config.find_value("RemoteOrder.StoreStatusSyncTime"))
    delivery_user_id = config.find_value("RemoteOrder.DeliveryUserId")
    price_list_order = config.find_values("RemoteOrder.DeliveryPriceListOrder")
    validate_delivery_price = (config.find_value("RemoteOrder.ValidateDeliveryPrice") or "false").lower() == "true"
    validate_delivery_value = float(config.find_value("RemoteOrder.ValidateDeliveryPriceRange") or 0)
    order_error_wait_time = int(config.find_value("RemoteOrder.OrderErrorSyncInterval") or 0)
    events_sync_interval = int(config.find_value("RemoteOrder.EventsSyncInterval") or 1)
    cancel_order_on_partner = (config.find_value("RemoteOrder.CancelOrderOnPartner") or "false").lower() == "true"
    printer_max_retries = int(config.find_value("RemoteOrder.PrinterMaxRetries") or 3)
    printer_retry_time = int(config.find_value("RemoteOrder.PrinterRetryTime") or 3)
    store_status_configurations = config.find_group("RemoteOrder.StoreStatusManager")
    auto_produce = (config.find_value("RemoteOrder.AutoProduce") or "false").lower() == "true"
    discount_partner_method = (config.find_value("RemoteOrder.MethodRegisterPartnerDiscount") or "payment").lower()

    mandatory_logistic_integration_config_key = "RemoteOrder.Logistic.MandatoryLogisticForIntegration"
    mandatory_logistic_integration_config = config.find_value(mandatory_logistic_integration_config_key) or "false"
    mandatory_logistic_for_integration = mandatory_logistic_integration_config.lower() == "true"

    mandatory_logistic_production_key = "RemoteOrder.Logistic.MandatoryLogisticForProduction"
    mandatory_logistic_production_config = config.find_value(mandatory_logistic_production_key) or "false"
    mandatory_logistic_for_production = mandatory_logistic_production_config.lower() == "true"

    coupon_after_production = (config.find_value("RemoteOrder.CouponAfterProduction") or "false").lower() == "true"
    receipt_after_production = (config.find_value("RemoteOrder.ReceiptAfterProduction") or "false").lower() == "true"

    delivery_chat_active_config_key = "RemoteOrder.DeliveryChatConfig.DeliveryChatActive"
    delivery_chat_active = (config.find_value(delivery_chat_active_config_key) or "false").lower() == "true"

    time_to_send_pending_chat_messages_key = (
        "RemoteOrder.DeliveryChatConfig.TimeToSendPendingChatMessages"
    )
    time_to_send_pending_chat_messages = (
        int(config.find_value(time_to_send_pending_chat_messages_key) or 60)
    )
    interval_to_clean_message_key = "RemoteOrder.DeliveryChatConfig.ClearOldMessagesThreadRunTime"
    interval_to_clean_message_config = (
        int(config.find_value(interval_to_clean_message_key) or 240)
    )

    delivery_chat_messages_fetch_timeout_config_key = "RemoteOrder.DeliveryChatConfig.FetchDeliveryChatTimeout"
    delivery_chat_messages_fetch_timeout = int(config.find_value(delivery_chat_messages_fetch_timeout_config_key) or 10)

    max_time_to_cancel_orders = int(config.find_value("RemoteOrder.MaxTimeToCancelOrders") or 2)
    use_delivery_fee = (config.find_value("RemoteOrder.UseDeliveryFee") or "false").lower() == "true"
    delivery_fee_part_code = config.find_value("RemoteOrder.DeliveryFeePartCode") or "1000000002"
    delivery_fee_product_price = (config.find_value("RemoteOrder.DeliveryFeeProductPrice") or "false").lower() == "true"
    sell_with_partner_price = (config.find_value("RemoteOrder.SellWithPartnerPrice") or "false").lower() == "true"
    partners_configuration = config.find_group("RemoteOrder.Partner")

    required_services = "Persistence|PosController|DeliveryPersistence|StoreWideConfig|ORDERMGR{}".format(pos_id)
    message_handler = MessageHandler(mb_context, SERVICE_NAME, SERVICE_NAME, required_services, None)

    store_id = read_sw_config(mb_context, "Store.Id")
    print_app_coupon = (read_sw_config(mb_context, "Store.PrintAppCoupon") or "false").lower() == "true"

    printer = Printer(
        mb_context=mb_context,
        pos_id=pos_id,
        printer_max_retries=printer_max_retries,
        printer_retry_time=printer_retry_time,
    )

    api_product_repository = ProductRepository(mb_context)

    product_repository = DbProductRepository(mb_context)
    remote_order_parser = RemoteOrderParser(
        product_repository=api_product_repository,
        use_delivery_fee=use_delivery_fee,
        external_logistic_partners=list(
            map(
                lambda p: p.lower(),
                config.find_values("RemoteOrder.Logistic.ExternalLogisticPartners") or []
            )
        ),
    )
    composition_tree_builder = CompositionTreeBuilder(product_repository)
    composition_tree_validator = CompositionTreeValidator(pos_id, api_product_repository)

    price_repository = PriceRepository(mb_context)
    price_service = PriceService(price_repository, price_list_order)

    warning_emitter = WarningEmitter(mb_context)
    part_code_to_skip = None
    if use_delivery_fee:
        part_code_to_skip = delivery_fee_part_code

    store_repository = StoreRepository(mb_context)
    store_service = StoreService(
        mb_context=mb_context,
        store_repository=store_repository,
        retry_sync_time=store_status_retry_sync_time,
        store_id=store_id,
        partners_configuration=partners_configuration,
    )

    order_repository = OrderRepository(mb_context)

    pos_service = PosService(
        pos_id=pos_id,
        message_bus=MbContextMessageBus(mbcontext=mb_context),
    )

    loyalty_service = LoyaltyService(
        pos_id=pos_id,
        message_bus=MbContextMessageBus(mbcontext=mb_context),
    )

    order_taker_wrapper = OrderTakerWrapper(
        mb_context=mb_context,
        product_repository=api_product_repository,
        delivery_user_id=delivery_user_id,
        price_list_id=".".join(price_list_order),
        print_app_coupon=print_app_coupon,
        printer=printer,
        pos_service=pos_service,
        loyalty_service=loyalty_service,
        discount_partner_method=discount_partner_method
    )

    order_item_creator = RemoteOrderItemCreator(order_repository, price_service, order_taker_wrapper, pos_id)

    remote_order_taker = RemoteOrderTaker(
        pos_id=pos_id,
        order_taker_wrapper=order_taker_wrapper,
        order_item_creator=order_item_creator,
        order_repository=order_repository,
        delivery_fee_part_code=delivery_fee_part_code,
        use_delivery_fee=use_delivery_fee,
        delivery_fee_product_price=delivery_fee_product_price,
        discount_partner_method=discount_partner_method,
    )

    store_status_manager = StoreStatusManager(mb_context, store_status_configurations, store_repository)
    store_status_manager.start()

    canceled_order_repository = CanceledOrderRepository(mb_context, pos_id, order_error_wait_time,
                                                        cancel_order_on_partner)
    produced_order_repository = ProducedOrderRepository(pos_id, mb_context)
    items_creator = ItemsCreator(api_product_repository, product_repository)

    cancel_paid_orders = (config.find_value("RemoteOrder.CancelPaidOrders") or "false").lower() == "true"
    order_service = OrderService(
        items_creator=items_creator,
        remote_order_taker=remote_order_taker,
        canceled_order_repository=canceled_order_repository,
        produced_order_repository=produced_order_repository,
        order_parser=OrderParser(),
        pos_id=pos_id,
        cancel_paid_orders=cancel_paid_orders,
        mb_context=mb_context,
    )

    remote_order_price_equalizer = RemoteOrderPriceEqualizer(
        pos_id=pos_id,
        order_taker_wrapper=order_taker_wrapper,
        skipped_part_code=part_code_to_skip,
    )
    remote_order_validator = RemoteOrderValidator(
        composition_tree_builder=composition_tree_builder,
        composition_validator=composition_tree_validator,
        warning_emitter=warning_emitter,
        validate_delivery_price=validate_delivery_price,
        validate_delivery_value=validate_delivery_value,
        remote_order_price_equalizer=remote_order_price_equalizer,
        sell_with_partner_price=sell_with_partner_price,
        items_creator=items_creator,
    )

    processed_order_builder = ProcessedOrderBuilder(api_product_repository, order_service)
    remote_order_processor = RemoteOrderProcessor(remote_order_parser, remote_order_validator, remote_order_taker,
                                                  store_service, processed_order_builder, cancel_order_on_partner,
                                                  order_service)

    ProducedOrderManager(pos_id, mb_context, order_error_wait_time, produced_order_repository, processed_order_builder)

    menu_builder = MenuBuilder(composition_tree_builder, price_service, product_repository)

    delivery_events_repository = DeliveryEventsRepository(mb_context, pos_id, produced_order_repository)
    delivery_events_manager = DeliveryEventsManager(
        pos_id,
        mb_context,
        events_sync_interval,
        delivery_events_repository
    )

    logistic_repository = LogisticRepository(
        mb_context,
        pos_id,
        order_service,
        remote_order_processor,
        None,
        config,
        store_service,
    )
    logistic_service = LogisticService(
        remote_order_taker=remote_order_taker,
        mb_context=mb_context,
        pos_id=pos_id,
        order_service=order_service,
        store_id=store_id,
        config=config,
        delivery_events_repository=delivery_events_repository,
        delivery_events_manager=delivery_events_manager,
        logistic_repository=logistic_repository,
    )
    logistic_repository.logistic_service = logistic_service
    logistic_repository.start_thread()

    chat_repository = ChatRepository(mb_context=mb_context)

    event_handler = RemoteOrderEventHandler(
        pos_id=pos_id,
        mb_context=mb_context,
        remote_order_processor=remote_order_processor,
        order_service=order_service,
        store_service=store_service,
        menu_builder=menu_builder,
        default_user_id=delivery_user_id,
        remote_order_pos_id=pos_id,
        store_id=store_id,
        cancel_order_on_partner=cancel_order_on_partner,
        store_status_manager=store_status_manager,
        auto_produce=auto_produce,
        mandatory_logistic_for_integration=mandatory_logistic_for_integration,
        mandatory_logistic_for_production=mandatory_logistic_for_production,
        canceled_order_repository=canceled_order_repository,
        max_time_to_cancel_orders=max_time_to_cancel_orders,
        logistic_service=logistic_service,
        delivery_fee_part_code=delivery_fee_part_code,
        delivery_event_repository=delivery_events_repository,
        delivery_events_manager=delivery_events_manager,
        use_delivery_fee=use_delivery_fee,
        printer=printer,
        coupon_after_production=coupon_after_production,
        receipt_after_production=receipt_after_production,
        api_product_repository=api_product_repository,
        remote_order_parser=remote_order_parser,
        order_repository=order_repository,
        produced_order_repository=produced_order_repository,
        remote_order_taker=remote_order_taker,
        time_to_production_in_minutes=time_to_production_in_minutes,
        chat_repository=chat_repository,
        delivery_chat_active=delivery_chat_active,
        delivery_chat_messages_fetch_timeout=delivery_chat_messages_fetch_timeout,
        time_to_send_pending_chat_messages=time_to_send_pending_chat_messages,
        interval_to_clean_message_config=interval_to_clean_message_config,
    )

    message_handler.set_event_handler(event_handler)
    message_handler.subscribe_non_reentrant_events([
        ListenedEvents.UPDATE_PICKUP_TIME,
        ListenedEvents.ORDER_MODIFIED
    ])

    message_handler.subscribe_reentrant_events([
        ListenedEvents.LOGISTIC_PICKUP_TIME_UPDATED,
        ListenedEvents.SAC_STORE_STATUS_UPDATE_ACK,
        ListenedEvents.SAC_ORDER_CANCEL,
    ])

    logger = getLogger(SERVICE_NAME)
    logger.info("Starting RemoteOrder...")

    message_handler.handle_events()
