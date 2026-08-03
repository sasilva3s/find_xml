from __future__ import annotations

import argparse
import os
import sys
from typing import List
from xml.etree import cElementTree as eTree


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))


def bootstrap_runtime_dependencies() -> None:

    bin_path = os.path.join(APP_ROOT, "bin")
    hv_data_dir = os.path.join(APP_ROOT, "data", "server")
    bundle_dir = os.path.join(hv_data_dir, "bundles", "auditlogger")

    os.environ.setdefault("BINPATH", bin_path)
    os.environ.setdefault("HVDATADIR", hv_data_dir)
    os.environ.setdefault("BUNDLEDIR", bundle_dir)
    os.environ.setdefault("HVCOMPNAME", "AuditLogger")
    os.environ.setdefault("HVPORT", "14000")
    os.environ.setdefault("HVIP", "127.0.0.1")
    os.environ.setdefault("HVCOMPPORT", "35689")
    os.environ.setdefault("HVPID", "-1")

    sys.path.insert(0, os.path.join(bin_path, "common3.pypkg"))
    sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "lib"))
    os.chdir(os.environ["BINPATH"])

    global MbContextMessageBus, MBEasyContext, OrderService, Config, LogEntry, LogRepository
    from mbcontextmessagehandler import MbContextMessageBus
    from msgbus import MBEasyContext
    from order_api import OrderService

    from auditlogger_services.model.config import Config
    from auditlogger_services.model.log import LogEntry
    from externals.repository import LogRepository


NORMALIZED_EVENT_TYPES = {
    "VOIDED": "VOID_ORDER",
    "ABANDONED": "VOID_ORDER",
    "STORED": "STORED",
    "STORE_ORDER": "STORED",
}


def normalize_event_type(state: str) -> str:

    return NORMALIZED_EVENT_TYPES.get(state, state)


def fetch_order_pictures(
    order_service: OrderService,
    order_ids: List[int],
) -> List[eTree.Element]:

    orders_root = order_service.get_multiple_order_picture(order_ids=order_ids)
    if orders_root is None:
        raise RuntimeError(f"No orders found for order_ids={order_ids}")

    return orders_root.findall("./Order")


def report_missing_order_ids(
    requested_order_ids: List[int],
    found_orders: List[eTree.Element],
) -> None:

    found_ids = {int(order.get("orderId")) for order in found_orders}
    missing_ids = [order_id for order_id in requested_order_ids if order_id not in found_ids]

    if missing_ids:
        print(f"Não encontrados no salecomp: {missing_ids}")


def build_log_entry(order_picture: eTree.Element) -> LogEntry:

    attributes = order_picture.attrib
    event_type = normalize_event_type(attributes.get("state"))

    return LogEntry(
        business_period=attributes.get("businessPeriod"),
        event_type=event_type,
        event_data=eTree.tostring(order_picture, encoding="utf-8"),
        pos_id=int(attributes.get("posId", "-1")),
        created_at_gmt=attributes.get("createdAtGMT"),
        session_id=attributes.get("sessionId"),
        order_id=attributes.get("orderId"),
    )


def order_already_logged(
    log_repository: LogRepository,
    log_entry: LogEntry,
) -> bool:

    existing_order_ids = log_repository.get_orders_by_period(
        state=log_entry.event_type,
        business_period=log_entry.business_period,
    )
    existing_ids_as_int = {int(order_id) for order_id in existing_order_ids}

    return int(log_entry.order_id) in existing_ids_as_int


def backfill_order(
    log_repository: LogRepository,
    order_picture: eTree.Element,
    force: bool,
) -> None:

    log_entry = build_log_entry(order_picture=order_picture)

    already_logged = order_already_logged(log_repository=log_repository, log_entry=log_entry)
    if already_logged and not force:
        print(
            f"Order {log_entry.order_id} (pos_id={log_entry.pos_id}) já está no AuditLog como "
            f"{log_entry.event_type} para o período {log_entry.business_period}; nada a fazer."
        )
        return

    dispatch = log_repository.insert_entry(
        log_entry=log_entry,
        business_period=log_entry.business_period,
    )

    print(
        f"Inserido row_id={dispatch.row_id} order_id={log_entry.order_id} pos_id={log_entry.pos_id} "
        f"evt_type={log_entry.event_type} business_period={log_entry.business_period}"
    )


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser()
    parser.add_argument("--order-ids", type=int, nargs="+", required=True)
    parser.add_argument("--query-pos-id", type=int, default=0)
    parser.add_argument("--audit-output-folder", type=str, required=True)
    parser.add_argument("--force", action="store_true")

    return parser.parse_args()


def main() -> None:

    args = parse_args()
    audit_output_folder = os.path.abspath(args.audit_output_folder)

    bootstrap_runtime_dependencies()

    mb_context = MBEasyContext("backfill_missing_order")
    message_bus = MbContextMessageBus(mb_context)

    try:
        order_service = OrderService(message_bus=message_bus, pos_id=args.query_pos_id)
        order_pictures = fetch_order_pictures(order_service=order_service, order_ids=args.order_ids)
        report_missing_order_ids(requested_order_ids=args.order_ids, found_orders=order_pictures)

        config = Config()
        config.output_folder = audit_output_folder
        config.connection_timeout = 30
        log_repository = LogRepository(output_path=config.output_folder, config=config)

        for order_picture in order_pictures:
            backfill_order(
                log_repository=log_repository,
                order_picture=order_picture,
                force=args.force,
            )

    finally:
        mb_context.MB_EasyFinalize()


if __name__ == "__main__":
    main()
