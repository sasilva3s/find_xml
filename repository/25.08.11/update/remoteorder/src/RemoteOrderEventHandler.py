# -*- coding: utf-8 -*-

import json
import logging

from json import JSONEncoder
from threading import Lock, Thread
from xml.etree import ElementTree as eTree

from sysactions import (
    get_model,
    translate_message,
)

from application.manager import DeliveryEventsManager
from application.model import (
    DispatchedEvents,
    RemoteOrderStatus,
    RemoteOrderModelJsonEncoder,
    EventHandler,
    BusTokens,
    ListenedEvents,
    DeliveryIntegrationStatus,
)
from application.customexception import (
    OrderError,
    ValidationException,
    FiscalException,
    CompositionTreeException,
    ProductUnavailableException,
)
from application.repository import (
    CanceledOrderRepository,
    DeliveryEventsRepository,
    ProductRepository,
    OrderRepository,
    ProducedOrderRepository,
    ChatRepository,
)
from application.services import (
    RemoteOrderProcessor,
    RemoteOrderPickupTimeUpdater,
    StoreService,
    MenuBuilder,
    Printer,
    ProductionTimeManager,
    OrderService,
    RemoteOrderParser,
    RemoteOrderTaker,
    ChatManager,
)
from application.servicehandlers import (
    IntegrationService,
    ProductionService,
    CancellationService,
    ConfigurationService,
    LogisticService,
    ChatService,
)
from application.services import StoreStatusManager
from msgbus import (
    MBEasyContext,
    MBMessage,
    TK_SYS_ACK,
    TK_SYS_NAK,
    FM_STRING,
)

from application.services.dto import (
    UpdateStoreStatusRequest,
    ClosedPartner,
)
from helper import remove_accents
from typing import (
    List,
    Optional,
    Type,
    Dict,
)

concurrent_events_lock = Lock()
logger = logging.getLogger("RemoteOrder")


class RemoteOrderEventHandler(EventHandler):
    def __init__(
        self,
        pos_id,                                 # type: int
        mb_context,                             # type: MBEasyContext
        remote_order_processor,                 # type: RemoteOrderProcessor
        order_service,                          # type: OrderService
        store_service,                          # type: StoreService
        menu_builder,                           # type: MenuBuilder
        default_user_id,                        # type: int
        remote_order_pos_id,                    # type: int
        store_id,                               # type: str
        cancel_order_on_partner,                # type: bool
        store_status_manager,                   # type: StoreStatusManager
        auto_produce,                           # type: bool
        mandatory_logistic_for_integration,     # type: bool
        mandatory_logistic_for_production,      # type: bool
        canceled_order_repository,              # type: CanceledOrderRepository
        max_time_to_cancel_orders,              # type: int
        logistic_service,                       # type: LogisticService
        delivery_fee_part_code,                 # type: str
        delivery_event_repository,              # type: DeliveryEventsRepository
        delivery_events_manager,                # type: DeliveryEventsManager
        use_delivery_fee,                       # type: bool
        printer,                                # type: Printer
        coupon_after_production,                # type: bool
        receipt_after_production,               # type: bool
        api_product_repository,                 # type: ProductRepository
        remote_order_parser,                    # type: RemoteOrderParser
        order_repository,                       # type: OrderRepository
        produced_order_repository,              # type: ProducedOrderRepository
        remote_order_taker,                     # type: RemoteOrderTaker
        time_to_production_in_minutes,          # type: int
        chat_repository,                        # type: ChatRepository
        delivery_chat_active,                   # type: bool
        delivery_chat_messages_fetch_timeout,   # type: int
        time_to_send_pending_chat_messages,     # type: int
        interval_to_clean_message_config,       # type: int
    ):
        # type: (...) -> None # noqa

        super(RemoteOrderEventHandler, self).__init__(mb_context)

        self.pos_id = pos_id
        self.remote_order_processor = remote_order_processor
        self.order_service = order_service
        self.store_service = store_service
        self.menu_builder = menu_builder
        self.default_user_id = default_user_id
        self.remote_order_pos_id = remote_order_pos_id
        self.store_id = store_id
        self.handled_tokens = {}
        self.handled_events = self.get_handled_events()
        self.store_status_manager = store_status_manager
        self.event_dispatcher = DispatchedEvents(self.mbcontext)
        self.cancel_order_on_partner = cancel_order_on_partner
        self.auto_produce = auto_produce
        self.mandatory_logistic_for_integration = mandatory_logistic_for_integration
        self.mandatory_logistic_for_production = mandatory_logistic_for_production
        self.canceled_order_repository = canceled_order_repository
        self.max_time_to_cancel_orders = max_time_to_cancel_orders
        self.logistic_service = logistic_service
        self.delivery_fee_part_code = delivery_fee_part_code
        self.delivery_event_repository = delivery_event_repository
        self.delivery_events_manager = delivery_events_manager
        self.use_delivery_fee = use_delivery_fee
        self.printer = printer
        self.coupon_after_production = coupon_after_production
        self.receipt_after_production = receipt_after_production
        self.api_product_repository = api_product_repository
        self.remote_order_parser = remote_order_parser
        self.order_repository = order_repository
        self.produced_order_repository = produced_order_repository
        self.remote_order_taker = remote_order_taker
        self.time_to_production_in_minutes = time_to_production_in_minutes
        self.chat_repository = chat_repository
        self.delivery_chat_active = delivery_chat_active
        self.delivery_chat_messages_fetch_timeout = delivery_chat_messages_fetch_timeout
        self.time_to_send_pending_chat_messages = time_to_send_pending_chat_messages
        self.interval_to_clean_message_config = interval_to_clean_message_config
        self.chat_manager = ChatManager(
            event_dispatcher=self.event_dispatcher,
            chat_repository=self.chat_repository,
            time_to_send_pending_chat_messages=self.time_to_send_pending_chat_messages,
            interval_to_clean_message_config=self.interval_to_clean_message_config,
        )

        self.model = get_model(remote_order_pos_id)
        self.cancellation_service = self._create_cancellation_service()
        self.integration_service = self._create_integration_service()
        self.production_service = self._create_production_service()
        self.chat_service = self._create_chat_service()
        self.production_time_manager = self._create_production_time_manager()
        self.remote_order_pickup_time_updater = self._create_remote_order_pickup_time_updater()

        self.logistic_service.set_concurrent_events_lock(concurrent_events_lock)
        self.logistic_service.set_production_service(production_service=self.production_service)

        self.ignored_log_bus_tokens = [
            BusTokens.TK_REMOTE_ORDER_GET_STORE,
            BusTokens.TK_REMOTE_ORDER_GET_STORE_STATUS
        ]
        self.ignored_log_subjects = [ListenedEvents.UPDATE_PICKUP_TIME, ListenedEvents.PING]

        load_menu_thread = Thread(target=self.load_menu, name="Initial Menu Load Thread")
        load_menu_thread.daemon = True
        load_menu_thread.start()

    def load_menu(
        self,   # type: RemoteOrderEventHandler
    ):
        # type: (...) -> None
        logger.info("RemoteOrder is ready!")

        self.menu_builder.get_menu()

    def _create_cancellation_service(self):
        return CancellationService(
            model=self.model,
            order_service=self.order_service,
            canceled_order_repository=self.canceled_order_repository,
            max_time_to_cancel_orders=self.max_time_to_cancel_orders,
            event_dispatcher=self.event_dispatcher,
            concurrent_events_lock=concurrent_events_lock,
            logger=logger,
        )

    def _create_integration_service(self):
        return IntegrationService(
            self.model,
            self.store_service,
            self.remote_order_processor,
            self.order_service,
            self.cancellation_service,
            self.event_dispatcher,
            self.logistic_service,
            self.cancel_order_on_partner,
            self.mandatory_logistic_for_integration,
            concurrent_events_lock,
            logger
        )

    def _create_production_service(self):
        return ProductionService(
            mb_context=self.mbcontext,
            model=self.model,
            order_service=self.order_service,
            cancellation_service=self.cancellation_service,
            logistic_service=self.logistic_service,
            delivery_events_repository=self.delivery_event_repository,
            delivery_events_manager=self.delivery_events_manager,
            event_dispatcher=self.event_dispatcher,
            mandatory_logistic_for_production=self.mandatory_logistic_for_production,
            cancel_order_on_partner=self.cancel_order_on_partner,
            concurrent_events_lock=concurrent_events_lock,
            printer=self.printer,
            coupon_after_production=self.coupon_after_production,
            receipt_after_production=self.receipt_after_production,
            logger=logger,
        )

    def _create_chat_service(self):
        return ChatService(
            chat_repository=self.chat_repository,
            event_dispatcher=self.event_dispatcher,
            delivery_chat_active=self.delivery_chat_active,
            delivery_chat_messages_fetch_timeout=self.delivery_chat_messages_fetch_timeout,
        )

    def _create_production_time_manager(self):
        return ProductionTimeManager(
            time_to_production_in_minutes=self.time_to_production_in_minutes,
            production_service=self.production_service,
            integration_service=self.integration_service,
        )

    def _create_remote_order_pickup_time_updater(self):
        remote_order_pickup_time_updater = RemoteOrderPickupTimeUpdater(
            mbcontext=self.mbcontext,
            pos_id=self.pos_id,
            order_repository=self.order_repository,
            order_service=self.order_service,
            remote_order_parser=self.remote_order_parser,
            production_time_manager=self.production_time_manager,
            automatic_produce_orders=self.auto_produce,
            production_service=self.production_service
        )
        remote_order_pickup_time_updater.start_thread()

        return remote_order_pickup_time_updater

    def get_handled_tokens(self):
        # type: () -> List[int]

        tokens = vars(BusTokens).iteritems()
        # noinspection PyTypeChecker
        for token in tokens:
            name, value = token
            if name.startswith("TK_REMOTE_ORDER"):
                self.handled_tokens[value] = name

        return self.handled_tokens.keys()

    def get_handled_events(self):
        # type: () -> List[int]

        self.handled_events = []
        events = vars(ListenedEvents).iteritems()
        # noinspection PyTypeChecker
        for event in events:
            name, value = event
            self.handled_events.append(value)

        return self.handled_events

    def handle_message(self, msg):
        # type: (MBMessage) -> None
        received_token = msg.token
        try:
            if msg.token not in self.handled_tokens:
                logger.info("Not handled token received: [{}]".format(received_token))
                return

            if msg.token not in self.ignored_log_bus_tokens:
                logger.info("New message received: [{}] Data: {}".format(self.handled_tokens[received_token], msg.data))

            if msg.token == BusTokens.TK_REMOTE_ORDER_CONCLUDE_OFFLINE_ORDER:
                data = json.loads(msg.data)
                order_id = data["orderId"]
                print_coupons = data["printCoupons"]

                self.production_service.conclude_offline_order(
                    order_id=order_id,
                    print_coupons=print_coupons,
                )
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                )

            elif msg.token == BusTokens.TK_REMOTE_ORDER_RECEIVE_OFFLINE_ORDER:
                order_id = json.loads(msg.data)["order_id"]
                self._handle_receive_offline_order(order_id=order_id)

                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(message=msg)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_POS_ID:
                data = str(self.remote_order_pos_id)
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=data)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_SEND_ORDER_TO_PRODUCTION:
                self._handle_produce_remote_order(msg)

                order_id = msg.data
                self.integration_service.process_delivery_order_automatic_logistic(
                    order_id=order_id,
                )

                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING,)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_CHECK_IF_ORDER_EXISTS:
                self._check_if_order_exists(msg)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_VOID_REMOTE_ORDER:
                data = ""
                try:
                    msg.token = TK_SYS_ACK
                    remote_order_id = json.loads(msg.data)[0]
                    void_reason = json.loads(msg.data)[1]
                    self.cancellation_service.trigger_manual_cancel_event(
                        remote_order_id=remote_order_id,
                        void_reason=void_reason,
                    )
                except BaseException as ex:
                    msg.token = TK_SYS_NAK
                    data = ex.message
                    raise
                finally:
                    self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=data)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_STORE:
                store = self.store_service.get_store()
                store_json = self._get_encoded_json(store)
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=store_json)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_UPDATE_STORE_STATUS:
                try:
                    request = self._build_update_store_status_request(json.loads(msg.data))
                    store = self.store_service.update_store_status(request=request)
                    store_json = self._get_encoded_json(store)
                    msg.token = TK_SYS_ACK
                    self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=store_json)
                except ValidationException:
                    msg.token = BusTokens.TK_REMOTE_ORDER_ERROR
                    self.mbcontext.MB_EasyReplyMessage(msg)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_STORE_STATUS:
                response = dict()
                response.update(self.store_status_manager.get_store_status())
                response.update(self.store_service.get_store_status())
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    msg,
                    format=FM_STRING,
                    data=json.dumps(response, cls=RemoteOrderModelJsonEncoder)
                )

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_LOGISTIC_PARTNERS:
                msg.token = TK_SYS_ACK
                logistic_partners_json = self.logistic_service.get_logistic_partners_json()
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=logistic_partners_json)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_SEARCH_LOGISTIC:
                data = json.loads(msg.data).split("\0")
                partner_id = data[0]
                order_id = data[1]
                self.logistic_service.set_order_to_search_logistic(order_id, partner_id)
                if partner_id == '0' and len(data) >= 3:
                    deliveryman_data = data[2]
                    self.logistic_service.set_deliveryman_data(order_id, deliveryman_data)

                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_SEND_ORDER_TO_LOGISTIC:
                data = msg.data.split("\0")
                order_id = data[1]
                self.logistic_service.logistic_send(order_id)

                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_CANCEL_LOGISTIC:
                data = msg.data.split("\0")
                order_id = data[1]
                self.logistic_service.cancel_logistic(order_id)

                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_DELIVERY_FEE_CODE:
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=self.delivery_fee_part_code)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_CONFIRM_DELIVERY_PAYMENT:
                order_id = msg.data
                self.logistic_service.confirm_logistic_delivered(order_id)
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_CHECK_IF_DELIVERY_FEE_IS_ENABLED:
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=str(self.use_delivery_fee))

            elif msg.token == BusTokens.TK_REMOTE_ORDER_INFORM_ORDER_READY:
                msg.token = TK_SYS_ACK
                order_id = int(msg.data.decode("utf-8"))
                self.production_service.order_produced_from_pos(order_id)
                self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=str(self.use_delivery_fee))

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_DELIVERY_BRANDS:
                msg.token = TK_SYS_ACK
                configuration_service = ConfigurationService()
                delivery_brands_json = json.dumps(
                    configuration_service.get_delivery_brands(),
                    default=lambda x: x.__dict__
                )
                self.mbcontext.MB_ReplyMessage(
                    msg,
                    format=FM_STRING,
                    data=delivery_brands_json
                )
            elif msg.token == BusTokens.TK_REMOTE_ORDER_DELIVERY_ACTIONS:
                self._execute_delivery_action(msg=msg)

            elif msg.token == BusTokens.TK_REMOTE_ORDER_CREATE_ORDER_INDOOR:
                order_id = self._handle_create_order_indoor(msg=msg)
                response = {
                    "orderId": order_id,
                }

                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                    data=json.dumps(response, encoding="utf-8"),
                )

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_DELIVERY_CHAT_CONFIGS:
                delivery_chat_config = self.chat_service.get_delivery_chat_config()
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                    data=json.dumps(delivery_chat_config, encoding="utf-8"),
                )

            elif msg.token == BusTokens.TK_REMOTE_ORDER_GET_DELIVERY_CHAT_MESSAGES:
                all_messages_data = self.chat_service.get_all_messages()
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                    data=json.dumps(all_messages_data, encoding="utf-8"),
                )

            elif msg.token == BusTokens.TK_REMOTE_ORDER_MARK_DELIVERY_CHAT_MESSAGES_AS_READ:
                self.chat_service.update_messages_as_read()
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                )

            elif msg.token == BusTokens.TK_REMOTE_ORDER_SAVE_POS_MESSAGE:
                data = json.loads(msg.data)
                self.chat_service.save_pos_messages(messages_data=data)
                self.chat_manager.dispatch_pos_message_thread.wake_up()
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                )

        except (Exception,) as ex:
            logger.exception("Error handling remote order token: [{}]".format(received_token))
            msg.token = TK_SYS_NAK
            data = self._format_exception(ex)
            self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=data)
        finally:
            if received_token not in self.ignored_log_bus_tokens:
                logger.info("Finishing message: [{}]".format(self.handled_tokens[received_token]))

            if msg.token == BusTokens.TK_REMOTE_ORDER_DELIVERY_ACTIONS:
                msg.token = TK_SYS_ACK
                self.mbcontext.MB_ReplyMessage(
                    message=msg,
                    format=FM_STRING,
                )

    def _execute_delivery_action(
        self,   # type: RemoteOrderEventHandler
        msg,    # type: MBMessage
    ):

        request = json.loads(msg.data)
        subject = request["subject"].encode("utf-8")
        data = request["data"].encode("utf-8")

        try:
            if subject not in self.handled_events:
                logger.info("Not handled subject received: [{}]".format(subject))
                return

            if subject not in self.ignored_log_subjects:
                logger.info("New event received: [{}] Data: {}".format(subject, data))

            if subject == ListenedEvents.PING:
                self.store_status_manager.ping_received(data)
            else:
                self.store_status_manager.notify_external_contact_received()

            integration_events = [ListenedEvents.LOGISTIC_ORDER_CONFIRM, ListenedEvents.LOGISTIC_ORDER_CONFIRM_OFFLINE]
            if subject in integration_events:
                self._handle_order_confirm(data)

            elif subject == ListenedEvents.UPDATE_PICKUP_TIME:
                try:
                    self.remote_order_pickup_time_updater.update_pickup_time()
                except OrderError as ex:
                    order_error_json = self._get_encoded_json(ex)
                    self.event_dispatcher.send_event(DispatchedEvents.POS_ERROR_IN_PICKUP_ORDER, "", order_error_json)
                except Exception as ex:
                    order_error_json = self._get_encoded_json(OrderError(None, 99, repr(ex).encode("utf-8")))
                    self.event_dispatcher.send_event(DispatchedEvents.POS_ERROR_IN_PICKUP_ORDER, "", order_error_json)
                    raise

            elif subject == ListenedEvents.LOGISTIC_PICKUP_TIME_UPDATED:
                try:
                    with concurrent_events_lock:
                        self.remote_order_pickup_time_updater.pickup_time_updated(data)
                        self.event_dispatcher.send_event(DispatchedEvents.POS_PICKUP_TIME_UPDATED, "", data)

                except OrderError as ex:
                    order_error_json = self._get_encoded_json(ex)
                    self.event_dispatcher.send_event(DispatchedEvents.POS_ERROR_IN_PICKUP_ORDER, "", order_error_json)

                except BaseException as ex:
                    order_error_json = self._get_encoded_json(OrderError(None, 99, repr(ex).encode("utf-8")))
                    self.event_dispatcher.send_event(DispatchedEvents.POS_ERROR_IN_PICKUP_ORDER, "", order_error_json)
                    raise

            elif subject == ListenedEvents.SAC_STORE_STATUS_UPDATE_ACK:
                self.store_service.mark_status_sent(data)

            elif subject == ListenedEvents.SAC_ORDER_CANCEL:
                self.cancellation_service.cancel_order(data)

            elif subject == ListenedEvents.POS_ORDER_CONFIRM_ACK:
                json_data = json.loads(data)
                local_order_id = json_data.get("localOrderId")
                self.order_service.set_order_custom_property(
                    local_order_id,
                    "DELIVERY_INTEGRATION_STATUS",
                    DeliveryIntegrationStatus.CONFIRMED,
                )

            elif subject == ListenedEvents.POS_ORDER_PRODUCED_ACK:
                self.order_service.confirm_produced_order(data)

            elif subject == ListenedEvents.LOGISTIC_SEARCHING:
                json_data = json.loads(data)
                remote_order_id = json_data.get("orderId")
                logistic_id = json_data.get("logisticId")
                self.logistic_service.set_logistic_searching_status(remote_order_id, logistic_id)

            elif subject == ListenedEvents.LOGISTIC_FOUND:
                json_data = json.loads(data)
                logistic_id = json_data.get("logisticId")
                remote_order_id = json_data.get("orderId")
                adapter_logistic_id = json_data.get("adapterLogisticId")
                eta = json_data.get("eta")
                delivery_fee = json_data.get("deliveryFee")

                self.logistic_service.logistic_found(
                    logistic_id,
                    remote_order_id,
                    adapter_logistic_id,
                    eta,
                    delivery_fee,
                )

            elif subject == ListenedEvents.POS_LOGISTIC_CONFIRM_ACK:
                json_data = json.loads(data)
                logistic_id = json_data.get("logisticId")
                eta = json_data.get("eta")
                delivery_fee = json_data.get("deliveryFee")
                delivery_person = json_data.get("deliveryPerson")
                self.logistic_service.logistic_confirm(logistic_id, eta, delivery_fee, delivery_person)

            elif subject == ListenedEvents.LOGISTIC_NOT_FOUND:
                self.logistic_service.logistic_not_found(data)

            elif subject == ListenedEvents.LOGISTIC_CANCELED or subject == ListenedEvents.POS_LOGISTIC_CONFIRM_ERROR:
                json_data = json.loads(data)
                logistic_id = json_data.get("logisticId")
                formatted_message = json_data.get("formattedMessage")
                rejection_info = json_data.get("rejectionInfo")
                if rejection_info is not None:
                    rejection_info = json.dumps(rejection_info, encoding="utf-8")
                self.logistic_service.logistic_canceled(
                    logistic_id=logistic_id,
                    formatted_message=formatted_message,
                    rejection_info=rejection_info
                )

            elif subject == ListenedEvents.LOGISTIC_FINISHED:
                json_data = json.loads(data)
                logistic_id = json_data.get("logisticId")
                self.logistic_service.logistic_finished(logistic_id, data)

            elif subject == ListenedEvents.KDS_ORDER_PRODUCED:
                production_order_xml = eTree.XML(data)
                self.production_service.order_produced(production_order_xml)

            elif subject == ListenedEvents.POS_ORDER_READY_TO_DELIVERY_ACK:
                json_data = json.loads(data)
                remote_order_id = json_data.get("orderId")
                self.production_service.order_ready_to_delivery_ack(remote_order_id)

            elif subject == ListenedEvents.ORDER_LOGISTIC_DISPATCHED:
                self.logistic_service.logistic_send(None, data=data)

            elif subject == ListenedEvents.ORDER_LOGISTIC_DELIVERED:
                self.logistic_service.order_logistic_delivered(data)

            elif subject == ListenedEvents.POS_LOGISTIC_DISPATCHED_ACK:
                self.logistic_service.logistic_dispatched_ack(data)

            elif subject == ListenedEvents.POS_LOGISTIC_DELIVERED_ACK:
                self.logistic_service.logistic_delivered_ack(data)

            elif subject == ListenedEvents.LOGISTIC_EVENT:
                logger.info("Receiving LogisticEvent: {}".format(data))
                json_data = json.loads(data)
                logistic_id = json_data["logisticId"]
                eta = json_data.get("eta")
                status = json_data.get("status")
                if eta is not None:
                    self.logistic_service.update_eta(logistic_id, eta, status)

            elif subject == ListenedEvents.LOGISTIC_CANCELED_BY_PARTNER:
                json_data = json.loads(data)
                logistic_id = json_data["logisticId"]
                self.logistic_service.logistic_canceled_by_partner(logistic_id)

            elif subject == ListenedEvents.SAC_CHAT_MESSAGE:
                json_data = json.loads(data)
                self.chat_service.receive_messages(messages_data=json_data)

            elif subject == ListenedEvents.POS_CHAT_MESSAGE_ACK:
                json_data = json.loads(data)
                self.chat_service.receive_confirmation_of_sending_the_pos_message(messages_data=json_data)
        except (CompositionTreeException, ProductUnavailableException):
            pass
        except (Exception,):
            logger.exception("Error processing event [{0}]".format(subject))
        finally:
            if subject not in [ListenedEvents.UPDATE_PICKUP_TIME, ListenedEvents.PING]:
                logger.info("Finishing event: [{}]".format(subject))

    def _handle_create_order_indoor(
        self,  # type: RemoteOrderEventHandler
        msg,  # type: MBMessage
    ):
        # type: (...) -> Optional[int]

        order_id = self.integration_service.create_order_indoor(
            pos_id=self.pos_id,
            delivery_json=msg.data,
        )
        return order_id

    def handle_event(
        self,       # type: RemoteOrderEventHandler
        subject,    # type: str
        evt_type,   # type: str
        data,       # type: str
        msg,        # type: MBMessage
    ):
        # type: (...) -> None

        if subject == ListenedEvents.ORDER_MODIFIED and (evt_type == "PAID" or evt_type == "VOIDED"):
            order = eTree.XML(data).find("Order")
            order_id = order.get("orderId")
            order_delivered = self.order_service.get_order_custom_property(
                order_id=order_id,
                key="CONFIRM_DELIVERY_PAYMENT"
            )
            if order_delivered.upper() == 'TRUE':
                self.order_service.set_order_custom_property(
                    order_id=order_id,
                    key="REMOTE_ORDER_STATUS",
                    value=RemoteOrderStatus.CONCLUDED.value,
                )

    def terminate_event(self):
        self.store_service.terminate()

    def _handle_produce_remote_order(self, msg):
        remote_order_id = None
        try:
            order_id = int(msg.data.decode("utf-8"))
            order_already_fiscalized = self.order_service.get_order_custom_property(
                order_id=order_id,
                key="FISCAL_XML"
            )
            if order_already_fiscalized:
                return

            remote_order_id = self.order_service.get_remote_order_id_by_order_id(order_id=order_id)
            self.production_service.produce_order(order_id)
        except (Exception,):
            logger.exception("Error producing remote order id: {}".format(remote_order_id))
            raise

    def _handle_order_confirm(self, data):
        order_id = self.integration_service.create_order(
            pos_id=self.pos_id,
            delivery_json=data,
        )

        remote_order = self.order_service.get_order(order_id)

        is_offline_order = str(remote_order.custom_properties.get("OFFLINE_DELIVERY_ORDER", "false")).lower() == "true"
        if is_offline_order:
            delivered_order = remote_order.custom_properties.get("LOGISTIC_STATUS") == "delivered"
            if delivered_order:
                self.production_service.conclude_offline_order(
                    order_id=order_id,
                    print_coupons=False,
                )

            return

        if self.auto_produce and order_id:
            self.production_service.auto_produce_order(order_id)

            logistic_status = self.logistic_service.get_logistic_status(order_id)
            self.logistic_service.save_logistic_status(order_id, logistic_status)

    def _handle_receive_offline_order(
        self,       # type: RemoteOrderEventHandler
        order_id,   # type: int
    ):
        # type: (...) -> None

        self.order_service.receive_offline_order(order_id=order_id)
        if self.auto_produce:
            self.production_service.auto_produce_order(order_id=order_id)

            logistic_status = self.logistic_service.get_logistic_status(order_id=str(order_id))
            self.logistic_service.save_logistic_status(
                order_id=order_id,
                logistic_status=logistic_status,
            )

    @staticmethod
    def _get_data_info(data):
        remote_order_id = None
        originator = None
        try:
            parsed_json = json.loads(data)
            if "id" in parsed_json:
                remote_order_id = parsed_json["id"]
            if "custom_params" in parsed_json and "ORIGINATOR" in parsed_json["custom_params"]:
                originator = parsed_json["custom_params"]["ORIGINATOR"]
        except ValueError:
            pass

        return remote_order_id, originator

    @staticmethod
    def _build_update_store_status_request(
        store_data,     # type: Dict
    ):
        # type: (...) -> UpdateStoreStatusRequest

        global_status = store_data.get("globalStoreOpened")
        user_id = store_data["userId"]
        if global_status is not None:
            return UpdateStoreStatusRequest(
                global_store_opened=global_status,
                user_id=user_id,
            )

        closed_partners = []
        for partner in store_data["closedPartners"]:
            partner_name = partner["partnerName"].upper()
            brand = partner.get("brandName")
            closed_partners.append(
                ClosedPartner(
                    name=partner_name,
                    brand_name=brand,
                )
            )

        return UpdateStoreStatusRequest(
            closed_partners=closed_partners,
            user_id=user_id,
        )

    def _check_if_order_exists(self, msg):
        data = ""
        try:
            with concurrent_events_lock:
                data = self.remote_order_processor.check_if_order_exists(json.loads(msg.data.decode("utf-8")))
                data = str(data)
            msg.token = TK_SYS_ACK
        except BaseException as ex:
            msg.token = TK_SYS_NAK
            data = self._format_exception(ex)
            raise
        finally:
            self.mbcontext.MB_ReplyMessage(msg, format=FM_STRING, data=data)

    def _format_exception(self, exception):
        exception_type = type(exception).__name__
        formatted_message = remove_accents(text=exception.message)

        if formatted_message.startswith("$"):
            params = formatted_message.split("|", 1)[1] if "|" in formatted_message else ""
            message = formatted_message.split("|", 1)[0] if params else formatted_message
            formatted_message = translate_message(
                self.model,
                message[1:],
                *(params.split("|")) if params else ()
            )

        message = "{}: {}".format(exception_type, formatted_message)
        if type(exception) is FiscalException:
            message = "{}".format(formatted_message)

        return message

    @staticmethod
    def _get_encoded_json(ex, cls=RemoteOrderModelJsonEncoder):
        # type: (object, Optional[Type[JSONEncoder]]) -> str

        return json.dumps(ex, encoding="utf-8", cls=cls)
