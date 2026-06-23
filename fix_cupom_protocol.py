import os
import re
import sys
import glob
import logging
import time
import base64

from datetime import datetime, timedelta
from xml.etree import cElementTree as ET


class FixingCstatCupons:
    def __init__(self):
        self.app_path = self.get_application_path()
        self.app_bin = os.path.join(self.app_path, 'bin')
        self.start_date = datetime.strftime(datetime.today() - timedelta(days=60), "%Y-%m-%d")
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.orders_to_fix = []
        self.orders_to_verify_protocol = []

        self.log_filename = os.path.join(self.app_path, "script_find_xml/fix_cupom_protocol.log")
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', filename=self.log_filename,
                            level=logging.INFO)
        self.logger = logging

        self.nfce_loader = os.path.join(self.app_path, "data/server/bundles/fiscalwrapper/loader_NFCE.cfg")
        if not os.path.exists(self.nfce_loader):
            print("Enviroment is not NFCE. This process is not supported")
            self.logger.info("Enviroment is not NFCE. This process is not supported")
            sys.exit(1)

        self.application_conn()

    def application_conn(self):
        self.mbcontext = None
        pump_path = os.path.join("src", "edpbohpump", "src")
        sys.path.append(os.path.join(self.app_bin, 'common.pypkg'))
        sys.path.append(os.path.join(self.app_bin, 'edpcommon.pypkg'))
        sys.path.append(os.path.join(self.app_path, pump_path))


        os.environ["BINPATH"] = os.path.join(self.app_path, self.app_bin)
        os.environ["HVPORT"] = "14000"
        os.environ["HVIP"] = "127.0.0.1"
        os.environ["HVCOMPPORT"] = "35689"
        os.environ["HVPID"] = "-1"

        os.chdir(os.environ["BINPATH"])
        from msgbus import MBEasyContext, FM_PARAM, TK_SLCTRL_OMGR_ORDERPICT, TK_SYS_ACK, TK_EVT_EVENT, TK_SLCTRL_OMGR_ORDERPICT
        from old_helper import BaseRepository, remove_xml_namespace
        from bustoken import TK_FISCALWRAPPER_SITUATION, TK_FISCALWRAPPER_CHANGE_CONTINGENCY_STATUS
        import cfgtools

        self.mbcontext = MBEasyContext("STANDALONE_SCRIPT")
        self.base_repository = BaseRepository
        self.FM_PARAM = FM_PARAM
        self.TK_EVT_EVENT = TK_EVT_EVENT
        self.TK_SLCTRL_OMGR_ORDERPICT = TK_SLCTRL_OMGR_ORDERPICT
        self.TK_FISCALWRAPPER_SITUATION = TK_FISCALWRAPPER_SITUATION
        self.TK_FISCALWRAPPER_CHANGE_CONTINGENCY_STATUS = TK_FISCALWRAPPER_CHANGE_CONTINGENCY_STATUS
        self.TK_SYS_ACK = TK_SYS_ACK
        self.cfgtools = cfgtools
        self.remove_xml_namespace = remove_xml_namespace

        #print("Proccess is running, view progress see log file on: {}".format(self.log_filename))

    def find_key_pattern(self, orderid):
        fiscal_component_directory = os.path.join(self.app_path, "data/server/bundles/fiscalwrapper")
        logs = glob.glob(os.path.join(fiscal_component_directory, "FiscalWrapperThread.log*"))
        re_pattern = "OrderId: [[0-9]*%s] - Response processamento (.*)" % (str(orderid).zfill(9))
        re_patterns_motivos = ["chNFe:[0-9]{44}", "[0-9]{44}</xMotivo>", "[0-9]{44}"]
        found_keys = []

        for file in logs:
            with open(file) as f:
                log = f.read()

            if re.search(re_pattern, log):
                for found_pattern in re.findall(re_pattern, log):
                    for r in re_patterns_motivos:
                        if re.search(r, found_pattern):
                            for found_key_pattern in re.findall(r, found_pattern):
                                if 'xMotivo' in r:
                                    key = found_key_pattern.split('</xMotivo')[0]
                                    cstat = found_pattern.split('</dhRecbto><cStat>')[1][0:3]
                                elif 'chNFe' in r:
                                    key = found_key_pattern.split("chNFe:")[1]
                                    cstat = found_pattern.split("<cStat>")[2][0:3]
                                else:
                                    key = found_key_pattern
                                    cstat = "539"

                                if key not in found_keys:
                                   found_keys.append(key)
                                   if cstat in ("539", "100", "150"):
                                       self.fix_cstat(orderid, key, cstat)
                                       return True
                                   elif cstat in ("204",):
                                       return self.key_pattern_fiscallog(orderid, key, cstat)
                                   else:
                                       self.logger.info("Found cStat: {} for orderid: {} ".format(cstat, orderid))

        return False

    def key_pattern_fiscallog(self, orderid, key, cstat):
        fiscal_component_directory = os.path.join(self.app_path, "data/server/bundles/fiscalwrapper")
        logs = reversed(glob.glob(os.path.join(fiscal_component_directory, "FiscalWrapper.log*")))
        re_pattern = "OrderId {} final [0-9]*".format(str(orderid).zfill(9))

        for file in logs:
            with open(file) as f:
                log = f.read()

            if re.search(re_pattern, log):
                for found_pattern in re.findall(re_pattern, log):
                    if not found_pattern.split('final ')[1] in key[30:]:
                        key = key[:30] + found_pattern.split('final ')[1]
                        self.fix_cstat(orderid, key, cstat)
                        return True

    def not_send_orders(self,):
        def inner_func(conn):
         # type: (Connection) -> dict
            query = """
            select orderid from fiscaldata where 
            senttonfce <> 1 and datetime(datanota,'unixepoch','localtime') 
            BETWEEN "{}" and "{}" """.format(self.start_date, self.current_date)

            return [(x.get_entry(0)) for x in conn.select(query)]

        orders_not_send = \
            self.base_repository(self.mbcontext).execute_with_connection(inner_func, service_name="FiscalPersistence")
        if len(orders_not_send) > 0:
            return orders_not_send

        #self.logger.info("No found orders not send for period: {} {}".format(self.start_date, self.current_date))

    def force_orders(self,):
        def inner_func(conn):
         # type: (Connection) -> dict
            query = \
                """update fiscaldata set senttonfce = 555 where orderid in ({})""".format(','.join(self.orders_to_fix))

            conn.select(query)

        self.base_repository(self.mbcontext).execute_with_connection(inner_func, service_name="FiscalPersistence")

    def send_fiscalwrapper_event(self, content, token=None):
        if not token:
            token = self.TK_EVT_EVENT
            content = '\0{}'.format(content)

        self.mbcontext.MB_EasySendMessage(
            "FiscalWrapper",
            token,
            format=self.FM_PARAM,
            data=content
        )

    def get_order_picture(self, orderid):
        msg = self.mbcontext.MB_EasySendMessage(
            "ORDERMGR0",
            self.TK_SLCTRL_OMGR_ORDERPICT,
            format=self.FM_PARAM,
            data='\00{}'.format(orderid)
        )

        if msg.token == self.TK_SYS_ACK:
            parsed_data = msg.data.split("\0")
            if parsed_data[2] != "":
                return ET.fromstring(parsed_data[2]).find('./')

    def get_xml_request(self, orderid):
        def inner_func(conn):
         # type: (Connection) -> dict
            query = """select xmlrequest from fiscaldata where orderid = {}""".format(orderid)

            return [(x.get_entry(0)) for x in conn.select(query)]

        return self.base_repository(self.mbcontext).execute_with_connection(inner_func, service_name="FiscalPersistence")

    def get_cupons(self,):
        def inner_func(conn):
         # type: (Connection) -> dict
            query = """select numeronota from fiscaldata where orderid in ({})""".format(",".join(self.orders_to_fix))

            return [(x.get_entry(0)) for x in conn.select(query)]

        return self.base_repository(self.mbcontext).execute_with_connection(inner_func, service_name="FiscalPersistence")

    def get_pos_list(self):
        def inner_func(conn):
            query = """select posid from posstate"""

            return [(x.get_entry(0)) for x in conn.select(query)]

        return self.base_repository(self.mbcontext).execute_with_connection(inner_func, db_name="posctrl")

    def get_orders_unused(self):
        pos_list = self.get_pos_list()

        def inner_func(conn):
            query = """select orderid from orders where orderid in (select orderid from 
            OrderCustomProperties where key = 'ORDER_DISABLED' and value = 'false')"""

            return [(x.get_entry(0)) for x in conn.select(query)]

        return self.base_repository(self.mbcontext).execute_parallel_in_all_databases_returning_flat_list(inner_func, pos_list)

    def delete_failed_inutilization(self, orderid):
        pos_list = self.get_pos_list()
        conn = None
        def inner_func(conn):
            query = """delete from ordercustomproperties where key = 'ORDER_DISABLED' and orderid = {}""".format(orderid)

            conn.select(query)

        try:
            self.base_repository(self.mbcontext).execute_parallel_in_all_databases_returning_flat_list(inner_func, pos_list)
        except Exception as ex:
            if conn:
                conn.close()
        finally:
            if conn:
                conn.close()

    def process_resign(self):
        self.orders_not_send = self.not_send_orders()

        if self.orders_not_send:
            for orderid in self.orders_not_send:
                order_picture = self.get_order_picture(orderid)
                if order_picture:
                    stateid = order_picture.find('.').attrib["stateId"]

                    if not stateid == "4" and not (self.find_key_pattern(orderid)):
                        self.orders_to_fix.append(orderid)
                else:
                    self.logger.info("Unable to obtain orderpicture for orderid: {}".format(orderid))

        if len(self.orders_to_verify_protocol) > 0:
            min_date = min(self.orders_to_verify_protocol)
            max_date = max(self.orders_to_verify_protocol)
            self.running_nfe_situation(min_date, max_date)
            #self.logger.info("Running check situation sefaz. See process on fiscalwrapper.log")


        if not len(self.orders_to_fix) > 0:
            #
            #self.logger.info("Not found any order paid pending to sent SEFAZ")
            return

        self.force_orders()
        self.cupons_to_fix = self.get_cupons()
        #self.logger.info("Resign sent NumeroNota to SEFAZ: {} ".format(",".join(self.cupons_to_fix)))
        self.send_fiscalwrapper_event("ReSignXMLs")
        self.send_fiscalwrapper_event("Enabled", token=self.TK_FISCALWRAPPER_CHANGE_CONTINGENCY_STATUS)
        time.sleep(6)
        self.send_fiscalwrapper_event("Disabled", token=self.TK_FISCALWRAPPER_CHANGE_CONTINGENCY_STATUS)

    def process_unused_orders(self):
        self.orders = self.get_orders_unused()
        self.orders_unused = set(self.orders)
        force_disable = None

        for orderid in self.orders_unused:
            order_picture = self.get_order_picture(orderid)
            if order_picture:
                stateid = order_picture.find('.').attrib["stateId"]
                numeronota = order_picture.find("CustomOrderProperties/OrderProperty/[@key='FISCAL_ID']").get('value')
                pos_id = order_picture.find('.').attrib["posId"]
                for state in reversed(order_picture.find('StateHistory').findall('State')):
                    if state.get('stateId') == "5":
                        force_disable = False
                        break

                    force_disable = True

                if force_disable:
                    inutilization_status = \
                        order_picture.find("CustomOrderProperties/OrderProperty/[@key='ORDER_DISABLED']").get('value')

                    self.logger.info("Forcing inutilization for orderid: {} numeronota: {} "
                                     "stateid: {} current inutilization state: {}"
                                     .format(orderid, numeronota, stateid, inutilization_status))
                    self.delete_failed_inutilization(orderid)
                    self.send_fiscalwrapper_event("DisableNfceOrder")
                    time.sleep(5)
            else:
                self.logger.info("Unable to obtain orderpicture for orderid: {}".format(orderid))


    def update_fiscal_persist(self, orderid, new_request):
        def inner_func(conn):
         # type: (Connection) -> dict
            query = """update fiscaldata set xmlrequest = '{}' where orderid = {}""".format(new_request, orderid)

            conn.select(query)

        self.base_repository(self.mbcontext).execute_with_connection(inner_func, service_name="FiscalPersistence")
    def fix_cstat(self, orderid, key, cstat):
        order_picture = self.get_order_picture(orderid)
        xml_request = order_picture.find("CustomOrderProperties/OrderProperty/[@key='FISCAL_XML']").get('value')
        xml_string_fiscal_persistcomp = base64.b64decode(xml_request)
        xml_element = ET.fromstring(xml_string_fiscal_persistcomp)
        inf_nfe = self.remove_xml_namespace(ET.tostring(xml_element.find(".//")))
        nfe_key = inf_nfe.attrib["Id"][3:]
        new_xml = xml_string_fiscal_persistcomp.replace(nfe_key, key)
        numeronota = inf_nfe.find(".//nNF").text

        base_xml = '''<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'''
        end_xml = '''<protNFe versao="4.00"><infProt><tpAmb>1</tpAmb><verAplic>PR-v4_4_9</verAplic><chNFe></chNFe><dhRecbto>2021-08-02T16:30:48-03:00</dhRecbto><cStat>204</cStat><xMotivo>Duplicidade de NF-e [nRec:411002421114061]</xMotivo></infProt></protNFe></nfeProc>'''

        new_request = base64.b64encode(base_xml + new_xml + end_xml)
        self.logger.info("Fixing protocol orderid: {} numero nota: {} with key: {}".format(orderid,numeronota, key))
        self.update_fiscal_persist(orderid, new_request)
        date = inf_nfe.find(".//dhEmi").text[0:10].replace('-', '')

        if date not in self.orders_to_verify_protocol:
            self.orders_to_verify_protocol.append(date)

    def running_nfe_situation(self, min_date, max_date):
        data = "{}\0{}".format(min_date, max_date)
        self.send_fiscalwrapper_event(data, token=self.TK_FISCALWRAPPER_SITUATION)

    def get_application_path(self):
        if sys.platform == 'win32':
            return "C:/edeployPOS"
        else:
            return "/home/administrador/edeployPOS"

