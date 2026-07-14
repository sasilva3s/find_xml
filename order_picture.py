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


class StandAlone_OrderPicture:
    def __init__(self, order_id):
        self.app_path = self.get_application_path()
        self.app_bin = os.path.join(self.app_path, 'bin')
        self.orders_not_processed = []
        self.fiscal_id_processed = []
        self.xml_dir = None
        self.application_conn()
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
                return order_picture
            else:
                logging.info("Unable to get orderpicture for order: {}".format(orderid))
        else:
            raise

    def get_application_path(self):
        if sys.platform == 'win32':
            return "C:\edeployPOS"
        else:
            return "/home/administrador/edeployPOS"