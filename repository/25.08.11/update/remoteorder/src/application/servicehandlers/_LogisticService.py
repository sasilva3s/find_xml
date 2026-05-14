import json
import logging

import iso8601
import pytz
from application.apimodel import Order
from application.model.open_delivery import (
    LogisticRequest,
    Vehicle,
    VehicleType,
    ContainerSize,
    VehicleContainer,
    Address,
    TimeLimits,
    LogisticOrder,
    Payment,
    PaymentMethod,
    OfflinePaymentMethod,
    OfflinePayment,
    Price,
    Customer,
)
from order_api.dto import (
    CustomProperty,
    OrderState,
)
from typing import (
    Any,
    Dict,
    Optional,
)

from cfgtools import Configuration
from msgbus import MBEasyContext
from sysactions import get_model, translate_message

from application.customexception import (
    LogisticException,
    OrderNotFoundException,
)
from application.manager import DeliveryEventsManager
from application.model import (
    DispatchedEvents,
    RemoteOrderModelJsonEncoder,
    LogisticIntegrationStatus,
    LogisticCancelRequest,
    LogisticConfirmRequest,
    DeliveryConfirm,
    DeliveryEventTypes,
    LogisticPartner,
    RemoteOrderStatus,
    DeliveryIntegrationStatus,
)
from application.services import (
    OrderService,
    RemoteOrderTaker,
)

from datetime import datetime

logger = logging.getLogger("RemoteOrder")


class LogisticService(object):
    def __init__(
        self,
        remote_order_taker,             # type: RemoteOrderTaker
        mb_context,                     # type: MBEasyContext
        pos_id,                         # type: int
        order_service,                  # type: OrderService
        store_id,                       # type: str
        config,                         # type: Configuration
        delivery_events_repository,     # type: DeliveryEventsRepository
        delivery_events_manager,        # type: DeliveryEventsManager
        logistic_repository,            # type: LogisticRepository
    ):
        # type: (...) -> None

        self.order_service = order_service
        self.store_code = store_id

        self.event_dispatcher = DispatchedEvents(mb_context)
        self.has_own_driver = config.find_value("RemoteOrder.Logistic.HasOwnDriver").lower() == "true"
        self.logistic_partners = self._get_logistic_partners(pos_id, config)
        self.default_logistic_partner = self._get_default_logistic_partner(self.logistic_partners)
        self.delivery_events_repository = delivery_events_repository
        self.delivery_events_manager = delivery_events_manager
        self.logistic_repository = logistic_repository
        self.concurrent_events_lock = None
        self.model = get_model(pos_id)
        self.production_service = None
        self.remote_order_taker = remote_order_taker

    def set_production_service(
        self,                  # type: LogisticService
        production_service,    # type: production_service
    ):
        # type: (...) -> None

        self.production_service = production_service

    def set_concurrent_events_lock(self, concurrent_events_lock):
        self.concurrent_events_lock = concurrent_events_lock

    def request_logistic(
        self,
        order_id,       # type: int
        order=None,     # type: Order
    ):
        # type: (...) -> None

        if order is None:
            order = self.order_service.get_order(order_id)

        logistic_partner = self._get_logistic_partner(order)

        if logistic_partner is None:
            raise LogisticException("Could not find logistic partner for order {}".format(order_id))

        logistic_request = self._get_logistic_request(order, logistic_partner)
        data = json.dumps(logistic_request, encoding="utf-8", cls=RemoteOrderModelJsonEncoder)
        self.event_dispatcher.send_event(DispatchedEvents.LOGISTIC_REQUEST, "", data)
        logger.info("Requesting logistic for order {}".format(order_id))

        if self._get_logistic_status(order_id) != LogisticIntegrationStatus.SEARCHING:
            self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.SEARCHING)

    def cancel_logistic(
        self,
        order_id,   # type: int
    ):
        # type: (...) -> None

        order = self.order_service.get_order(order_id=order_id)
        logistic_cancel_request = self._get_cancel_request(order=order)

        current_integration_status = order.custom_properties.get("LOGISTIC_INTEGRATION_STATUS", None)
        waiting_logistic_cancel_response = LogisticIntegrationStatus.WAITING_LOGISTIC_CANCEL_RESPONSE
        if current_integration_status in [None, ""] or current_integration_status != waiting_logistic_cancel_response:
            self.set_integration_status_custom_property(
                order_id=order_id,
                status=LogisticIntegrationStatus.WAITING_LOGISTIC_CANCEL_RESPONSE,
            )

        if logistic_cancel_request:
            data = json.dumps(logistic_cancel_request, encoding="utf-8", cls=RemoteOrderModelJsonEncoder)
            self.event_dispatcher.send_event(
                subject=DispatchedEvents.LOGISTIC_CANCEL,
                evt_type="",
                data=data,
            )
            return

        self.logistic_canceled(
            logistic_id=None,
            order_id=order_id,
        )

    def set_logistic_searching_status(self, remote_order_id, logistic_id):
        # type: (str, str) -> None

        order_id = self.order_service.get_order_id_by_remote_order_id(remote_order_id=remote_order_id)
        found_status = [LogisticIntegrationStatus.CONFIRMED, LogisticIntegrationStatus.WAITING_CONFIRM_RESPONSE]
        if self._get_logistic_status(order_id) not in found_status:
            self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.WAITING_SEARCHING_RESPONSE)
            self._set_logistic_id_custom_property(order_id, logistic_id)

    def logistic_found(self, logistic_id, remote_order_id, adapter_logistic_id, eta, delivery_fee):
        order_id = self.order_service.get_order_id_by_remote_order_id(remote_order_id=remote_order_id)

        custom_properties = [
            CustomProperty(
                key="LOGISTIC_ID",
                value=logistic_id,
            ),
            CustomProperty(
                key="ADAPTER_LOGISTIC_ID",
                value=adapter_logistic_id,
            ),
            CustomProperty(
                key="LOGISTIC_ETA",
                value=json.dumps(eta),
            ),
            CustomProperty(
                key="LOGISTIC_DELIVERY_FEE",
                value=delivery_fee,
            ),
        ]
        self.order_service.set_order_custom_properties(
            order_id=order_id,
            custom_properties=custom_properties,
        )

        if self._get_logistic_status(order_id=order_id) == LogisticIntegrationStatus.SEARCHING:
            self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.WAITING_CONFIRM_RESPONSE)

    def logistic_confirm(
        self,               # type: LogisticService
        logistic_id,        # type: str
        eta,                # type: str
        delivery_fee,       # type: str
        delivery_person,    # type: str
    ):
        # type: (...) -> None

        order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)
        try:
            partner = self.order_service.get_order_custom_property(
                order_id=order_id,
                key="PARTNER",
            )

            if partner != 'MANUAL':
                self.production_service.produce_order(
                    order_id=order_id,
                    validate_logistic=False,
                )

            custom_properties = [
                CustomProperty(
                    key="LOGISTIC_ETA",
                    value=json.dumps(eta),
                ),
                CustomProperty(
                    key="LOGISTIC_DELIVERY_FEE",
                    value=delivery_fee,
                ),
                CustomProperty(
                    key="LOGISTIC_DELIVERY_PERSON",
                    value=json.dumps(delivery_person),
                ),
            ]
            self.order_service.set_order_custom_properties(
                order_id=order_id,
                custom_properties=custom_properties,
            )

            if self._get_logistic_status(order_id=order_id) == LogisticIntegrationStatus.WAITING_CONFIRM_RESPONSE:
                self.set_integration_status_custom_property(
                    order_id=order_id,
                    status=LogisticIntegrationStatus.CONFIRMED,
                )

        except Exception:
            logger.exception("Exception confirming logistic for logistic: {}".format(logistic_id))
            canceled_or_error = self._has_canceled_order_or_delivery_error(order_id=order_id)
            if canceled_or_error:
                self.cancel_logistic(order_id=order_id)

    def update_eta(self, logistic_id, eta, status):
        order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)

        custom_properties = [
            CustomProperty(
                key="LOGISTIC_ETA",
                value=json.dumps(eta),
            ),
            CustomProperty(
                key="LOGISTIC_DELIVERY_STEP",
                value=status,
            ),
            CustomProperty(
                key="WAITING_MANUAL_PRODUCTION_CONFIRM",
                value="clear",
            ),
        ]
        self.order_service.set_order_custom_properties(
            order_id=order_id,
            custom_properties=custom_properties,
        )

    def send_logistic_confirm(self, order_id):
        order = self.order_service.get_order(order_id)
        logistic_confirm_request = self._get_logistic_confirm_request(order)
        if logistic_confirm_request:
            data = json.dumps(logistic_confirm_request, encoding="utf-8", cls=RemoteOrderModelJsonEncoder)
            self.event_dispatcher.send_event(DispatchedEvents.POS_LOGISTIC_CONFIRM, "", data)

    def logistic_not_found(self, data):
        json_data = json.loads(data)
        remote_order_id = json_data.get("orderId")
        order_id = self.order_service.find_order_id_by_remote_order_id(remote_order_id=remote_order_id)
        if order_id:
            self.set_integration_status_custom_property(
                order_id=order_id,
                status=LogisticIntegrationStatus.NOT_FOUND,
            )
            self.delivery_events_repository.cancel_delivery_event(
                order_id=order_id,
            )

    def logistic_canceled(self, logistic_id, order_id=None, formatted_message=None, rejection_info=None):
        if not order_id:
            order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)

        custom_properties = [
            CustomProperty(
                key="LOGISTIC_REJECTION_INFO",
                value=rejection_info,
            ),
        ]

        if formatted_message is not None:
            custom_properties.append(
                CustomProperty(
                    key="LOGISTIC_CANCELED_FORMATTED_MESSAGE",
                    value=formatted_message.encode("utf-8"),
                ),
            )

        self.order_service.set_order_custom_properties(
            order_id=order_id,
            custom_properties=custom_properties,
        )

        self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.CANCELED)

    def logistic_canceled_by_partner(self, logistic_id):
        try:
            order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)
            self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.CANCELED)
        except LogisticException:
            pass

        self.event_dispatcher.send_event(
            DispatchedEvents.LOGISTIC_CANCELED_BY_PARTNER_ACK,
            "",
            json.dumps({"logisticId": logistic_id})
        )

    def logistic_send(self, order_id, data=None):
        if data is not None:
            json_data = json.loads(data)
            try:
                order_id = self.order_service.get_order_id_by_remote_order_id(remote_order_id=json_data.get("id"))
            except OrderNotFoundException:
                self.event_dispatcher.send_event(DispatchedEvents.ORDER_LOGISTIC_DISPATCHED_ACK, "", data)
                return

        self.order_service.set_order_custom_property(
            order_id=order_id,
            key="LOGISTIC_DISPATCHED_TIME",
            value=datetime.utcnow().isoformat(),
        )
        self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.SENT)
        if data is None:
            self.out_for_delivery_confirm(order_id)
            return

        if not self.delivery_events_repository.has_event(order_id, DeliveryEventTypes.LOGISTIC_DISPATCHED):
            self.insert_logistic_event(order_id, DeliveryEventTypes.LOGISTIC_DISPATCHED)

        self.event_dispatcher.send_event(DispatchedEvents.ORDER_LOGISTIC_DISPATCHED_ACK, "", data)

    def order_logistic_delivered(self, data):
        json_data = json.loads(data)
        remote_order_id = json_data.get("id")
        try:
            order_id = self.order_service.find_order_id_by_remote_order_id(remote_order_id=remote_order_id)
            if not order_id:
                message = "An order associated with the Remote Order Id {} does not exist".format(remote_order_id)
                raise LogisticException(message=message)

            self._set_confirm_delivery_payment_custom_property_with_lock(order_id=str(order_id))
            if not self.delivery_events_repository.has_event(order_id, DeliveryEventTypes.LOGISTIC_DELIVERED):
                self.insert_logistic_event(order_id, DeliveryEventTypes.LOGISTIC_DELIVERED)
        except LogisticException:
            pass
        finally:
            self.event_dispatcher.send_event(DispatchedEvents.ORDER_LOGISTIC_DELIVERED_ACK, "", data)

    def logistic_finished(self, logistic_id, data):
        order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)
        self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.FINISHED)
        self.event_dispatcher.send_event(DispatchedEvents.LOGISTIC_FINISHED_ACK, "", data)

    def out_for_delivery_confirm(self, order_id):
        try:
            self.insert_logistic_event(order_id, DeliveryEventTypes.LOGISTIC_DISPATCHED)
        except (Exception,):
            self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.CONFIRMED)
            raise

    def save_logistic_status(self, order_id, logistic_status):
        current_logistic_status = self.order_service.get_order_custom_property(
            order_id=order_id,
            key="LOGISTIC_INTEGRATION_STATUS",
        )
        if not current_logistic_status or current_logistic_status == LogisticIntegrationStatus.WAITING:
            self.update_logistic_integration_status(
                order_id=order_id,
                logistic_status=logistic_status,
            )

    def update_logistic_integration_status(
        self,
        order_id,
        logistic_status
    ):
        # type: (...) -> None

        pickup_type = self.order_service.get_order_custom_property(
            order_id=order_id,
            key="PICKUP_TYPE"
        )
        if pickup_type and pickup_type.lower() == "take_out":
            return

        self.order_service.set_order_custom_property(order_id, "LOGISTIC_INTEGRATION_STATUS", logistic_status)

    def get_logistic_status(
        self,           # type: LogisticService
        order_id,       # type: str
    ):
        # type: (...) -> LogisticIntegrationStatus

        order_already_fiscalized = self.order_service.get_order_custom_property(
            order_id=order_id,
            key="FISCAL_XML"
        )
        if self.has_default_partner() and order_already_fiscalized:
            self.set_default_partner_id_custom_property(order_id)
            if not self._order_needs_logistics(order_id=order_id):
                return LogisticIntegrationStatus.RECEIVED

            return LogisticIntegrationStatus.SEARCHING

        return LogisticIntegrationStatus.WAITING

    def confirm_logistic_delivered(self, order_id):
        self._set_confirm_delivery_payment_custom_property_with_lock(order_id=order_id)
        self.insert_logistic_event(order_id, DeliveryEventTypes.LOGISTIC_DELIVERED)

    def logistic_dispatched_ack(self, data):
        self.update_event_delivery(data, DeliveryEventTypes.LOGISTIC_DISPATCHED)

    def logistic_delivered_ack(self, data):
        json_data = json.loads(data)
        logistic_id = json_data.get("logisticId")
        if logistic_id is not None:
            order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)
            partner_id = int(self.order_service.get_order_custom_property(order_id, "LOGISTIC_PARTNER_ID"))
            if partner_id == 0:
                self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.FINISHED)
        self.update_event_delivery(data, DeliveryEventTypes.LOGISTIC_DELIVERED)

    def insert_logistic_event(self, order_id, event_type):
        order = self.order_service.get_order(order_id)
        logistic_id = self._get_logistic_id(order.id)
        if not logistic_id:
            return

        delivery_confirm_request = self._get_out_for_delivery_confirm_request(order, logistic_id)
        event_data = json.dumps(delivery_confirm_request, encoding="utf-8", cls=RemoteOrderModelJsonEncoder)
        self.delivery_events_repository.insert_delivery_event(order_id, str(order.remote_id), event_type, event_data)
        self.delivery_events_manager.send_now()

    def update_event_delivery(self, data, event_type):
        json_data = json.loads(data)
        logistic_id = json_data.get("logisticId")
        if logistic_id is not None:
            order_id = self.order_service.get_order_id_by_logistic_id(logistic_id=logistic_id)
        else:
            remote_order_id = json_data.get("orderId")
            order_id = self.order_service.find_order_id_by_remote_order_id(remote_order_id=remote_order_id)
            if not order_id:
                message = "An order associated with the Remote Order Id {} does not exist".format(remote_order_id)
                raise LogisticException(message=message)

        self.delivery_events_repository.set_event_server_ack(order_id, event_type)

    def has_default_partner(self):
        return self.default_logistic_partner is not None

    def set_order_to_search_logistic(self, order_id, partner_id):
        self.set_adapter_logistic_name_custom_property(order_id, partner_id)
        self.set_integration_status_custom_property(order_id, LogisticIntegrationStatus.NEED_SEARCH)

    def set_integration_status_custom_property(self, order_id, status):
        with self.concurrent_events_lock:
            logger.info("Changing LOGISTIC_INTEGRATION_STATUS for order {} to {}".format(str(order_id), status))
            self.update_logistic_integration_status(
                order_id=order_id,
                logistic_status=status,
            )
            self.logistic_repository.run_now()

    def set_default_partner_id_custom_property(self, order_id):
        self.order_service.set_order_custom_property(order_id, "LOGISTIC_PARTNER_ID", self.default_logistic_partner.id)

    def set_adapter_logistic_name_custom_property(self, order_id, partner_id):
        with self.concurrent_events_lock:
            self.order_service.set_order_custom_property(order_id, "LOGISTIC_PARTNER_ID", partner_id)

    def set_delivery_status_custom_property(self, order_id, name):
        with self.concurrent_events_lock:
            self.order_service.set_order_custom_property(order_id, "DELIVERY_INTEGRATION_STATUS", name)

    def get_logistic_partners_json(self):
        ret = []
        for partner_id in self.logistic_partners.keys():
            partner = self.logistic_partners[partner_id]
            ret.append({
                "id": partner.id,
                "name": partner.name,
                "default": partner.default
            })
        return json.dumps(ret)

    def _set_logistic_id_custom_property(self, order_id, logistic_id):
        with self.concurrent_events_lock:
            self.order_service.set_order_custom_property(order_id, "LOGISTIC_ID", logistic_id)

    def _get_logistic_request(
        self,
        order,              # type: Order
        logistic_partner    # type: LogisticPartner
    ):
        # type: (...) -> LogisticRequest

        is_manual_delivery = order.custom_properties.get("PARTNER", "") == "MANUAL"
        key_order_json = "MANUAL_DELIVERY_ORDER_JSON" if is_manual_delivery else "REMOTE_ORDER_JSON"
        remote_order_json = order.custom_properties.get(key_order_json)
        remote_order = json.loads(remote_order_json)
        tenders = remote_order.get("tenders", [])

        total_not_paid_online = self._get_total_not_paid_online(tenders)
        if total_not_paid_online == 0:
            payments = Payment(
                method=PaymentMethod.ONLINE,
                wireless_pos=False,
            )
        else:
            payments = Payment(
                method=PaymentMethod.OFFLINE,
                wireless_pos=True,
                offline_method=[
                    OfflinePayment(
                        type=OfflinePaymentMethod.CREDIT,
                        amount=Price(total_not_paid_online)
                    )
                ]
            )

        address_json = remote_order["pickup"]["address"]
        source_app_id = order.custom_properties.get("APP_ID")
        if "SOURCE_APP_ID" in order.custom_properties:
            source_app_id = order.custom_properties.get("SOURCE_APP_ID")

        source_order_id = remote_order.get("code", "")
        if "SOURCE_ORDER_ID" in order.custom_properties:
            source_order_id = order.custom_properties.get("SOURCE_ORDER_ID")

        brand = order.custom_properties.get("BRAND")
        delivery_fee_value = order.custom_properties.get("DELIVERY_FEE_VALUE")
        return LogisticRequest(
            partner_id=logistic_partner.id,
            store_code=self.store_code,
            brand=brand,
            vehicle=Vehicle(
                types=[
                    VehicleType.MOTORBIKE_BAG,
                    VehicleType.MOTORBIKE_BOX,
                    VehicleType.SCOOTER,
                    VehicleType.BICYCLE,
                    VehicleType.CAR,
                    VehicleType.VUC,
                ],
                container=VehicleContainer.NORMAL,
                container_size=ContainerSize.SMALL,
            ),
            customer_name=order.customer_name,
            delivery_address=Address(
                street=address_json["streetName"],
                number=address_json["streetNumber"],
                complement=address_json.get("complement", None),
                district=address_json["neighborhood"],
                city=address_json.get("city", ""),
                state=address_json.get("state", ""),
                postal_code=address_json.get("postalCode", ""),
                country="BR",
                latitude=address_json.get("latitude"),
                longitude=address_json.get("longitude"),
            ),
            limit_times=TimeLimits(
                pickup_limit=45,
                delivery_limit=45,
            ),
            order=LogisticOrder(
                id=remote_order.get("id", remote_order.get("shortReference", "")),
                source_app_id=source_app_id,
                source_order_id=source_order_id,
                created_at=iso8601.parse_date(remote_order["createAt"]),
                display_id=remote_order.get("shortReference", ""),
                total_weight=0,
                package_volume=0,
                package_quantity=1,
                total_price=remote_order["totalPrice"],
                delivery_fee_value=delivery_fee_value,
                payments=payments,
                manual_delivery=is_manual_delivery,
            ),
            customer=Customer(
                name=order.customer_name,
                document=order.customer_document,
                phone=order.customer_phone,
                phone_localizer=order.customer_phone_localizer,
            )
        )

    def _get_cancel_request(self, order):
        logistic_id = self.order_service.get_order_custom_property(order.id, "LOGISTIC_ID")
        if not logistic_id:
            logger.info("Logistic Id not Found")
            return None

        return LogisticCancelRequest(logistic_id, self.store_code)

    def _get_logistic_confirm_request(self, order):
        return LogisticConfirmRequest(self._get_logistic_id(order.id))

    @staticmethod
    def _get_out_for_delivery_confirm_request(order, logistic_id):
        return DeliveryConfirm(order.remote_id, logistic_id, datetime.utcnow())

    def _get_logistic_partner(self, order):
        partner_id = self._get_logistic_partner_id(order.id)
        if partner_id is None:
            return None
        partner_id = int(partner_id)
        return self.logistic_partners.get(partner_id)

    def _get_logistic_status(self, order_id):
        with self.concurrent_events_lock:
            return self.order_service.get_order_custom_property(order_id, "LOGISTIC_INTEGRATION_STATUS")

    def _get_logistic_id(self, order_id):
        return self.order_service.get_order_custom_property(order_id, "LOGISTIC_ID")

    def _get_logistic_partner_id(self, order_id):
        return self.order_service.get_order_custom_property(order_id, "LOGISTIC_PARTNER_ID")

    def set_deliveryman_data(self, order_id, data):
        return self.order_service.set_order_custom_property(order_id, "LOGISTIC_DELIVERYMAN_DATA", data)

    def _set_confirm_delivery_payment_custom_property_with_lock(
        self,       # type: LogisticService
        order_id,   # type: str
    ):
        # type: (...) -> None

        with self.concurrent_events_lock:
            self.set_confirm_delivery_payment_custom_property(order_id=order_id)

    def set_confirm_delivery_payment_custom_property(
        self,       # type: LogisticService
        order_id,   # type: str
    ):
        # type: (...) -> None

        order = self.order_service.get_order(order_id=order_id)
        if self._order_is_confirmed_with_error_and_is_not_manually_confirmed(order=order):
            return

        custom_properties = [
            CustomProperty(
                key="CONFIRM_DELIVERY_PAYMENT",
                value="True",
            ),
            CustomProperty(
                key="CONFIRM_DELIVERY_PAYMENT_DATETIME",
                value=datetime.now(pytz.utc).isoformat(),
            ),
            ]
        order_picture = self.remote_order_taker.get_local_order(local_order_id=order.id)
        order_picture = order_picture.find("Order") if order_picture.find("Order") else order_picture
        order_status = int(order_picture.get("stateId"))
        if order_status == OrderState.PAID.value or order_status == OrderState.VOIDED.value:
            remote_order_concluded = CustomProperty(
                key="REMOTE_ORDER_STATUS",
                value=RemoteOrderStatus.CONCLUDED.value,
            )
            custom_properties.append(remote_order_concluded)
        
        self.order_service.set_order_custom_properties(
            order_id=order_id,
            custom_properties=custom_properties,
        )

    @staticmethod
    def _order_is_confirmed_with_error_and_is_not_manually_confirmed(
        order,    # type: Order
    ):
        # type: (...) -> bool

        integration_status = order.custom_properties.get("DELIVERY_INTEGRATION_STATUS", "")
        delivery_order_manual = order.custom_properties.get("DELIVERY_ORDER_MANUAL_CONFIRMED", False)
        confirmed_with_error = integration_status == DeliveryIntegrationStatus.CONFIRMED_WITH_ERROR

        return confirmed_with_error and not delivery_order_manual

    def _get_total_not_paid_online(
        self,           # type: LogisticService
        tenders,        # type: Dict[str, Any]
    ):
        total_paid = 0
        for tender in tenders:
            if not self._tender_is_prepaid(tender):
                total_paid += tender.get("value", 0)
        return total_paid

    @staticmethod
    def _tender_is_prepaid(
        tender  # type: Dict
    ):
        # type: (...) -> bool

        return (isinstance(tender['prepaid'], bool) and tender['prepaid']) or tender['prepaid'] == "true"

    def _get_logistic_partners(
        self,    # type: LogisticService
        pos_id,  # type: str
        config   # type: Any
    ):
        # type: (...) -> Dict[int, LogisticPartner]

        ret = {}

        if self.has_own_driver:
            ret[0] = LogisticPartner(
                id=0,
                name=translate_message(get_model(pos_id), "$LOGISTIC_PARTNER_DEFAULT"),
                default=False,
            )

        partners_group = config.find_group("RemoteOrder.Logistic.LogisticPartners")
        if partners_group is not None:
            index = 0
            partner_group = partners_group.find_group("{}".format(index))
            while partner_group is not None:
                partner_id = int(partner_group.find_value("id"))

                ret[partner_id] = LogisticPartner(
                    id=partner_id,
                    name=partner_group.find_value("name"),
                    default=bool(partner_group.find_value("default").lower() == "true"),
                )

                index += 1
                partner_group = partners_group.find_group("{}".format(index))

        return ret

    def _order_needs_logistics(
        self,        # type: LogisticService
        order_id,    # type: str
    ):
        # type: (...) -> bool

        need_logistics_property = self.order_service.get_order_custom_property(
            order_id=order_id,
            key="NEED_LOGISTICS"
        )
        return need_logistics_property.lower() == "true"

    @staticmethod
    def _get_default_logistic_partner(
        logistic_partners,  # type: Dict[int, LogisticPartner]
    ):
        # type: (...) -> Optional[LogisticPartner]

        for partner_id in logistic_partners.keys():
            partner = logistic_partners[partner_id]
            if partner.default:
                return partner
        return None

    def _has_canceled_order_or_delivery_error(
        self,       # type: LogisticService
        order_id,   # type: str | int
    ):
        # type: (...) -> bool

        error_type = self.order_service.get_order_custom_property(
            order_id=order_id,
            key="DELIVERY_ERROR_TYPE",
        )
        void_reason = self.order_service.get_order_custom_property(
            order_id=order_id,
            key="VOID_REASON_ID",
        )

        return error_type is not None or void_reason is not None
