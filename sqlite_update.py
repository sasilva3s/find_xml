# -*- coding: utf-8 -*-
import logging
import sqlite3
import os
import glob
import time
import base64
import xml.etree.ElementTree as ET

def seq_update(caminho, seq_id):
    """
    Função responsável por se conectar ao banco "fiscal_persistcomp.db" atualizar o status da coluna "senttonfce" para "0"

    :param caminho: Caminho do banco de dados "fiscal_persistcomp.db"
    :param orderid: OrderId da venda
    """
    with sqlite3.connect("{}".format(caminho)) as fiscal_connect:
        fiscal_cursor = fiscal_connect.cursor()
        fiscal_cursor.execute("""update sequencer set seqno = {} where seqnm = 'FiscalId'""".format(seq_id))
        logging.info("Alterado sequencia")
        fiscal_connect.commit()



def update_xml_APED23848(caminho, xmlrequest, orderid, status_senttonfce, nota):
    """
    Função responsável por se conectar ao banco "fiscal_persistcomp.db" atualizar o status da coluna "senttonfce" para "0"

    :param caminho: Caminho do banco de dados "fiscal_persistcomp.db"
    :param orderid: OrderId da venda
    """
    with sqlite3.connect("{}".format(caminho)) as fiscal_connect:
        if status_senttonfce != 1:
            fiscal_cursor = fiscal_connect.cursor()
            fiscal_cursor.execute("""update fiscaldata set senttonfce = {}, numeronota = {} where orderid = {}""".format(status_senttonfce, nota , orderid))
            fiscal_connect.commit()
        else:
            if status_senttonfce != 555:
                fiscal_cursor = fiscal_connect.cursor()
                fiscal_cursor.execute("""update fiscaldata set senttonfce = {}, xmlrequest = '{}' where orderid = {}""".format(status_senttonfce, xmlrequest, orderid))
                fiscal_connect.commit()



class FiscalData:
    def __init__(self, caminho, posid, OrderId, XMLRequest, NumeroNota, order_picture, DataNota):
        self.caminho = caminho
        self.OrderId = OrderId
        self.XMLRequest = XMLRequest
        self.NumeroNota = NumeroNota
        self.DataNota = DataNota
        self.posid = posid
        self.order_picture = order_picture
    def insert_fiscal_faltante(self):
        with sqlite3.connect("{}".format(self.caminho)) as fiscal_connect:
            fiscal_cursor = fiscal_connect.cursor()
            try:
                fiscal_cursor.execute("""INSERT INTO FiscalData("PosId", "OrderId", "XMLRequest", "NumeroNota", "NumeroSat", "NextDateToSend", "SentToNfce", "NextDateToSendToBKC", "OrderPicture", "DataNota", "XMLResponse", "InvoiceType")
                                   VALUES('{}', '{}', '{}', '{}', '00', NULL, '0', NULL, '{}', '{}', NULL, 'NFCE')
                                   """.format(self.posid, self.OrderId, self.XMLRequest, self.NumeroNota, self.order_picture, self.DataNota))
                logging.info("Inserido venda orderid -- {} , fiscal_persistcomp".format(self.OrderId))
                fiscal_connect.commit()
            except sqlite3.OperationalError:
                time.sleep(5)
                fiscal_cursor.execute("""INSERT INTO FiscalData("PosId", "OrderId", "XMLRequest", "NumeroNota", "NumeroSat", "NextDateToSend", "SentToNfce", "NextDateToSendToBKC", "OrderPicture", "DataNota", "XMLResponse", "InvoiceType")
                                                   VALUES('{}', '{}', '{}', '{}', '00', NULL, '0', NULL, '{}', '{}', NULL, 'NFCE')
                                                   """.format(self.posid, self.OrderId, self.XMLRequest,
                                                              self.NumeroNota, self.order_picture, self.DataNota))
                logging.info("Inserido venda orderid -- {} , fiscal_persistcomp".format(self.OrderId))
                fiscal_connect.commit()
            except sqlite3.InternalError:
                pass
    def sales_inquiry(self):
        with sqlite3.connect("{}".format(self.caminho)) as fiscal_connect:
            fiscal_cursor = fiscal_connect.cursor()
            fiscal_cursor.execute("""SELECT * FROM FiscalData WHERE OrderId = {}""".format(self.OrderId))
            res = fiscal_cursor.fetchall()
            if res:
                return res[0]
            else:
                return None

def connect_order_state(path_order, orderid):
    db_orders = []
    with sqlite3.connect("{}".format(path_order)) as orders_id:
        orders = orders_id.cursor()
        res = orders.execute("""select orderid, stateid, Timestamp from orderstatehistory where orderid = {}""".format(orderid))
        for coluna in res:
            values_dict = {"OrderId_order": coluna[0],
                           "status_order": coluna[1],
                           "Timestamp": coluna[2],
                           }
            db_orders.append(values_dict)
    return db_orders

def seq_fiscal(caminho):
    fiscal = []
    with sqlite3.connect("{}".format(caminho)) as orders_id:
        orders = orders_id.cursor()
        res = orders.execute("""select seqno from sequencer where seqnm = 'FiscalId'""")
        for coluna in res:
            values_dict = {"fiscal_id": coluna[0],
                           }
            fiscal.append(values_dict)
    return fiscal

def update_fiscal_order(caminho, orderid, seq_nova):
    with sqlite3.connect("{}".format(caminho)) as orders_id:
        orders = orders_id.cursor()
        orders.execute("""update ordercustomproperties set value = {} where orderid = {} and key = 'FISCAL_ID'""".format(seq_nova, orderid))
        orders_id.commit()



def consulte_orderid(caminho, orderid):
    db_orders = []
    with sqlite3.connect("{}".format(caminho)) as orders_id:
        orders = orders_id.cursor()
        res = orders.execute("""select stateid, ordersubtype from orders where orderid = {}""".format(orderid))
        for coluna in res:
            values_dict = {"status_order": coluna[0],
                           "ordersubtype": coluna[1]
                           }
            db_orders.append(values_dict)
    return db_orders


def orders_customproperties(caminho, orderid):
    results_dict = []
    with sqlite3.connect("{}".format(caminho)) as custom_properties:
        custom = custom_properties.cursor()
        res = custom.execute("""select key, value from ordercustomproperties where orderid = {}""".format(orderid))
        for coluna in res:
            values_dict = {
                "key": coluna[0],
                'value': coluna[1],
            }
            results_dict.append(values_dict)
    return results_dict

def delete_customproperties(caminho, orderid):
    with sqlite3.connect("{}".format(caminho)) as custom_properties:
        custom = custom_properties.cursor()
        custom.execute("""delete from ordercustomproperties where orderid = {} and key == 'ORDER_DISABLED'""".format(orderid))
        logging.info("Delete ORDER_DISABLED : {} para inutilizar novamente".format(orderid))
        custom_properties.commit()

def tblservice_conect(caminho, order):
    db_orders = []
    with sqlite3.connect("{}".format(caminho)) as orders_id:
        orders = orders_id.cursor()
        res = orders.execute("""select orderid, posid from serviceorders where orderid = {}""".format(order))
        for coluna in res:
            values_dict = {"OrderId_order": coluna[0],
                           "posid": coluna[1],
                           }
            db_orders.append(values_dict)
    return db_orders

def updater_aped_20805(file_connect, order_id, nota, status_order = None):
    xml_request = None
    xml_canceled = None
    order_disabled = None
    sale_custom = orders_customproperties(file_connect, order_id)
    for sale in sale_custom:
        if sale.get("key") == "ORDER_DISABLED":
            order_disabled = sale.get("value")
            if str(order_disabled).lower() == "false":
                order_disabled = False
        if sale.get("key") == "FISCAL_XML":
            xml_request = sale.get("value")
        if sale.get("key") == "CANCELED_FISCAL_XML" if sale.get("key") == "CANCELED_FISCAL_XML" else sale.get("key") == "DISABLED_FISCAL_XML":
            xml_canceled = sale.get("value")
    if xml_request is not None and xml_canceled is not None:
        xml_encoded = base64.b64decode(xml_canceled)
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
        root = ET.fromstring(xml_encoded)
        cstat = root.find(".//nfe:cStat", ns)
        if cstat.text in ('135', '102'):
            logging.info("Order {} foi cancelada com cstat {}".format(order_id, cstat.text, nota))
        else:
            logging.info(
                    "Não identificado status de inutilização {}, {}, {} - ".format(cstat.text, order_id, nota))
    elif order_disabled == False and status_order == "cancelada":
        return
    else:
        logging.info("Necessario analisar / não existe tratamento - Orderid = {}, Nota {}".format(order_id, nota))

def updater_OXAP_5832(file_connect, order_id, nota, date, minutos, type_posid):
    sale_custom_ = orders_customproperties(file_connect, order_id)
    for sale in sale_custom_:
        if sale.get("key") == "FISCAL_XML":
            base = sale.get("value")
            xml_encoded = base64.b64decode(base)
            ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            root = ET.fromstring(xml_encoded)
            cstat = root.find(".//nfe:protNFe/nfe:infProt/nfe:cStat", ns)
            if cstat is not None or cstat == 100:
                logging.info("Cancelada após 30: Order:{}, Nota:{}, Dia:{}, Tempo:{}, Pos:{}, cstat {}".format(order_id, nota, date, minutos, type_posid, cstat.text))
            else:
                delete_customproperties(file_connect, order_id)


def updater_aped_20805_unpaid(order_id, base, nota):
        xml_encoded = base64.b64decode(base)
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
        root = ET.fromstring(xml_encoded)
        cstat = root.find(".//nfe:protNFe/nfe:infProt/nfe:cStat", ns)
        if cstat is not None:
            logging.info("Order {} foi cancelada sem status de paid , com cstat {} : APED-20805/APED-21665 ".format(order_id, cstat.text))
        else:
            cstat = root.find(".//nfe:infNFe/nfe:ide/nfe:xJust", ns)
            logging.info(
                "{}, Order {}, Numero {} foi cancelada , mais a alocou o cupom - Bug - OXAP-5990".format(cstat.text, order_id, nota))
def update_status_remote(file_connect, order):
    with sqlite3.connect("{}".format(file_connect)) as orders_id:
        orders = orders_id.cursor()
        res = orders.execute("""update OrderCustomProperties set Value = 1 WHERE orderid = {} and key = 'REMOTE_ORDER_STATUS'; """.format(order))
        res.close()

def not_order_picture(file_backup):
    file_main_backup = r"{}".format(file_backup)
    os.chdir(r"{}".format(file_main_backup))
    os.chdir(".")
    file_data = []
    for db_file in glob.glob("order*"):
        file_data.append(file_main_backup + "\{}".format(db_file))
    return file_data


def insert_db(file_antigo, file_novo, orderid):
    with sqlite3.connect("{}".format(file_antigo)) as connect_id:
        connect = connect_id.cursor()
        connect.execute(
            """attach database '{}' AS banco_novo""".format(file_novo))
        connect.execute(
            """insert into banco_novo.orders select * from orders where orderid = {}""".format(orderid))
        connect.execute(
            """insert into banco_novo.ordercustomproperties select * from ordercustomproperties where orderid = {}""".format(orderid))
        connect.execute(
            """insert into banco_novo.ordertax select * from ordertax where orderid = {}""".format(orderid))
        connect.execute(
            """insert into banco_novo.ordervoidhistory select * from ordervoidhistory where orderid = {}""".format(orderid))
        connect.execute(
            """insert into banco_novo.ordertender select * from ordertender where orderid = {}""".format(orderid))
        connect.execute(
            """insert into banco_novo.orderitem select * from orderitem where orderid = {}""".format(orderid))
        connect.execute(
            """insert into banco_novo.orderstatehistory select * from orderstatehistory where orderid = {}""".format(orderid))
        connect_id.commit()



def find_fiscal_id(path_order, note_found):
    file_orders = r"{}".format(path_order)
    os.chdir(r"{}".format(file_orders))
    os.chdir(".")
    saleline = []
    for db_file in glob.glob("order.db*"):
        if len(db_file) > 10:
            continue
        try:
            file_data = file_orders + "\{}".format(db_file)
            with sqlite3.connect("{}".format(file_data)) as connect_id:
                orders = connect_id.cursor()
                res = orders.execute("""select orderid, key, value from OrderCustomProperties where key = 'FISCAL_ID' and value = {}""".format(note_found))
                if res:
                    for coluna in res:
                        sale_order = {
                            "orderid": coluna[0],
                            "nota": coluna[2],
                            "path_order": file_data,
                        }
                        saleline.append(sale_order)
        except sqlite3.OperationalError:
            pass
    return saleline


def validate_status(path_fiscal, orderid):
    xml_request = []
    with sqlite3.connect("{}".format(path_fiscal)) as connect_id:
        connect = connect_id.cursor()
        res = connect.execute("""select xmlrequest from fiscaldata where orderid = {}""".format(orderid))
        for coluna in res:
            xml_request.append(coluna[0])
    return xml_request