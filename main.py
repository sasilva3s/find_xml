# -*- coding: utf-8 -*-
from send_orderpaid_boh import *
from xml_file import *
from sqlite_update import *
from time_hora import *
from consulte_csv import *
from csv_handler import *
import subprocess
import time
import logging
import base64
from fix_cupom_protocol import *


log_filename = 'integracao.log'
logging.basicConfig(filename=log_filename, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

arquivo_file = "file_csv/bkoffice.csv"
logging.info("Start Componente")

from datetime import datetime

caminho_databases = get_system_version()
if caminho_databases == "/home/administrador/mwpos_server":
    acesso_fiscal = r"""{}/data/server/databases/fiscal_persistcomp.db""".format(caminho_databases)
    file_store = r"""{}/data/server/bundles/storecfg/loader.cfg""".format(caminho_databases)
    localizar_xml = r"{}/bin/".format(caminho_databases)
    acesso_orders = r"""{}/data/server/databases/order.db""".format(caminho_databases)
    Erro = ["Erros", "Enviados/2025/12/"]
    local_fix = "fix_venda/"
    local_fix_data = "{}/data/server/bundles/bkofficeuploader/python/repository".format(caminho_databases)
elif caminho_databases == "C:\edeployPOS":
    acesso_fiscal = r"""{}\data\server\databases\fiscal_persistcomp.db""".format(caminho_databases)
    file_store = r"""{}\data\server\bundles\storecfg\loader.cfg""".format(caminho_databases)
    localizar_xml = r"{}\bin".format(caminho_databases)
    acesso_orders = r"""{}\data\server\databases\order.db""".format(caminho_databases)
    Erro = ["\Erros", r"\Enviados\2026"]
    acesso_orders_tbl = r"""{}\data\server\databases\tblservice.db""".format(caminho_databases)
    # local_fix = "fix_venda/"
    # local_fix_data = "{}/data/server/bundles/bkofficeuploader/python/repository".format(caminho_databeses)
elif caminho_databases == "C:\edeploy-pos-structure":
    acesso_fiscal = r"""{}\data\server\databases\fiscal_persistcomp.db""".format(caminho_databases)
    file_store = r"""{}\data\server\bundles\storecfg\loader.cfg""".format(caminho_databases)
    localizar_xml = r"{}\bin".format(caminho_databases)
    acesso_orders = r"""{}\data\server\databases\order.db""".format(caminho_databases)
    Erro = ["\Erros", r"\Enviados\2026"]
    acesso_orders_tbl = r"""{}\data\server\databases\tblservice.db""".format(caminho_databases)
    # local_fix = "fix_venda/"
    # local_fix_data = "{}/data/server/bundles/bkofficeuploader/python/repository".format(caminho_databeses)



def main():
    logging.info("Validando as informações do arquivo bkoffice.csv")
    arquivo_csv = ler_arquivo_csv(arquivo_file)
    retorno_archilo = file_valor_sentto(arquivo_csv)
    xml_bin = []
    nota_csv = []

    for store_cfg in retorno_archilo:
        xml_numero = {
                    "numero_nota": store_cfg.get("numeronota")
        }
        nota_csv.append(xml_numero)
        for past_erro in Erro:
            localizar_xml_erros = r"{}{}".format(localizar_xml, past_erro)
            xml_enviado = get_xmls_list(localizar_xml_erros)
            for x in xml_enviado:
                if x:
                    xml_erro = x.split("/")[-1].split(".")[0] + ".xml"
                    list_nota = xml_erro.split("_")
                    try:
                        if list_nota[3] not in ("procInutNfe.xml", "cancelamento"):
                            # with open(x, 'rb') as f:
                            #     xml_content = f.read()
                            #     xml_encoded = base64.b64encode(xml_content)
                            numero_nota = int(list_nota[1])
                            if len(str(numero_nota)) > 4:
                                dict_erro = {"numero_nota": int(list_nota[1]),
                                                         "orderid": int(list_nota[2]),
                                                         "posid": int(list_nota[5].replace("pos", "")) if list_nota[4] in ("proc") else int(list_nota[4].replace("pos", ""))
                                                         }
                                xml_bin.append(dict_erro)
                    except Exception as ex:
                           pass
    try:
        notas_processadas = set()
        notas_com_correcoes = set()
        notas_rastreadas = {}
        xml_not_localizado = None
        for xml_file in xml_bin:
            if xml_file:
                num_nota_erro = xml_file.get("numero_nota")
                if num_nota_erro not in notas_processadas:
                    for nota in nota_csv:
                        if str(num_nota_erro) in nota.get("numero_nota"):
                            notas_processadas.add(num_nota_erro)
                            numero_nota_str = str(num_nota_erro)
                            xml_not_localizado = "Identificado_xml"
                            file_orders = "{}{}".format(acesso_orders, xml_file.get("posid"))
                            consult_order = connect_order_state(file_orders, xml_file.get("orderid"))
                            
                            correcao_aplicada = "Sem correção"
                            detalhes_correcao = ""
                            
                            if consult_order:
                                order_statr = time_direction(consult_order, xml_file.get("orderid"), file_orders, nota.get("numero_nota"), xml_file.get("posid"))
                                correcao_aplicada = mapear_correcao(order_statr)
                                
                                if order_statr == 5:
                                    notas_com_correcoes.add(num_nota_erro)
                                    detalhes_correcao = "Resend executado"
                                    StandAlone(xml_file.get("orderid"))
                                if order_statr == -1:
                                    xml_not_localizado = -1
                                    detalhes_correcao = "Reprocessamento SEFAZ"
                                if order_statr is not None:
                                    notas_com_correcoes.add(num_nota_erro)
                            else:
                                logging.info("Vendas não identificadas no order {}, {}, {}, vamos procurar no backup".format(xml_file.get("orderid"), nota.get("numero_nota"), xml_file.get("posid")))
                                for file_databases in not_order_picture():
                                    consult_order = connect_order_state(file_databases, xml_file.get("orderid"))
                                    if consult_order:
                                        order_state = time_direction(consult_order, xml_file.get("orderid"), file_databases, nota.get("numero_nota"), xml_file.get("posid"))
                                        correcao_aplicada = mapear_correcao(order_state)
                                        
                                        if order_state == 5:
                                            notas_com_correcoes.add(num_nota_erro)
                                            detalhes_correcao = "Resend com backup DB"
                                            insert_db(file_databases, file_orders, xml_file.get("orderid"))
                                            logging.info("Inserido vendas no banco atual {}, {}, {}".format(xml_file.get("orderid"), nota.get("numero_nota"), xml_file.get("posid")))
                                            StandAlone(xml_file.get("orderid"))
                                        if order_state is not None:
                                            notas_com_correcoes.add(num_nota_erro)
                            
                            notas_rastreadas[numero_nota_str] = {
                                "correcao": correcao_aplicada,
                                "detalhes": detalhes_correcao,
                                "data_processamento": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
        
        # Log de notas sem correções encontradas
        notas_sem_correcoes = notas_processadas - notas_com_correcoes
        if notas_sem_correcoes:
            logging.warning(f"Notas processadas sem correções encontradas: {sorted(notas_sem_correcoes)}")
        
        # Atualizar CSV com correções aplicadas
        try:
            atualizar_csv_com_correções(arquivo_file, notas_rastreadas)
            logging.info("CSV atualizado com informações de correções")
        except Exception as e:
            logging.error(f"Erro ao atualizar CSV de resultado: {e}")
        
        return xml_not_localizado, nota_csv
    except Exception as ex:
        logging.error(f"Erro crítico em main: {ex}", exc_info=True)
        raise


not_bin, note_number = main()
try:
    if not_bin in (-1, "Identificado_xml"):
        try:
            execution = FixingCstatCupons()
            if execution.mbcontext is not None:
                execution.process_resign()
                execution.process_unused_orders()
            else:
                logging.warning(
                    "Funcionalidades avançadas indisponíveis: msgbus não encontrado. "
                    "Script continuará com correções básicas apenas."
                )
        except Exception as ex:
            logging.error(
                f"Erro ao processar executores avançados: {ex}. "
                "Script continuará com correções básicas apenas.",
                exc_info=True
            )

    if not_bin is None:
        logging.info("Não identificado xml para a venda, vamos procurar no order's.")
        for note in note_number:
            nota = note.get("numero_nota")
            find_fiscal_id(nota)
except Exception as ex:
    logging.error(f"Erro crítico ao processar: {ex}", exc_info=True)
print("Finish Componente")
logging.info("Finish Componente")

