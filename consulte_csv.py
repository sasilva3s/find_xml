# -*- coding: utf-8 -*-
import base64
import logging
import sqlite3
import os
import platform
import shutil
import time
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from sqlite_update import *
from fix_apply import *
from decode_base64 import *

def ler_arquivo_csv(caminho_arquivo):
    dados = []
    with open(caminho_arquivo, 'r') as arquivo_csv:
        leitor_csv = arquivo_csv.readlines()
        for linha in leitor_csv:
            dados.append(linha)
    return dados

def get_system_version():
    if is_that_system_windows() and os.path.exists("C:\mwpos"):
        return "C:\mwpos"
    elif is_that_system_windows() and os.path.exists("C:\edeployPOS"):
        return "C:\edeployPOS"
    elif is_that_system_windows() and os.path.exists("C:\edeploy-pos-structure"):
        return "C:\edeploy-pos-structure"
    elif not is_that_system_windows() and os.path.exists("/home/administrador/edeployPOS"):
        return "/home/administrador/edeployPOS"
    else:
        return r"/home/administrador/mwpos_server"

def is_that_system_windows():
    return platform.system().lower() == "windows"


def mover_arquvi_xml(diretorio, local):
    arquivo_diretorio_fiscalrepository = (
        "{}/fiscalrepository.py".format(diretorio))
    arquivo_local_fiscalrepository = ("{}/fiscalrepository.py".format(local))
    shutil.copy(arquivo_diretorio_fiscalrepository,
                arquivo_local_fiscalrepository)

def open_store_cfg(caminho_bkoffice):
    codido_centro = "{}".format(caminho_bkoffice)
    tree = ET.parse(codido_centro)
    root = tree.getroot()
    persistcomp_normal = root.find(".//key[@name='Id']/string").text
    return str(persistcomp_normal)

def file_valor_sentto(file_csv):
    valida_dic = []
    for file in file_csv:
        file_csv_le = file.replace("\n", "").split(";")
        file_valida = {
            "numeronota": file_csv_le[0]
        }
        valida_dic.append(file_valida)
    return valida_dic




def time_direction(venda, order_id, file_connect, nota, posid, fiscal_banco):
    void_time = None
    paid_time = None
    state_id_paid = None
    state_id_void = None
    no_ident_status = None
    type_posid = None
    if posid == 0:
        type_posid = "Delivery"
    for order in venda:
        time_order = order.get("Timestamp")
        status_order = int(order.get("status_order"))
        date = datetime.strptime(time_order, "%Y-%m-%dT%H:%M:%S.%f")
        no_ident_status = status_order
        if status_order == 4:
            void_time = date
            state_id_void = status_order
        elif status_order == 5:
            paid_time = date
            state_id_paid = status_order

    if void_time and paid_time and state_id_void == 4 and state_id_paid == 5:
        #diferenca = paid_time - void_time
        minutos = abs((paid_time - void_time).total_seconds() / 60)
        if minutos >= 30:
            logging.debug(
                "Cancelada após 30: Order:{}, Nota:{}, Dia:{}, Tempo:{}, Pos:{}".format(order_id, nota, date,
                                                                                                  minutos, type_posid,                                                                                                  ))
            updater_OXAP_5832(file_connect, order_id, nota, date, minutos, type_posid)
            return
        else:
            updater_aped_20805(file_connect, order_id, nota)
            return
    if state_id_paid and state_id_void is None:
        time.sleep(7)
        sale_custom = orders_customproperties(file_connect, order_id)
        order_disabled = False
        xml_fiscal_disabled = None
        for sale in sale_custom:
            if sale.get("key") == "FISCAL_XML":
                base = sale.get("value")
                decoder = DecodeBase64(base)
                status = decoder.decode()
                if status in ("100", "150"):
                    logging.info("Validar cstat no fiscal_persistcomp".format(order_id, status))
                    xml_request = validate_status(fiscal_banco, order_id)
                    decoder_xml = DecodeBase64(xml_request[0])
                    status_xml = decoder_xml.decode()
                    if status != status_xml:
                        logging.info(
                            "Order:{}, Nota:{}, Cstat: {} | Fiscal Status_xml:{} Atualizando informações no fiscal".format(
                                order_id, nota, status, status_xml))
                        update_xml_APED23848(fiscal_banco, base, order_id, 1)
                        return 5
                    else:
                        logging.info("Venda possui o mesmo status entre fiscal/order {}, {}".format(order_id, nota))
                        return 5
                if status == "Problemas de conexao com a SEFAZ":
                    logging.info("Nota em contigencia , vamos alterar o status no banco {}, {}".format(order_id, nota))
                    update_xml_APED23848(fiscal_banco, base, order_id, 0, nota)
                    return
        for sale_cancel in sale_custom:
            if sale_cancel.get("key") == "ORDER_DISABLED":
                if sale_cancel.get("value") == "true":
                    order_disabled = True
            if sale_cancel.get("key") == "DISABLED_FISCAL_XML":
                xml_fiscal_disabled = sale_cancel.get("value")
        if order_disabled and xml_fiscal_disabled is not None:
            xml_encoded = base64.b64decode(xml_fiscal_disabled)
            ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            root = ET.fromstring(xml_encoded)
            cstat = root.find(".//nfe:cStat", ns)
            if cstat.text == '206':
                logging.info("Inutizada incorretamente {} APED-23983 nota:{}".format(order_id, nota))
                fiscal = seq_fiscal(fiscal_banco)
                seq = fiscal[0]["fiscal_id"]
                seq += 1
                seq_update(fiscal_banco, seq)
                update_fiscal_order(file_connect, order_id, seq)
                update_xml_APED23848(fiscal_banco, xml_fiscal_disabled, order_id, 555, seq)
                return
            else:
                logging.info("Não identificado inutilização".format(order_id, nota))


    if state_id_void and state_id_paid is None:
        sale_custom = orders_customproperties(file_connect, order_id)

        for sale in sale_custom:
            if sale.get("key") == "DISABLED_FISCAL_XML":
                base = sale.get("value")
                xml_encoded = base64.b64decode(base)
                ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
                root = ET.fromstring(xml_encoded)
                cstat = root.find(".//nfe:retInutNFe/nfe:infInut/nfe:cStat", ns)
                if cstat is not None and cstat.text == "563":
                    logging.info("Order foi Void sem status de paid {}, {}, cstat {} : APED-20811 - Erro 563 ".format(order_id, nota, cstat.text))
                    return
                else:
                    logging.info("Order foi Void sem status de paid {}, {}, cstat {}".format(order_id, nota, cstat.text))
                    return
            elif sale.get("key") == "VOID_REASON_DESCR":
                logging.info("Order foi Void sem status de paid {}, {}, Motivo {} : APED-20813 ".format(order_id, nota, sale.get("value")))
                return
            elif sale.get("key") == "FISCAL_XML":
                base = sale.get("value")
                updater_aped_20805_unpaid(order_id, base, nota)
                return


            elif sale.get("key") not in ("BENEFIT_LIST", "CUSTOMER_FISCAL_DOCUMENT", "CUSTOMER_DOC", "CUSTOMER_NAME",
                                         "FISCALIZATION_DATE", "FISCAL_ID"):
                logging.info(
                    "Não existe tratamendo ainda, status atual da venda {}, order {}, {}".format(no_ident_status,
                                                                                                 order_id,
                                                                                                 sale.get("key")))
                return

    elif state_id_paid is None and state_id_void is None:
        updated_state = consulte_orderid(file_connect, order_id)
        restart_compont = None
        for updated in updated_state:
            no_ident_status = updated.get("status_order")
            type_venda = updated.get("ordersubtype")
            if no_ident_status == 2:
                restart_compont = no_ident_status
                update_status_remote(file_connect, order_id)
                logging.info("Venda com o status {}, {}, {} : APED-19705 - Aplicado fix".format(no_ident_status, order_id, type_venda))
        if no_ident_status == 6:
            find_fiscal_id(nota)
            logging.info("Venda com o status {}, {}, {} : APED-19705 - Aplicado fix".format(no_ident_status, order_id, type_venda))
        elif restart_compont is not None:
            main_fix()

    else:
        if state_id_paid is None and state_id_void is None:
            logging.info("Não existe tratamendo ainda, status atual da venda {}, order {}".format(no_ident_status, order_id))
