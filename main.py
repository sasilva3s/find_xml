# -*- coding: utf-8 -*-
from send_orderpaid_boh import *
from xml_file import *
from sqlite_update import *
from time_hora import *
from consulte_csv import *
import subprocess
import time
import logging
import base64
from fix_cupom_protocol import *
import re

log_filename = 'system_script.log'
logging.basicConfig(filename=log_filename, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

arquivo_file = "file_csv/bkoffice.csv"
logging.info("Start Componente")
print "start Componente"
caminho_databases = get_system_version()
if caminho_databases == "/home/administrador/mwpos_server":
    acesso_fiscal = r"""{}/data/server/databases/fiscal_persistcomp.db""".format(caminho_databases)
    file_store = r"""{}/data/server/bundles/storecfg/loader.cfg""".format(caminho_databases)
    localizar_xml = r"{}/bin/".format(caminho_databases)
    Erro = ["Erros", "Enviados/2025/12/"]
    local_fix = "fix_venda/"
    local_fix_data = "{}/data/server/bundles/bkofficeuploader/python/repository".format(caminho_databases)
elif caminho_databases == "C:\edeployPOS":
    acesso_fiscal = r"""{}\data\server\databases\fiscal_persistcomp.db""".format(caminho_databases)
    file_store = r"""{}\data\server\bundles\storecfg\loader.cfg""".format(caminho_databases)
    localizar_xml = r"{}\bin".format(caminho_databases)
    path_orders = r"{}\data\server\databases".format(caminho_databases)
    Erro = ["\Erros", r"\Enviados\2026\06"]
    acesso_orders_tbl = r"""{}\data\server\databases\tblservice.db""".format(caminho_databases)
    # local_fix = "fix_venda/"
    # local_fix_data = "{}/data/server/bundles/bkofficeuploader/python/repository".format(caminho_databeses)
elif caminho_databases == "C:\edeploy-pos-structure":
    acesso_fiscal = r"""{}\data\server\databases\fiscal_persistcomp.db""".format(caminho_databases)
    file_store = r"""{}\data\server\bundles\storecfg\loader.cfg""".format(caminho_databases)
    path_orders = r"{}\data\server\databases".format(caminho_databases)
    localizar_xml = r"{}\bin".format(caminho_databases)
    Erro = ["\Erros", r"\Enviados\2026\06"]
    acesso_orders_tbl = r"""{}\data\server\databases\tblservice.db""".format(caminho_databases)
    # local_fix = "fix_venda/"
    # local_fix_data = "{}/data/server/bundles/bkofficeuploader/python/repository".format(caminho_databeses)



def main():
    logging.info("Validando as informações do arquivo bkoffice.csv")
    arquivo_csv = ler_arquivo_csv(arquivo_file)
    retorno_archilo = file_valor_sentto(arquivo_csv)
    xml_bin = []
    for store_cfg in retorno_archilo:
        xml_numero = store_cfg.get("numeronota")
        sales_data = find_fiscal_id(path_orders, xml_numero)
        if sales_data:
            invoceid = sales_data[0]["nota"]
            order_id = sales_data[0]["orderid"]
            path_sales = sales_data[0]["path_order"]
            xml_bin.append({"numero_nota": xml_numero, "invoceid": invoceid, "orderid": order_id, "path_order": path_sales})
        else:
            logging.info("Nota não identificada no banco {}".format(xml_numero))
    try:
        for xml_file in xml_bin:
            if xml_file.get("numero_nota") == xml_file.get("invoceid"):
                path_pos = xml_file.get("path_order")
                posid = re.search(r'(\d+)$', path_pos)
                consult_order = connect_order_state(xml_file.get("path_order"), xml_file.get("orderid"))
                if consult_order:
                    order_statr = time_direction(consult_order, xml_file.get("orderid"), xml_file.get("path_order"), xml_file.get("invoceid"), posid.group(1), acesso_fiscal)
                    logging.debug(order_statr)
                    if order_statr == 5:
                        StandAlone(xml_file.get("orderid"))
            else:
                logging.info("Vendas não identificadas no order {}, {}, {}, vamos procurar no backup".format(xml_file.get("orderid"), xml_file.get("invoceid"), xml_file.get("posid")))
                for file_databases in not_order_picture():
                    consult_order = connect_order_state(file_databases, xml_file.get("orderid"))
                    if consult_order:
                        order_state = time_direction(consult_order, xml_file.get("orderid"), xml_file.get("path_order"), xml_file.get("invoceid"), posid.group(1), acesso_fiscal)
                        if order_state == 5:
                            insert_db(file_databases, xml_file.get("path_order"), xml_file.get("orderid"))
                            logging.info("Inserido vendas no banco atual {}, {}, {}".format(xml_file.get("orderid"), xml_file.get("invoceid"), xml_file.get("posid")))
                            StandAlone(xml_file.get("orderid"))
    except Exception as ex:
        logging.info("Erro {}".format(ex))


main()
main_fix()
print("Finish Componente")
logging.info("Finish Componente")

