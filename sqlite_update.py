# -*- coding: utf-8 -*-
import logging
import sqlite3
import os
import glob
import time
import base64
import xml.etree.ElementTree as ET


def update_fiscal_data(caminho, orderid, updates_dict):
    """
    Atualiza dados fiscais de forma genérica.
    
    Consolida as operações comuns de update em fiscaldata,
    evitando duplicação de código entre connect_fiscal_wrapper
    e connect_fiscal_picture.
    
    Args:
        caminho (str): Caminho do banco de dados fiscal_persistcomp.db
        orderid (int): ID do pedido a atualizar
        updates_dict (dict): Dicionário com colunas e valores
                            Ex: {"senttonfce": 0, "senttobkoffice": 0, "orderpicture": 1}
    """
    with sqlite3.connect(caminho) as fiscal_connect:
        fiscal_cursor = fiscal_connect.cursor()
        set_clause = ", ".join([f"{k} = {v}" for k, v in updates_dict.items()])
        query = f"UPDATE fiscaldata SET {set_clause} WHERE orderid = {orderid}"
        fiscal_cursor.execute(query)
        logging.info(f"Update 'senttonfce' gerado para orderid -> {orderid} com valores {updates_dict}")
        fiscal_connect.commit()


def connect_fiscal_wrapper(caminho, orderid):
    """
    Reseta status fiscal para reenvio NFCE (wrapper).
    
    Atualiza senttonfce e senttobkoffice para 0.
    
    Args:
        caminho (str): Caminho do banco fiscal_persistcomp.db
        orderid (int): ID do pedido
    """
    update_fiscal_data(caminho, orderid, {"senttonfce": 0, "senttobkoffice": 0})


def connect_fiscal_picture(caminho, orderid):
    """
    Reseta status fiscal e marca como picture para reprocessamento.
    
    Atualiza senttonfce, senttobkoffice para 0 e orderpicture para 1.
    
    Args:
        caminho (str): Caminho do banco fiscal_persistcomp.db
        orderid (int): ID do pedido
    """
    update_fiscal_data(caminho, orderid, {"senttonfce": 0, "senttobkoffice": 0, "orderpicture": 1})


def insert_fiscal_faltante(caminho, posid, OrderId, XMLRequest, NumeroNota, DataNota):
    with sqlite3.connect("{}".format(caminho)) as fiscal_connect:
        fiscal_cursor = fiscal_connect.cursor()
        try:
            fiscal_cursor.execute("""INSERT INTO FiscalData("PosId", "OrderId", "XMLRequest", "NumeroNota", "NumeroSat", 
            "NextDateToSend", "SentToNfce", "NextDateToSendToBKC", 
            "OrderPicture", "DataNota", "XMLResponse")
                               VALUES('{}', '{}', '{}', '{}', '00', NULL, '0', NULL, '1', '{}', NULL)
                               """.format(posid, OrderId, XMLRequest, NumeroNota, DataNota))
            logging.info("Inserido venda orderid -- {} , fiscal_persistcomp".format(OrderId))
            fiscal_connect.commit()
        except sqlite3.OperationalError:
            time.sleep(5)
            fiscal_cursor.execute("""INSERT INTO FiscalData("PosId", "OrderId", "XMLRequest", "NumeroNota", "NumeroSat", 
                        "NextDateToSend", "SentToNfce", "NextDateToSendToBKC", 
                        "OrderPicture", "DataNota", "XMLResponse")
                                           VALUES('{}', '{}', '{}', '{}', '00', NULL, '0', NULL, '1', '{}', NULL)
                                           """.format(posid, OrderId, XMLRequest, NumeroNota, DataNota))
            logging.info("Inserido venda orderid -- {} , fiscal_persistcomp".format(OrderId))
            fiscal_connect.commit()
        except sqlite3.InternalError:
            pass

def connect_order_state(caminho, orderid):
    db_orders = []
    with sqlite3.connect("{}".format(caminho)) as orders_id:
        orders = orders_id.cursor()
        res = orders.execute("""select orderid, stateid, Timestamp from orderstatehistory where orderid = {}""".format(orderid))
        for coluna in res:
            values_dict = {"OrderId_order": coluna[0],
                           "status_order": coluna[1],
                           "Timestamp": coluna[2],
                           }
            db_orders.append(values_dict)
    return db_orders

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

def updater_aped_20805(file_connect, order_id, nota):
    sale_custom = orders_customproperties(file_connect, order_id)
    for sale in sale_custom:
        if sale.get("key") == "FISCAL_XML":
            base = sale.get("value")
            xml_encoded = base64.b64decode(base)
            ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            root = ET.fromstring(xml_encoded)
            cstat = root.find(".//nfe:protNFe/nfe:infProt/nfe:cStat", ns)
            if cstat is not None:
                logging.info("Order {} foi cancelada antes de 30 --->, com cstat {} : APED-20805 ".format(order_id, cstat.text))
            else:
                cstat = root.find(".//nfe:infNFe/nfe:ide/nfe:xJust", ns)
                logging.info(
                    "{}, Order {}, Numero {} foi cancelada antes de 30 , Bug - OXAP-5990".format(cstat.text,
                                                                                                             order_id,
                                                                                                             nota))

def updater_OXAP_5832(file_connect, order_id, nota, date, minutos, type_posid):
    sale_custom_ = orders_customproperties(file_connect, order_id)
    for sale in sale_custom_:
        if sale.get("key") == "FISCAL_XML":
            base = sale.get("value")
            xml_encoded = base64.b64decode(base)
            ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            root = ET.fromstring(xml_encoded)
            cstat = root.find(".//nfe:protNFe/nfe:infProt/nfe:cStat", ns)
            if cstat is not None and cstat.text == "100":
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

def not_order_picture():
    file_main_backup = r"C:\edeployPOS\data\server\backups_maintenance\databases"
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



def find_fiscal_id(note_found):
    file_orders = r"C:\edeployPOS\data\server\databases"
    os.chdir(r"{}".format(file_orders))
    os.chdir(".")
    for db_file in glob.glob("order.db*"):
        if len(db_file) > 10:
            continue
        file_data = file_orders + "\{}".format(db_file)
        with sqlite3.connect("{}".format(file_data)) as connect_id:
            orders = connect_id.cursor()
            res = orders.execute("""select orderid, key, value from OrderCustomProperties where key = 'FISCAL_ID' and value = {}""".format(note_found))
            if res:
                for coluna in res:
                    logging.info("Fiscal Number {}, identificado no {}, orderid {}".format(coluna[2], file_data, coluna[0]))
                    orders.execute("""update OrderCustomProperties set Value = 1 WHERE orderid = {} and key = 'REMOTE_ORDER_STATUS'; """.format(coluna[0]))
                    connect_id.commit()
