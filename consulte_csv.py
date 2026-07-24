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
from time_hora import *
from order_picture import *
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
    state_recalled = None
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
        elif status_order == 6:
            state_recalled = status_order
    if state_recalled:
        return 6

    if void_time and paid_time and state_id_void == 4 and state_id_paid == 5:
        #diferenca = paid_time - void_time
        minutos = abs((paid_time - void_time).total_seconds() / 60)
        if minutos >= 30:
            logging.info(
                "Cancelada após 30: Order:{}, Nota:{}, Dia:{}, Tempo:{}, Pos:{}".format(order_id, nota, date,
                                                                                                  minutos, type_posid,                                                                                                  ))
            updater_aped_20805(file_connect, order_id, nota)
            return
        else:
            updater_aped_20805(file_connect, order_id, nota)
            return
    if state_id_paid is not None and state_id_void is None:
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
                        update_xml_APED23848(fiscal_banco, base, order_id, 1, nota)
                        logging.info(
                            "Nota em contigencia , vamos alterar o status no banco {}, {}".format(order_id, nota)
                        )
                        return 5
                    else:
                        logging.info("Venda possui o mesmo status entre fiscal/order {}, {}".format(order_id, nota))
                        return 5
                if status in ("Problemas de conexao com a SEFAZ", "Notas anteriores em contingencia"):
                    sale_order_picture = 1 #rder_picture.encode()
                    date_time = order.get("Timestamp").replace("T", " ")
                    date_fiscal = datetime_to_float(date_time)
                    validate_sale = FiscalData(fiscal_banco, posid, order_id, base, nota, sale_order_picture, date_fiscal)
                    insert_fiscal = validate_sale.sales_inquiry()
                    if insert_fiscal:
                        update_xml_APED23848(fiscal_banco, base, order_id, 0, nota)
                        logging.info(
                            "Nota em contigencia , vamos alterar o status no banco {}, {}".format(order_id, nota))
                        return
                    else:
                        validate_sale.insert_fiscal_faltante()
                        return


        if order_disabled and xml_fiscal_disabled is not None:
            xml_encoded = base64.b64decode(xml_fiscal_disabled)
            ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            root = ET.fromstring(xml_encoded)
            cstat = root.find(".//nfe:cStat", ns)
            if cstat.text in ('206', '256', '102'):
                logging.info("Inutizada incorretamente {} APED-23983 nota:{}".format(order_id, nota))
                fiscal = seq_fiscal(fiscal_banco)
                seq = fiscal[0]["fiscal_id"]
                seq += 1
                seq_update(fiscal_banco, seq)
                update_fiscal_order(file_connect, order_id, seq)
                update_xml_APED23848(fiscal_banco, xml_fiscal_disabled, order_id, 555, seq)
                return
            else:
                logging.info("Não identificado inutilização {}, {}".format(order_id, nota))


    if state_id_void and state_id_paid is None:
        sale_custom = orders_customproperties(file_connect, order_id)
        cstat = None
        order_disabled = None
        for sale in sale_custom:
            if sale.get("key") == "DISABLED_FISCAL_XML":
                base = sale.get("value")
                decoder = DecodeBase64(base)
                status = decoder.decode()
                cstat = status
                break
            if sale.get("key") == "ORDER_DISABLED":
                order_disabled = sale.get("value")
        if cstat == "563":
            logging.info(
                "Order foi Void sem status de paid {}, {}, cstat {} : APED-20811 - Erro 563 ".format(order_id, nota,                                                                                                         cstat))
            return
        elif cstat == "102":
                logging.info("Order foi Void sem status de paid {}, {}, cstat {}".format(order_id, nota, cstat))
                return
        elif order_disabled is not  None:
            logging.info("Order foi Void sem status de paid {}, {}, ORDER_DISABLED {} possivel quebra de sequencia".format(order_id, nota, order_disabled))
            return
        else:
            logging.info(
                "Não existe tratamendo ainda, status atual da venda {}, order {} ".format(no_ident_status,
                                                                                             order_id,                                                                                             sale.get("key")))
            return

    elif state_id_paid is None and state_id_void is None:
        updated_state = consulte_orderid(file_connect, order_id)
        for updated in updated_state:
            no_ident_status = updated.get("status_order")
            type_venda = updated.get("ordersubtype")
            if no_ident_status == 2:
                #restart_compont = no_ident_status
                #update_status_remote(file_connect, order_id)
                logging.info("Venda com o status {}, {}, {} : APED-19705 - Aplicado fix".format(no_ident_status, order_id, type_venda))
            elif no_ident_status == 6:
                break
    else:
        if state_id_paid is None and state_id_void is None:
            logging.info("Não existe tratamendo ainda, status atual da venda {}, order {}".format(no_ident_status, order_id))
