import os
import logging
import sys
import base64
import json
import time
import sqlite3

from datetime import datetime, timedelta
from xml.etree import cElementTree as ET
from zlib import compressobj, Z_DEFAULT_COMPRESSION, DEFLATED, MAX_WBITS
from base64 import b64encode

class StandAlone():
    def __init__(self, order_id):
        self.app_path = self.get_application_path()
        self.app_bin = os.path.join(self.app_path, 'bin')
        self.orders_not_processed = []
        self.fiscal_id_processed = []
        self.xml_dir = None

        self.log_filename = os.path.join(self.app_path, "scripts/send_orderpaid_boh.log")
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', filename=self.log_filename,
                            level=logging.INFO)
        self.logger = logging


        self.application_conn()
        self.logger.info("Starting process")

        self.orders_to_send = [order_id]

        self.process_orders()

    def application_conn(self):
        self.mbcontext = None
        pump_path = os.path.join("src", "edpbohpump", "src")
        sys.path.append(os.path.join(self.app_bin, 'common.pypkg'))
        sys.path.append(os.path.join(self.app_bin, 'edpcommon.pypkg'))
        sys.path.append(os.path.join(self.app_path, pump_path))


        os.environ["BINPATH"] = os.path.join(self.app_path, self.app_bin)
        os.environ["LOADERCFG"] = os.path.join(self.app_path, os.path.join("data\\server\\bundles\\storecfg\\loader.cfg"))
        os.environ["HVPORT"] = "14000"
        os.environ["HVIP"] = "127.0.0.1"
        os.environ["HVCOMPPORT"] = "35689"
        os.environ["HVPID"] = "-1"

        os.chdir(os.environ["BINPATH"])
        from msgbus import MBEasyContext, FM_PARAM, TK_SLCTRL_OMGR_ORDERPICT, TK_SYS_ACK
        from old_helper import BaseRepository, remove_xml_namespace
        import cfgtools

        self.mbcontext = MBEasyContext("STANDALONE_SCRIPT")
        self.base_repository = BaseRepository
        self.FM_PARAM = FM_PARAM
        self.TK_SLCTRL_OMGR_ORDERPICT = TK_SLCTRL_OMGR_ORDERPICT
        self.TK_SYS_ACK = TK_SYS_ACK
        self.cfgtools = cfgtools
        self.remove_xml_namespace = remove_xml_namespace

        LOADER_CFG = os.environ["LOADERCFG"]
        self.config = self.cfgtools.read(LOADER_CFG)

        self.store_id = str(self.config.find_value("PublishedConfiguration.Store.Id"))
        print("Proccess is running, view progress on file: {}".format(self.log_filename))


    def process_orders(self):
        orderid = self.orders_to_send[0]
        msg = self.mbcontext.MB_EasySendMessage(
                "ORDERMGR0",
                self.TK_SLCTRL_OMGR_ORDERPICT,
                format=self.FM_PARAM,
                data='\00{}'.format(orderid)
        )
        if msg.token == self.TK_SYS_ACK:
            parsed_data = msg.data.split("\0")
            if parsed_data[2] != "":
                order_picture = ET.fromstring(parsed_data[2]).find('./')
            else:
                self.logger.info("Unable to get orderpicture for order: {}".format(orderid))
        else:
            raise

        if order_picture:
            try:
                xml_base64 = \
                    order_picture.find("CustomOrderProperties/OrderProperty/[@key='FISCAL_XML']").get('value')
                numero_nota = \
                    order_picture.find("CustomOrderProperties/OrderProperty/[@key='FISCAL_ID']").get('value')
            except Exception:
                if order_picture.find('.').attrib["stateId"] == "5":
                    self.logger.info("CustomProperty FISCAL_XML dont exist for order:{}".format(orderid))
                    self.logger.info("Fix missing customproperty and run process again")


            xml_element = ET.fromstring(base64.b64decode(xml_base64))
            xml_element = self.remove_xml_namespace(ET.tostring(xml_element))
            if xml_element.find("infCFe"):
                found_key = xml_element.find("infCFe").attrib.get("Id")
            else:
                found_key = xml_element.find(".//infNFe").attrib['Id']

            payload = self.build(order_picture)
            self.send_request('PAID', payload, orderid, found_key, False)
        else:
            self.logger.info("Unable to get orderpicture for order: {}".format(orderid))


    def _get_business_period(self, order_picture):
        order_business_period = order_picture.get("businessPeriod")
        return datetime.strptime(order_business_period, "%Y%m%d").strftime("%Y-%m-%d")

    def _get_custom_properties(self, order_picture):
        payload_custom_order_properties = {}


        custom_properties = order_picture.find("CustomOrderProperties") or []
        for custom_property in custom_properties:
            if custom_property.get("key").upper() not in ["FISCAL_XML", "CANCELED_FISCAL_XML"]:
                custom_property_key = custom_property.get("value").encode("utf-8")
                payload_custom_order_properties[custom_property.get("key")] = custom_property_key

        return payload_custom_order_properties

    @staticmethod
    def _get_sale_line_comments(sale_line):
        # type: (eTree.Element) -> List

        comments = []
        for comment in sale_line.findall("Comment"):
            comments.append(comment.get("comment").encode("utf-8"))

        return comments

    @staticmethod
    def _get_sale_line_custom_properties(sale_line):
        # type: (eTree.Element) -> Dict

        custom_properties = {}
        try:
            custom_properties = json.loads(sale_line.get("customProperties") or {})
        except Exception:
            pass
        return custom_properties

    @staticmethod
    def _get_sale_line_tags(sale_line):
        # type: (eTree.Element) -> List

        item_tags = []
        try:
            json_array_tags = json.loads(sale_line.get("jsonArrayTags") or "[]")
            for temp_tag in json_array_tags:
                item_tags.append(temp_tag)
        except Exception:
            pass

        return item_tags

    @staticmethod
    def _get_sale_line_tax_items(sale_line):

        tax_items = []
        for order_tax_item in sale_line.findall("TaxItem"):
            tax_item = {
                "base-amount-ad": float(order_tax_item.get("baseAmountAD", 0)),
                "base-amount-bd": float(order_tax_item.get("baseAmountBD", 0)),
                "tax-amount-ad": float(order_tax_item.get("taxAmountAD", 0)),
                "tax-amount-bd": float(order_tax_item.get("taxAmountBD", 0)),
                "tax-index": order_tax_item.get("taxIndex"),
                "tax-rate": float(order_tax_item.get("taxRate", 0)),
                "tax-rule-id": int(order_tax_item.get("taxRuleId"))
            }
            tax_items.append(tax_item)

        return tax_items
    def _get_sale_lines(self, order_picture):
        payload_order_sale_lines = []
        for sale_line in order_picture.findall("SaleLine"):
            sale_line_payload = {
                "added-qty": sale_line.get("addedQty") if sale_line.get("addedQty") else None,
                "added-unit-price": float(sale_line.get("addedUnitPrice") or 0),
                "chosen-qty": sale_line.get("chosenQty") if sale_line.get("chosenQty") else None,
                "comment": self._get_sale_line_comments(sale_line),
                "custom-properties": self._get_sale_line_custom_properties(sale_line),
                "default-qty": sale_line.get("defaultQty") if sale_line.get("defaultQty") else None,
                "inc-qty": sale_line.get("incQty") or 0,
                "dec-qty": sale_line.get("decQty") or 0,
                "item-discount": float(sale_line.get("itemDiscount") or 0),
                "discount-applied": sale_line.get("discountsApplied"),
                "item-price": float(sale_line.get("itemPrice") or 0),
                "item-type": sale_line.get("itemType"),
                "line-number": int(sale_line.get("lineNumber")),
                "level": int(sale_line.get("level")),
                "multiplied-qty": sale_line.get("multipliedQty") or sale_line.get("qty") or 0,
                "qty": sale_line.get("qty") or 0,
                "menu-item-code": sale_line.get("itemId"),
                "price-key": sale_line.get("priceKey"),
                "product": sale_line.get("productName").encode("utf-8"),
                "part-code": sale_line.get("partCode"),
                "plu": sale_line.get("partCode"),
                "order-picture-id": int(order_picture.get("orderId")),
                "sub-qty": sale_line.get("subQty") if sale_line.get("subQty") else None,
                "sub-unit-price": float(sale_line.get("subUnitPrice") or 0),
                "tax-items": self._get_sale_line_tax_items(sale_line),
                "tags": self._get_sale_line_tags(sale_line),
                "unit-price": float(sale_line.get("unitPrice") or 0)
            }
            payload_order_sale_lines.append(sale_line_payload)

        return payload_order_sale_lines

    def _get_tender_detail(self, tender_line):
        tender_detail_json = tender_line.get("tenderDetail")
        if not tender_detail_json:
            return None

        try:
            tender_detail = json.loads(tender_detail_json)
        except Exception:
            return None

        self._fix_missing_tender_details(tender_detail)
        return json.dumps(tender_detail)

    @staticmethod
    def _fix_missing_tender_details(tender_detail):
        for value in tender_detail.keys():
            if tender_detail[value] is None:
                tender_detail[value] = ""

    def _get_tenders(self, order_picture):

        payload_order_tenders = []
        for tender_line in order_picture.findall("TenderHistory/Tender"):
            tender_payload = {
                "change-amount": float(tender_line.get("change") or 0),
                "order-picture-id": int(order_picture.get("orderId")),
                "reference-amount": float(tender_line.get("tenderAmount") or 0),
                "tender-amount": float(tender_line.get("tenderAmount") or 0),
                "tender-detail": self._get_tender_detail(tender_line),
                "tender-type": tender_line.get("tenderType"),
                "tip": float(tender_line.get("tip")) if tender_line.get("tip") else None
            }
            payload_order_tenders.append(tender_payload)

        return payload_order_tenders

    @staticmethod
    def _get_pos_user_id(session_id):
        # type: (str) -> str

        if session_id == "":
            return '-1'

        for session_data in session_id.split(","):
            key, value = session_data.split("=")
            if key == "user":
                return value

    def _get_state_history(self, order_picture):
        list_state_history = []
        for state in order_picture.find('StateHistory').findall('State'):
            list_state_history.append({
                'state': state.get('state'),
                'state-id': state.get('stateId'),
                'timestamp': state.get('timestamp'),
                'timestamp-gmt': state.get('timestampGMT'),
            })
        return list_state_history

    def build(self, order_picture):
        # type: (EventData) -> str

        session_id = order_picture.get('sessionId')
        sale_type = int(order_picture.get("saleType") or 0)
        exemption = int(order_picture.get("exemptionCode")) if order_picture.get("exemptionCode") else None

        payload = {
            "business-dt": self._get_business_period(order_picture),
            "change": float(order_picture.get("change") or 0),
            "creation-dttm": order_picture.get("createdAtGMT") + "Z",
            "discount-amount": float(order_picture.get("discountAmount") or 0),
            "discount-applied": order_picture.get("discountsApplied"),
            "due-amount": float(order_picture.get("dueAmount") or 0),
            "exemption": exemption,
            "order-code": str(order_picture.get("orderId")),
            "order-discount-amount": float(order_picture.get("orderDiscountAmount") or 0),
            "order-picture-custom-order-properties": self._get_custom_properties(order_picture),
            "order-picture-sale-lines": self._get_sale_lines(order_picture),
            "order-picture-tenders": self._get_tenders(order_picture),
            "originator-code": str(int(order_picture.get("originatorId")[-2:])),
            "pod-type": order_picture.get("podType"),
            "pos-code": order_picture.get("posId"),
            "pos-user-id": self._get_pos_user_id(session_id),
            "price-basis": order_picture.get("priceBasis"),
            "price-list": order_picture.get("priceList"),
            "sale-type-id": sale_type if sale_type >= 0 else 0,
            "session": session_id,
            "state-id": int(order_picture.get("stateId")),
            "store-code": str(self.store_id),
            "tax-applied": float(order_picture.get("taxTotal") or 0),
            "tax-total": float(order_picture.get("taxTotal") or 0),
            "tip": float(order_picture.get("tip") or 0),
            "total-after-discount": float(order_picture.get("totalAfterDiscount") or 0),
            "total-amount": float(order_picture.get("totalAmount") or 0),
            "total-gift": 0,
            "total-gross": float(order_picture.get("totalGross") or 0),
            "total-tender": float(order_picture.get("totalTender") or 0),
            "type-id": int(order_picture.get("typeId")),
            "state-history": self._get_state_history(order_picture),
            "xml-data": order_picture.find("CustomOrderProperties/OrderProperty/[@key='FISCAL_XML']").get('value'),
            "xml-data-cancel": None
        }

        return json.dumps(payload)

    def send_request(self, event_data, payload, orderid, fiscal_key, include_store_id=False):
        import requests

        from requests.packages.urllib3.exceptions import InsecureRequestWarning
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

        server_url = str(self.config.find_value("PublishedConfiguration.BackOffice.Host"))
        api_key = str(self.config.find_value("PublishedConfiguration.BackOffice.ApiKey"))

        try:
            current_view = "/pump/sale/order-picture"

            payload = payload.encode("utf-8")

            self.fiscal_id_processed.append(fiscal_key)

            self.logger.info("Sending event to boh server orderid: {} fiscal key: {}".format(orderid, fiscal_key))

            headers = {
                "Accept": "application/json",
                "Content-type": "application/json",
                "x-api-key": api_key
            }

            post_url = "https://" + server_url + current_view
            if include_store_id:
                post_url += "/{}".format(self.store_id)

            response = requests.post(post_url, headers=headers, data=payload, timeout=30, verify=False)

            if response.status_code != 200:
                log_message = "[{0}] Error sending event to server. Status: {1} - {2}"
                self.logger.info(log_message.format(event_data, response.status_code, response.content))
                time.sleep(30000 / 1000.0)


            self.logger.info("Success sending event to server: {}".format(orderid))
            time.sleep(1)


        except (Exception,):
            message = "[{}] Communication error sending event to server".format(orderid)
            self.logger.exception(message)

    def get_application_path(self):
        if sys.platform == 'win32':
            return "C:\edeployPOS"
        else:
            return "/home/administrador/edeployPOS"


