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
from xml_utils import *

def ler_arquivo_csv(caminho_arquivo):
    dados = []
    with open(caminho_arquivo, 'r') as arquivo_csv:
        leitor_csv = arquivo_csv.readlines()
        for linha in leitor_csv:
            dados.append(linha)
    return dados

def get_system_version():
    if is_that_system_windows() and os.path.exists(r"C:\mwpos"):
        return r"C:\mwpos"
    elif is_that_system_windows() and os.path.exists(r"C:\edeployPOS"):
        return r"C:\edeployPOS"
    elif is_that_system_windows() and os.path.exists(r"C:\edeploy-pos-structure"):
        return r"C:\edeploy-pos-structure"
    elif not is_that_system_windows() and os.path.exists("/home/administrador/edeployPOS"):
        return "/home/administrador/edeployPOS"
    else:
        return "/home/administrador/mwpos_server"

def is_that_system_windows():
    return platform.system().lower() == "windows"


def mover_arquivo_xml(diretorio, local):
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




def time_direction(venda, order_id, file_connect, nota, posid):
    """
    Analisa histórico de estados de um pedido e aplica correções necessárias.
    
    Coordena diferentes cenários de estado de pedido:
    - Pedido cancelado E pago (APED-20805, OXAP-5832)
    - Pedido apenas pago (resend)
    - Pedido apenas cancelado (APED-20811, 20813)
    - Pedido sem estado (APED-19705)
    
    Args:
        venda (list): Histórico de estados do pedido
        order_id (int): ID do pedido
        file_connect (str): Caminho do banco de dados
        nota (str): Número da nota fiscal
        posid (int): ID do POS
        
    Returns:
        int or None: 5 (resend), -1 (reprocessar), None (sem ação)
    """
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
        minutos = abs((paid_time - void_time).total_seconds() / 60)
        if minutos >= 30:
            logging.info(
                f"Cancelada após 30: Order:{order_id}, Nota:{nota}, "
                f"Dia:{date}, Tempo:{minutos}, Pos:{type_posid}"
            )
            updater_OXAP_5832(file_connect, order_id, nota, date, minutos, type_posid)
            return None
        else:
            logging.info(f"Cancelada antes de 30 minutos: Order {order_id}, Nota {nota}")
            updater_aped_20805(file_connect, order_id, nota)
            return None
            
    if state_id_paid and state_id_void is None:
        time.sleep(7)
        sale_custom = orders_customproperties(file_connect, order_id)
        for sale in sale_custom:
            if sale.get("key") == "FISCAL_XML":
                xml_bytes = decode_fiscal_xml(sale.get("value"))
                root = parse_fiscal_xml(xml_bytes)
                cstat = extract_cstat(root)
                if cstat is not None:
                    logging.info(
                        f"Order foi Paid {order_id}, cstat {cstat}, Nota {nota} - "
                        "Reenviando para o BOH (Resend)"
                    )
                    return 5
                else:
                    just = extract_justificativa(root)
                    logging.info(
                        f"{just}, Order {order_id}, Numero {nota} - "
                        "Vamos reprocessar SEFAZ (Reprocessamento)"
                    )
                    return -1
        # Se não encontrou FISCAL_XML
        logging.warning(
            f"Order {order_id}, Nota {nota}: Pedido Pago mas FISCAL_XML não encontrado - "
            "Nenhuma correção aplicada"
        )
        return None
        
    if state_id_void and state_id_paid is None:
        sale_custom = orders_customproperties(file_connect, order_id)
        for sale in sale_custom:
            if sale.get("key") == "DISABLED_FISCAL_XML":
                xml_bytes = decode_fiscal_xml(sale.get("value"))
                root = parse_fiscal_xml(xml_bytes)
                cstat = extract_cstat_inut(root)
                if cstat is not None and cstat == "563":
                    logging.info(
                        f"Order foi Void sem status paid {order_id}, {nota}, "
                        f"cstat {cstat} : APED-20811 - Erro 563 (Nenhuma ação necessária)"
                    )
                else:
                    logging.info(
                        f"Order foi Void sem status paid {order_id}, {nota}, "
                        f"cstat {cstat} (Nenhuma ação necessária)"
                    )
                return None
            elif sale.get("key") == "VOID_REASON_DESCR":
                logging.info(
                    f"Order foi Void sem status paid {order_id}, {nota}, "
                    f"Motivo {sale.get('value')} : APED-20813 (Nenhuma ação necessária)"
                )
                return None
            elif sale.get("key") == "FISCAL_XML":
                updater_aped_20805_unpaid(order_id, sale.get("value"), nota)
                return None
            elif sale.get("key") not in ("BENEFIT_LIST", "CUSTOMER_FISCAL_DOCUMENT", "CUSTOMER_DOC",
                                         "CUSTOMER_NAME", "FISCALIZATION_DATE", "FISCAL_ID"):
                logging.warning(
                    f"Nao existe tratamento para status {no_ident_status}, "
                    f"order {order_id}, chave {sale.get('key')}"
                )
                return None
        # Se não encontrou nenhuma custom property relevante
        logging.warning(
            f"Order {order_id}, Nota {nota}: Pedido Void mas nenhuma propriedade customizada relevante - "
            "Nenhuma correção aplicada"
        )
        return None

    elif state_id_paid is None and state_id_void is None:
        updated_state = consulte_orderid(file_connect, order_id)
        restart_needed = False
        for updated in updated_state:
            status = updated.get("status_order")
            type_venda = updated.get("ordersubtype")
            if status == 2:
                restart_needed = True
                update_status_remote(file_connect, order_id)
                logging.info(
                    f"Venda com o status {status}, {order_id}, {type_venda} : "
                    "APED-19705 - Aplicado fix"
                )
                return None
            if status == 6:
                find_fiscal_id(nota)
                logging.info(
                    f"Venda com o status {status}, {order_id}, {type_venda} : "
                    "APED-19705 - Aplicado fix"
                )
                return None
        if restart_needed:
            logging.warning(f"Venda {order_id} requer restart do componente remoteorder")
        else:
            logging.warning(
                f"Order {order_id}, Nota {nota}: Sem estado de pagamento ou cancelamento - "
                "Nenhuma correção aplicada (Status não mapeado)"
            )
        return None

    # Caso padrão: nenhuma condição atendida
    logging.warning(
        f"Order {order_id}, Nota {nota}: Nenhum cenário de correção encontrado - "
        "Nenhuma correção aplicada"
    )
    return None
