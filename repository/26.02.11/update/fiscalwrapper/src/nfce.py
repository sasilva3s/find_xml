# -*- coding: utf-8 -*-
import pytz
# noinspection PyUnresolvedReferences
from lxml import etree as lxml_etree
import base64
import glob
import logging
import os
import time
from datetime import datetime, timedelta
from threading import Thread, Condition, Lock
from xml.etree import cElementTree as eTree
import iso8601
from common import FiscalParameterController
from comp_exceptions import FiscalValidation
from dateutil import tz
from fiscalinterface import FiscalProcessor
from fiscalpersistence import (
    FiscalDataRepository,
    Order,
)
from old_helper import convert_from_localtime_to_utc, OrderTaker, remove_xml_namespace
from repository import JanitorRepository
from services import (
    FiscalOrderValidationService,
    OrderService,
    FiscalValidationService,
)
from msgbus import MBEasyContext
from nfcebuilder import NfeBuilder, ContextKeys
from nfcebuilder.nfceutil import NfceRequest
from pos_model import OrderParser
from pos_util import SaleLineUtil
from helper import remove_accents
from requests import ConnectionError, Timeout, Response
from typing import (
    Optional,
    Tuple,
    List,
    Dict,
)
from adapter.sqlite_adapter import Driver as DBDriver

from models import NfModel

INF_NFE = ".//infNFe"
NFE_PROC_POS = "{0}_{1}_{2}_nfe_proc_pos{3}_{4}.xml"
NFCE_WT_RESP = "<nfeProc versao=\"{0:.2f}\" xmlns=\"{1}\">{2}<protNFe versao=\"{0:.2f}\">{3}</protNFe></nfeProc>"

CSTAT_539_DUP_DIFF_KEY = -6

loggerThread = logging.getLogger("FiscalWrapperThread")
logger = logging.getLogger("FiscalWrapper")
loggerFiscalXml = logging.getLogger("FiscalWrapperXmlLog")


class NfceRequestBuilder:
    NAMESPACE_NFE = "http://www.portalfiscal.inf.br/nfe"
    NAMESPACE_SOAP = "http://www.w3.org/2003/05/soap-envelope"
    NAMESPACE_AUTORIZACAO = "http://www.portalfiscal.inf.br/nfe/wsdl/NfeAutorizacao"
    NAMESPACE_AUTORIZACAO_4 = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4"
    NAMESPACE_RET_AUTORIZACAO_4 = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeRetAutorizacao4"
    NAMESPACE_CONSULTA_SITUACAO = "http://www.portalfiscal.inf.br/nfe/wsdl/NfeConsulta2"
    NAMESPACE_CONSULTA_SITUACAO_3 = "http://www.portalfiscal.inf.br/nfe/wsdl/NfeConsulta3"
    NAMESPACE_CONSULTA_SITUACAO_4 = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo4"

    VOIDED = 4
    PAID = 5

    def __init__(
        self,                           # type: NfceRequestBuilder
        mbcontext,                      # type: MBEasyContext
        initial_order_id,               # type: int
        crt,                            # type: int
        cnpj_contribuinte,              # type: unicode
        inscr_estadual,                 # type: unicode
        c_uf,                           # type: unicode
        uf,                             # type: str
        c_mun_fg,                       # type: unicode
        mod,                            # type: unicode
        serie,                          # type: str
        nfe_serie,                      # type: str
        ambiente,                       # type: unicode
        end_logradouro,                 # type: str
        end_numero,                     # type: str
        end_compl,                      # type: str
        bairro,                         # type: str
        municipio,                      # type: str
        cep,                            # type: str
        nome_emit,                      # type: str
        csc,                            # type: unicode
        cid_token,                      # type: unicode
        qrcode_base_url,                # type: str
        certificate_key_path,           # type: str
        certificate_path,               # type: unicode
        order_parser,                   # type: OrderParser
        fiscal_parameter_controller,    # type: FiscalParameterController
        sale_line_util,                 # type: SaleLineUtil
        nfe_builder,                    # type: NfeBuilder
        versao_ws,                      # type: int
        synchronous_mode,               # type: bool
        max_items_to_fiscalization,     # type: int
        is_new_nfce_schema_enabled,     # type: bool
    ):
        # type: (...) -> None

        self.mbcontext = mbcontext
        self.initial_order_id = initial_order_id
        self.crt = crt
        self.cnpj_contribuinte = cnpj_contribuinte
        self.inscr_estadual = inscr_estadual
        self.c_uf = c_uf
        self.uf = uf
        self.c_mun_fg = c_mun_fg
        self.mod = mod
        self.serie = serie
        self.nfe_serie = nfe_serie
        self.ambiente = ambiente
        self.end_logradouro = end_logradouro
        self.end_numero = end_numero
        self.end_compl = end_compl
        self.bairro = bairro
        self.municipio = municipio
        self.cep = cep
        self.nome_emit = nome_emit
        self.csc = csc
        self.cid_token = cid_token
        self.qrcode_base_url = qrcode_base_url
        self.certificate_key = open(certificate_key_path, "rb").read().replace("\r\n", "\n")
        self.certificate_cert = open(certificate_path, "rb").read().replace("\r\n", "\n")
        self.order_parser = order_parser  # type: OrderParser
        self.fiscal_parameter_controller = fiscal_parameter_controller  # type: FiscalParameterController
        self.sale_line_util = sale_line_util  # type: SaleLineUtil
        self.nfe_builder = nfe_builder  # type: NfeBuilder
        self.versao_ws = versao_ws  # type: int
        self.synchronous_mode = synchronous_mode
        self.fiscal_order_validation = FiscalOrderValidationService(
            max_items_to_fiscalization=max_items_to_fiscalization,
        )
        self.is_new_nfce_schema_enabled = is_new_nfce_schema_enabled

    def build_request(self, order, contingencia, dh_contingencia, just_contingencia):
        nfe, data_emissao, serie_nota, numero_nota, nf_model = self._build_nfe(
            order_xml=order,
            contingencia=contingencia,
            dh_contingencia=dh_contingencia,
            just_contingencia=just_contingencia,
        )

        envi_nfe = """<enviNFe versao="%.2f" xmlns="%s"><idLote>%s</idLote><indSinc>%s</indSinc>%s</enviNFe>""" % (
            3.1 if self.versao_ws in (1, 3) else 4,
            NfceRequestBuilder.NAMESPACE_NFE,
            int(round(time.time() * 1000)),
            "1" if self.synchronous_mode else "0",
            nfe)
        if self.versao_ws in (1, 3):
            envelopado = self.envelopa(
                request=envi_nfe,
                namespace="http://www.portalfiscal.inf.br/nfe/wsdl/NfeAutorizacao",
                c_uf=self.c_uf
            )
        else:
            envelopado = self.envelopa(
                request=envi_nfe,
                namespace=self.NAMESPACE_AUTORIZACAO_4,
                c_uf=self.c_uf,
                versao_ws=float(self.versao_ws)
            )
        return envelopado, data_emissao, serie_nota, numero_nota, nf_model

    def build_consulta(self, recibo):
        request = "<consReciNFe versao=\"%.2f\" xmlns=\"%s\"><tpAmb>%s</tpAmb><nRec>%s</nRec></consReciNFe>" % (
            3.1 if self.versao_ws in (1, 3) else 4,
            NfceRequestBuilder.NAMESPACE_NFE,
            self.ambiente,
            recibo)
        if self.versao_ws in (1, 3):
            envelopado = self.envelopa(
                request=request,
                namespace="http://www.portalfiscal.inf.br/nfe/wsdl/NfeRetAutorizacao",
                c_uf=self.c_uf
            )
        else:
            envelopado = self.envelopa(
                request=request,
                namespace=self.NAMESPACE_RET_AUTORIZACAO_4,
                c_uf=self.c_uf
            )
        return envelopado

    def envelopa_lote(self, xmls):
        envi_nfe = """<enviNFe versao="%.2f" xmlns="%s"><idLote>%s</idLote><indSinc>%s</indSinc>%s</enviNFe>""" % (
            3.1 if self.versao_ws in (1, 3) else 4,
            NfceRequestBuilder.NAMESPACE_NFE,
            int(round(time.time() * 1000)),
            "1" if self.synchronous_mode else "0",
            xmls)
        if self.versao_ws in (1, 3):
            envelopado = self.envelopa(
                request=envi_nfe,
                namespace="http://www.portalfiscal.inf.br/nfe/wsdl/NfeAutorizacao",
                c_uf=self.c_uf
            )
        else:
            envelopado = self.envelopa(
                request=envi_nfe,
                namespace=self.NAMESPACE_AUTORIZACAO_4,
                c_uf=self.c_uf,
                versao_ws=float(self.versao_ws)
            )
        return envelopado

    def _has_contribuinte_csosn(
        self,
        order,    # type: Order
    ):
        # type: (Order) -> bool

        for sale_item in order.sale_items:
            csosn = self.fiscal_parameter_controller.get_optional_parameter(
                part_code=sale_item.part_code,
                parameter_name="CSOSN",
            )
            if csosn is not None and int(csosn) in (101, 201, 202, 203):
                return True

        return False

    def _build_nfe(
        self,                   # type: NfceRequestBuilder
        order_xml,              # type: eTree.ElementTree
        contingencia,           # type: bool
        dh_contingencia,        # type: Optional[datetime]
        just_contingencia,      # type: Optional[str]
    ):
        # type: (...) -> (str, str, unicode, int)

        try:
            order_xml = order_xml.find("Order") if order_xml.find("Order") else order_xml
            order = self.order_parser.parse_order_grouping_lines(
                order_xml=order_xml,
            )
        except Exception as ex:
            raise FiscalBuildException("Error parsing order: {}".format(ex.message))

        self.fiscal_order_validation.validate_qty_sale_items(order=order)

        context = {
            ContextKeys.is_in_contingency: contingencia,
            ContextKeys.contingency_datetime: dh_contingencia,
            ContextKeys.contingency_reason: just_contingencia,
            ContextKeys.should_use_new_xml_schema: self.is_new_nfce_schema_enabled,
            ContextKeys.csosn_dest_contribuinte: self._has_contribuinte_csosn(order=order),
        }
        xml = self.nfe_builder.build_xml(
            order=order,
            context=context,
        )

        order_id = str(order.order_id).zfill(9)
        finally_nfce_key = context.get("nfce_key")[30:]
        value = context.get("total_prod")
        logger.info("OrderId {} final {}. Valor {}".format(order_id, finally_nfce_key, value))

        data_emissao = context.get(ContextKeys.data_emissao)
        nf_model = int(context.get(ContextKeys.nf_model))
        serie_nota = self.nfe_serie if nf_model == NfModel.NFE.value else self.serie
        numero_nota = context.get(ContextKeys.fiscal_number)
        nf_model = context.get(ContextKeys.nf_model)

        return xml, data_emissao, serie_nota, numero_nota, nf_model

    @staticmethod
    def formata_data(data):
        # type: (datetime) -> str
        local_zone = tz.tzlocal()
        data = data.replace(tzinfo=local_zone)
        data_str = data.strftime("%Y-%m-%dT%H:%M:%S%z")
        data_str = data_str[:22] + ":" + data_str[22:]
        return data_str

    @staticmethod
    def envelopa(request, namespace, c_uf, versao_ws=3.1):
        prefix = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Envelope xmlns=\"{0:s}\"><Header>"\
            "<nfeCabecMsg xmlns=\"{1:s}\"><cUF>{2:s}</cUF><versaoDados>{3:.2f}" \
            "</versaoDados></nfeCabecMsg></Header><Body><nfeDadosMsg xmlns=\"{4:s}\">".format(
                NfceRequestBuilder.NAMESPACE_SOAP, namespace, c_uf, float(versao_ws), namespace
            )
        suffix = "</nfeDadosMsg></Body></Envelope>"
        return prefix + request + suffix


class NfceProcessor(FiscalProcessor):

    _MIN_CONSULTATION_INTERVAL_SECONDS = 300
    # Sentinel gravado em SentToNfce para notas de dia anterior que receberam cStat 217
    # (NFC-e nao encontrada na SEFAZ). Ignoradas nos loops de retry para evitar escalada
    # para cStat 656. Notas do mesmo dia sao reenfileiradas automaticamente para re-emissao.
    CSTAT_217_NOT_IN_SEFAZ = -5
    REQUEUE_WINDOW_HOURS = 22

    def __init__(
        self,                       # type: NfceProcessor
        mbcontext,                  # type: MBEasyContext
        nfce_request_builder,       # type: NfceRequestBuilder
        nfce_request,               # type: NfceRequest
        nfce_autorizador,           # type: NfceAutorizador
        nfce_contingencia,          # type: NfceContingencia
        url_sefaz,                  # type: str
        fiscal_sent_dir,            # type: str
        versao_ws,                  # type: int
        synchronous_mode,           # type: bool
        nfce_situation_checker,     # type: NfceSituationChecker
        xml_enviados,               # type: str
        fiscal_validation_service,  # type: FiscalValidationService
        janitor_repository,         # type: JanitorRepository
    ):
        # type: (...) -> None
        super(NfceProcessor, self).__init__(mbcontext)

        self.mbcontext = mbcontext
        self.nfce_request_builder = nfce_request_builder
        self.nfce_request = nfce_request
        self.nfce_autorizador = nfce_autorizador
        self.nfce_contingencia = nfce_contingencia
        self.fiscal_sent_dir = fiscal_sent_dir
        self.url_sefaz = url_sefaz
        self.versao_ws = versao_ws
        self.synchronous_mode = synchronous_mode
        self.nfce_situation_checker = nfce_situation_checker
        self.xml_enviados = xml_enviados
        self.fiscal_validation_service = fiscal_validation_service
        self.janitor_repository = janitor_repository
        self._order_last_consultation = {}  # type: Dict[str, datetime]

    def request_fiscal(self, posid, order, tenders, paf=False, fiscal_id=None):
        if self.nfce_contingencia:
            contingencia, dh_contingencia, just_contingencia = self.nfce_contingencia.is_contingencia()
        else:
            contingencia, dh_contingencia, just_contingencia = False, None, None

        order_id = order.get("orderId")
        order_id = order_id.zfill(9)

        message = "Gerando XML request para FiscalId: [{}] - OrderId: [{}]"
        logger.info(message.format(str(fiscal_id), str(order_id)))

        request, data_emissao, serie_nota, numero_nota, nf_model, = self.nfce_request_builder.build_request(
            order=order,
            contingencia=contingencia,
            dh_contingencia=dh_contingencia,
            just_contingencia=just_contingencia,
        )  # type: str

        serie_nota = str(serie_nota).zfill(3)
        numero_nota = str(numero_nota).zfill(9)
        dir_arquivo = os.path.join(data_emissao[0:4], data_emissao[5:7], data_emissao[8:10])

        request_xml = self._get_xml_request(request=request)
        log_message = "Nova order para processamento - FiscalId: [{}] - OrderId: [{}]"
        log_message = log_message.format(str(fiscal_id), str(order_id))
        logger.info(log_message)

        index1 = request.index("Id=\"NFe")
        nfe_key = request[index1 + 4:index1 + 51]

        response_consulta = None
        dir_enviados = os.path.join(self.fiscal_sent_dir, "Enviados", dir_arquivo)
        nfe_proc = None

        if not contingencia and self.nfce_autorizador:
            request_file = None
            if not os.path.exists(dir_enviados):
                os.makedirs(dir_enviados)

            try:
                logger.info("Consultando SEFAZ para autorizar Order: %s" % order_id)
                self.nfce_autorizador.logger = logger
                request, response_consulta, _ = self.nfce_autorizador.autoriza_notas(
                    request=request,
                    nf_model=nf_model,
                )

                logger.info("Resposta da SEFAZ recebida para Order: %s" % order_id)
                logger.info("Order {} Enviada com a chave {}".format(order_id.zfill(9), nfe_key))
            except (ConnectionError, Timeout) as e:
                logger.warning("Entrando em contingencia. Motivo: %s", e)
                request, request_file, request_xml = self._active_contingency(
                    order=order,
                    request_xml=request_xml,
                    order_id=order_id,
                    pos_id=posid,
                )

            except Exception as ex:
                error_response_xml = ""
                if isinstance(ex, FiscalException):
                    error_response_xml = ex.response_xml

                logger.exception("Erro tratando NFCE")

                dir_erro = os.path.join(self.fiscal_sent_dir, "Erros")
                if not os.path.exists(dir_erro):
                    os.makedirs(dir_erro)

                if "protNFe" in error_response_xml:
                    protocol_xml = self._get_protocol_nfe(response_xml=error_response_xml)
                    request_xml = self._add_protocol_to_request_xml(
                        request_xml=request_xml,
                        protocol_xml=protocol_xml,
                    )

                file_name = "{0}_{1}_{2}_request_pos{3}_{4}.xml"
                file_name = file_name.format(serie_nota, numero_nota, order_id, str(posid).zfill(2), nfe_key)
                request_file = open(os.path.join(dir_erro, file_name), "w+")
                raise ex

            finally:
                try:
                    if request_file:
                        request_file.write(request_xml)
                        request_file.close()
                except Exception as ex:
                    error_message = "Erro salvando arquivo request pos: %s, orderid: %s, ex: %s"
                    logger.exception(error_message, str(posid), order.get("orderId"), ex)

                if response_consulta is not None:
                    if self.versao_ws == 4:
                        signature_namespace = 'http://www.w3.org/2000/09/xmldsig#'
                        xml_namespaces = lxml_etree.fromstring(response_consulta).iter().next().nsmap
                        for namespace in xml_namespaces:
                            if signature_namespace == xml_namespaces[namespace]:
                                response_consulta = response_consulta.replace(namespace + ":", "")
                                response_consulta = response_consulta.replace(
                                    "<Signature>",
                                    "<Signature xmlns=\"{}\">".format(signature_namespace)
                                )

                    try:
                        nfe_proc, response_consulta = self._get_search_response(
                            order_xml=order,
                            request_xml=request_xml,
                            response_xml=response_consulta,
                        )
                        file_name = NFE_PROC_POS.format(
                            serie_nota,
                            numero_nota,
                            order_id,
                            str(posid).zfill(2),
                            nfe_key
                        )
                        with open(os.path.join(dir_enviados, file_name), "w+") as nfe_proc_file:
                            nfe_proc_file.write(nfe_proc)
                    except (Timeout, ConnectionError) as e:
                        logger.warning("Entrando em contingencia. Motivo: %s", e)
                        request, request_file, request_xml = self._active_contingency(
                            order=order,
                            request_xml=request_xml,
                            order_id=order_id,
                            pos_id=posid,
                        )
                        if request_file:
                            request_file.write(request_xml)
                            request_file.close()
                    except (Exception,):
                        file_name = "{0}_{1}_{2}_response_pos{3}_{4}.xml"
                        file_name = file_name.format(
                            serie_nota,
                            numero_nota,
                            order_id,
                            str(posid).zfill(2),
                            nfe_key
                        )
                        with open(os.path.join(dir_enviados, file_name), "w+") as response_file:
                            response_file.write(response_consulta)

                        log_message = "Error finalizing request fiscal. OrderId: [{}] - ResponseXML: [{}]"
                        xml_base64 = base64.b64encode(response_consulta)
                        log_message = log_message.format(order_id, xml_base64)
                        loggerFiscalXml.info(log_message)

                        if nfe_proc is None:
                            self._verify_nfce_response(response=response_consulta)
        else:
            self._log_original_request(
                order=order,
                request_xml=request_xml,
            )

            request, data_emissao, serie_nota, numero_nota, nf_model = self.nfce_request_builder.build_request(
                order=order,
                contingencia=contingencia,
                dh_contingencia=dh_contingencia,
                just_contingencia=just_contingencia,
            )  # type: str
            serie_nota = str(serie_nota).zfill(3)
            numero_nota = str(numero_nota).zfill(9)
            dir_arquivo = os.path.join(data_emissao[0:4], data_emissao[5:7], data_emissao[8:10])
            dir_enviados = os.path.join(self.fiscal_sent_dir, "Enviados", dir_arquivo)

            if not os.path.exists(dir_enviados):
                os.makedirs(dir_enviados)

            file_name = "{0}_{1}_{2}_request_pos{3}_{4}_contingencia.xml"
            originator_pos_id = str(int(order.get("originatorId")[3:])).zfill(2)
            file_name = file_name.format(serie_nota, numero_nota, order_id, originator_pos_id, nfe_key)
            with open(os.path.join(dir_enviados, file_name), "w+") as request_file:
                request_file.write(request_xml)

        emissao_date = convert_from_localtime_to_utc(iso8601.parse_date(data_emissao))

        self.order_service.set_custom_property(
            pos_id=posid,
            key="FISCALIZATION_DATE",
            value=emissao_date.strftime("%Y-%m-%dT%H:%M:%S"),
            order_id=order_id,
            blk_notify=True,
        )

        return request, response_consulta, nfe_proc

    def _active_contingency(
        self,           # type: NfceProcessor
        order,          # type: eTree.Element
        request_xml,    # type: str
        order_id,       # type: str
        pos_id,         # type: str
    ):
        # type: (...) -> Tuple[str, file, str]

        self._log_original_request(
            order=order,
            request_xml=request_xml,
        )

        justificativa = "Problemas de conexao com a SEFAZ"
        contingencia, dh_contingencia, just_contingencia = self.nfce_contingencia.entra_contingencia(
            justificativa=justificativa,
        )
        request, data_emissao, serie_nota, numero_nota, _ = self.nfce_request_builder.build_request(
            order=order,
            contingencia=contingencia,
            dh_contingencia=dh_contingencia,
            just_contingencia=just_contingencia,
        )  # type: str

        index1 = request.index("<NFe")
        index2 = request.index("</NFe>")
        request_xml = request[index1:index2 + 6]

        index1 = request.index("Id=\"NFe")
        nfe_key = request[index1 + 4:index1 + 51]

        serie_nota = str(serie_nota).zfill(3)
        numero_nota = str(numero_nota).zfill(9)
        dir_arquivo = os.path.join(data_emissao[0:4], data_emissao[5:7], data_emissao[8:10])
        dir_enviados = os.path.join(self.fiscal_sent_dir, "Enviados", dir_arquivo)
        if not os.path.exists(dir_enviados):
            os.makedirs(dir_enviados)

        file_name = "{0}_{1}_{2}_request_pos{3}_{4}_contingencia.xml"
        file_name = file_name.format(serie_nota, numero_nota, order_id, str(pos_id).zfill(2), nfe_key)
        request_file = open(os.path.join(dir_enviados, file_name), "w+")

        return request, request_file, request_xml

    def check_nf_situation(
        self,           # type: NfceProcessor
        initial_date,   # type: str
        final_date,     # type: str
    ):
        # type: (...) -> (bool, str)

        initial_date = initial_date[:4] + '-' + initial_date[4:6] + '-' + initial_date[6:]
        final_date = final_date[:4] + '-' + final_date[4:6] + '-' + final_date[6:]

        with FiscalDataRepository(self.mbcontext) as fiscal_repository:
            logger.info("Tratando Evento Consultar Situacao XML")
            conn = None
            try:
                conn = DBDriver().open()
                offset = 0
                while True:
                    sql = """SELECT PosId, OrderId, XMLRequest, SentToNfce, OrderPicture, DataNota
                              FROM FiscalData
                              WHERE date(DataNota, 'unixepoch', 'localtime') >= '%s'
                              AND date(DataNota, 'unixepoch', 'localtime') <= '%s'
                              AND SentToNfce <> 0
                              ORDER BY OrderId ASC LIMIT 100 OFFSET %s""" % (initial_date, final_date, offset)
                    cursor = conn.select(sql)
                    if cursor.rows() == 0:
                        break
                    offset += cursor.rows()
                    eTree.register_namespace('', NfceRequestBuilder.NAMESPACE_NFE)

                    self._iterate_in_orders_to_check_nfe_situation(
                        cursor=cursor,
                        fiscal_repository=fiscal_repository,
                    )

            except (Exception,):
                logger.exception("Erro ao Consultar Status dos XMLs")
                return False, "Erro ao Consultar Status dos XMLs"
            finally:
                if conn:
                    conn.close()

        return True, "Status Salvos com Sucesso"

    def _iterate_in_orders_to_check_nfe_situation(
        self,               # type: NfceProcessor
        cursor,             # type: persistence.Cursor
        fiscal_repository,  # type: FiscalDataRepository
    ):
        # type: (...) -> None

        for row in cursor:
            row_entry = map(row.get_entry, ("PosId", "OrderId", "XMLRequest", "SentToNfce", "OrderPicture", "DataNota"))
            pos_id, order_id, request, sent_to_nfce, order_picture, data_nota = row_entry

            try:
                sent_to_nfce_int = int(sent_to_nfce)
            except (ValueError, TypeError):
                logger.warning("SentToNfce invalido para order %s: %r", order_id, sent_to_nfce)
                continue

            if sent_to_nfce_int in (self.CSTAT_217_NOT_IN_SEFAZ, CSTAT_539_DUP_DIFF_KEY):
                continue

            request_str = base64.b64decode(request + "=" * ((4 - len(request) % 4) % 4))
            req = eTree.XML(request_str)

            need_to_change_cstat_situation = self._need_to_change_cstat_situation(req=req)
            if not need_to_change_cstat_situation:
                self._update_cstat_if_not_success_in_db(
                    sent_to_nfce=sent_to_nfce_int,
                    fiscal_repository=fiscal_repository,
                    order_id=order_id,
                )
                continue

            logger.info("Need to change situation from order_id {}".format(order_id))
            is_on_cooldown = self._is_on_consultation_cooldown(order_id=order_id)
            if is_on_cooldown:
                continue

            try:
                order_xml = base64.b64decode(order_picture + "=" * ((4 - len(order_picture) % 4) % 4))
                order_xml = eTree.XML(order_xml)
                order_xml = order_xml.find("Order") if order_xml.find("Order") else order_xml

                new_req, fiscal_data_xml = self._get_new_request_situation(
                    order_xml=order_xml,
                    request_str=request_str,
                    fiscal_repository=fiscal_repository,
                    data_nota=data_nota,
                    pos_id=pos_id,
                )

                if not fiscal_data_xml:
                    continue

                self._update_order_situation(
                    pos_id=pos_id,
                    order_id=order_id,
                    fiscal_repository=fiscal_repository,
                    new_req=new_req,
                    new_response=eTree.tostring(fiscal_data_xml),
                )
            except (Exception,):
                logger.exception("Erro ao buscar protocolo da order %s" % order_id)
                continue

    def _is_on_consultation_cooldown(
        self,       # type: NfceProcessor
        order_id,   # type: str
    ):
        # type: (...) -> bool

        now = datetime.now()
        last_consulted = self._order_last_consultation.get(order_id)
        elapsed_seconds = (now - last_consulted).total_seconds() if last_consulted else None
        is_too_recent = elapsed_seconds is not None and elapsed_seconds < self._MIN_CONSULTATION_INTERVAL_SECONDS
        if is_too_recent:
            logger.warning(
                "Order %s consultada ha %.0fs. Ignorando para evitar consumo indevido (NT 2018/002)." % (
                    order_id,
                    elapsed_seconds,
                )
            )
            return True

        self._order_last_consultation[order_id] = now
        return False

    @staticmethod
    def _get_xml_request(
        request,    # type: str
    ):
        # type: (...) -> str

        index1 = request.index("<NFe")
        index2 = request.index("</NFe>")

        return request[index1:index2 + 6]

    @staticmethod
    def _need_to_change_cstat_situation(
        req,        # type: eTree.Element
    ):
        # type: (...) -> bool

        c_stat_path = "{{{0}}}protNFe/{{{0}}}infProt/{{{0}}}cStat"
        c_stat = req.find(c_stat_path.format(NfceRequestBuilder.NAMESPACE_NFE))
        if c_stat is not None:
            c_stat = c_stat.text

        if c_stat in ("100", "150"):
            return False

        return True

    @staticmethod
    def _update_cstat_if_not_success_in_db(
        sent_to_nfce,       # type: int
        fiscal_repository,  # type: FiscalDataRepository
        order_id,           # type: str
    ):
        # type: (...) -> None

        if sent_to_nfce == 1:
            return

        else:
            logger.info(
                "Corrigindo SentToNfce to 1 (era %s) para order %s",
                sent_to_nfce,
                order_id
            )
            fiscal_repository.set_nfce_sent(
                order_id=order_id,
                status=1,
            )

    def _update_order_situation(
        self,               # type: NfceProcessor
        pos_id,             # type: str
        order_id,           # type: str
        fiscal_repository,  # type: FiscalDataRepository
        new_req,            # type: Optional[str]
        new_response,       # type: Optional[str]
    ):
        # type: (...) -> None

        if not new_req:
            logger.exception("Erro ao gerar XML da order %s" % order_id)
            fiscal_repository.set_nfce_sent(order_id, -1)
            return

        xml_base64 = base64.b64encode(new_req)
        fiscal_repository.set_nfce_sent_with_xml(order_id, xml_base64, 1)
        fiscal_repository.set_xml_response(
            order_id=order_id,
            xml_base64=base64.b64encode(new_response),
        )
        fiscal_repository.set_fiscal_xml_custom_property(
            pos_id=pos_id,
            order_id=order_id,
            xml_request=xml_base64,
            blk_notify=False,
        )
        new_req_xml = remove_xml_namespace(new_req)
        inf_nfe = new_req_xml.find(INF_NFE)
        data_emissao = inf_nfe.find(".//dhEmi").text
        serie_nota = new_req_xml.find(".//serie").text
        numero_nota = new_req_xml.find(".//nNF").text.zfill(9)
        nfe_key = inf_nfe.attrib["Id"][3:]
        dir_arquivo = os.path.join(data_emissao[0:4], data_emissao[5:7], data_emissao[8:10])
        dir_nota = os.path.join(self.fiscal_sent_dir, self.xml_enviados, dir_arquivo)
        self._create_directory_if_not_exists(dir_nota)
        nfce_file_name = NFE_PROC_POS.format(
            serie_nota, numero_nota, order_id.zfill(9), str(pos_id).zfill(2), nfe_key
        )

        for xml_file in glob.glob(dir_nota + '/*' + order_id.zfill(9) + '_nfe_proc_pos*'):
            os.remove(xml_file)

        file_path = os.path.join(dir_nota, nfce_file_name)
        with open(file_path, "w+") as nfe_proc_file:
            nfe_proc_file.write(new_req)

    def _get_new_request_situation(
        self,                                # type: NfceProcessor
        order_xml,                           # type: eTree.Element
        request_str,                         # type: str
        fiscal_repository=None,              # type: Optional[FiscalDataRepository]
        c_stat_from_search_response=None,    # type: str
        data_nota=None,                      # type: Optional[str]
        pos_id=None,                         # type: Optional[str]
    ):
        # type: (...) -> Tuple[Optional[str], Optional[eTree.Element]]

        order_id = order_xml.get("orderId")
        logger.info("Buscando protocolo para atualizar order %s" % order_id)

        req = remove_xml_namespace(request_str)
        inf_nfe = req.find(INF_NFE)
        nfe_key = inf_nfe.attrib["Id"][3:]

        c_stat, fiscal_data_xml = self._get_c_stat_from_check_situation(
            chave_xml=nfe_key,
            order_id=order_id,
            c_stat_from_search_response=c_stat_from_search_response,
        )
        if c_stat in ("100", "150", "613", "539"):  # 100 = ok; 613 = Chave de Acesso difere da existente em BD;
            if c_stat in ("613", "539"):
                try:
                    logger.info("Tratando 613/539 para order %s" % order_id)
                    request = self.nfce_request_builder.build_request(
                        order=order_xml,
                        contingencia=False,
                        dh_contingencia=None,
                        just_contingencia=None,
                    )
                    envelopado = request[0]
                    logger.info("XML Regerado. Order %s " % order_id)
                    index1 = envelopado.index("<NFe")
                    index2 = envelopado.index("</NFe>")
                    new_req = envelopado[index1:index2 + 6]

                    nfe_start_tag = request_str[:request_str.index("<NFe ")]
                    nfe_end_tag = request_str[request_str.index("</NFe>") + 6:]
                    request_str = nfe_start_tag + new_req + nfe_end_tag
                    rebuilt_key = remove_xml_namespace(request_str).find(INF_NFE).attrib["Id"][3:]
                    c_stat, fiscal_data_xml = self._get_c_stat_from_check_situation(
                        chave_xml=rebuilt_key,
                        order_id=order_id,
                    )
                    if c_stat not in ("100", "150"):
                        raise FiscalValidation("Campo cStat diferente de 100/150")

                except (Exception,):
                    logger.exception("Erro ao tratar 613 da order %s" % order_id)
                    return None

            inf_prot_namespace = "/{{{2}}}retConsSitNFe/{{{2}}}protNFe/{{{2}}}infProt"
            inf_prot = fiscal_data_xml.find(
                (
                    "{{{0}}}Body/{{{1}}}" + "nfeResultMsg" + inf_prot_namespace).format(
                    NfceRequestBuilder.NAMESPACE_SOAP,
                    NfceRequestBuilder.NAMESPACE_CONSULTA_SITUACAO_4,
                    NfceRequestBuilder.NAMESPACE_NFE
                )
            )
            inf_prot.attrib.pop("Id", None)
            inf_prot_str = eTree.tostring(inf_prot).decode()
            ws_version = "4.00"
            index1 = request_str.index("<NFe")
            index2 = request_str.index("</NFe>")
            nfe_only = request_str[index1:index2 + 6]

            new_req = ''.join([
                '<nfeProc versao="%s" xmlns="http://www.portalfiscal.inf.br/nfe">' % ws_version,
                nfe_only,
                '<protNFe versao="%s">' % ws_version,
                inf_prot_str,
                '</protNFe></nfeProc>',
            ])

        elif c_stat == "217":
            self._handle_cstat_217(
                order_id=order_id,
                order_xml=order_xml,
                data_nota=data_nota,
                pos_id=pos_id,
                fiscal_repository=fiscal_repository,
            )
            return None, None

        elif c_stat == NfceStatus.PedidoConsultaDuplicado:
            logger.warning(
                "Order %s retornou cStat 562 - Pedido de consulta duplicado (NT 2018/002). "
                "Consulta suspensa para esta order." % order_id
            )
            return None, None

        else:
            raise FiscalValidation("Status diferente de 100/150. %s" % c_stat)

        request_xml = (new_req or request_str)
        return request_xml, fiscal_data_xml

    def _handle_cstat_217(
        self,               # type: NfceProcessor
        order_id,           # type: str
        order_xml,          # type: eTree.Element
        data_nota,          # type: Optional[str]
        pos_id,             # type: Optional[str]
        fiscal_repository,  # type: Optional[FiscalDataRepository]
    ):
        # type: (...) -> None

        state_id = order_xml.get("stateId")
        is_paid = state_id == str(NfceRequestBuilder.PAID)
        is_inside_window = self._is_order_inside_window(data_nota)
        was_canceled_after_paid = order_xml.find(".//OrderProperty[@key='CANCELED_AFTER_PAID']") is not None

        if is_paid and is_inside_window:
            logger.info(
                "Order %s nao encontrada na SEFAZ (cStat 217) — pedido pago, nota do mesmo dia. "
                "Reenfileirando para re-emissao." % order_id
            )
            fiscal_repository.set_nfce_sent(order_id, 0)
            return

        if is_paid and not was_canceled_after_paid:
            logger.info(
                "Order %s nao encontrada na SEFAZ (cStat 217) — pedido pago sem CANCELED_AFTER_PAID "
                "(venda em contingencia pendente de transmissao). Mantendo na fila de envio, sem inutilizar." % order_id
            )
            fiscal_repository.set_nfce_sent(order_id, 0)
            return

        logger.info(
            "Order %s nao encontrada na SEFAZ (cStat 217) — pedido %s cancelado/gap. "
            "Marcando como invalida e reenfileirando inutilizacao." % (order_id, state_id)
        )
        fiscal_repository.set_nfce_sent(order_id, self.CSTAT_217_NOT_IN_SEFAZ)
        self.order_service.set_custom_property(
            pos_id=pos_id,
            order_id=order_id,
            key="ORDER_DISABLED",
            value="false",
        )

    @staticmethod
    def _is_order_inside_window(
        data_nota,  # type: Optional[str]
    ):
        # type: (...) -> bool

        if data_nota is None:
            return False

        try:
            emission_dt = datetime.fromtimestamp(int(data_nota))
        except (ValueError, TypeError):
            return False

        window = timedelta(hours=NfceProcessor.REQUEUE_WINDOW_HOURS)
        elapsed = datetime.now() - emission_dt
        return elapsed <= window

    @staticmethod
    def _create_directory_if_not_exists(data_path):
        try:
            if not os.path.exists(data_path):
                os.makedirs(data_path)
        except OSError:
            logger.error("Cannot create path: {}".format(data_path))

    def _get_c_stat_from_check_situation(
        self,                                # type: NfceProcessor
        chave_xml,                           # type: str
        order_id,                            # type: str
        c_stat_from_search_response=None,    # type: str
    ):
        # type: (...) -> Tuple[str, eTree.Element]

        self.nfce_situation_checker.logger = logger
        fiscal_data = self.nfce_situation_checker.check_situation_nfe(chave_xml)
        if not fiscal_data:
            duplicate_status = [NfceStatus.DuplicidadeNfce, NfceStatus.DuplicidadeDiferencaChaveAcesso]
            if c_stat_from_search_response in duplicate_status:
                msg = "Erro ao verificar situação de nota processada com status de duplicidade"
                raise ConnectionError(msg)

            msg = "Problemas ao tentar consultar a situação da nota na sefaz. Vamos tentar novamente depois."
            raise FiscalValidation(msg)

        fiscal_data_xml = eTree.XML(fiscal_data.encode('utf-8'))
        c_stat = fiscal_data_xml.find(
            ("{{{0}}}Body/{{{1}}}" + "nfeResultMsg" + "/{{{2}}}retConsSitNFe/{{{2}}}cStat").format(
                NfceRequestBuilder.NAMESPACE_SOAP,
                NfceRequestBuilder.NAMESPACE_CONSULTA_SITUACAO_4,
                NfceRequestBuilder.NAMESPACE_NFE
            )
        )
        if c_stat is None:
            raise FiscalValidation("Campo cStat Nao Encontrado")
        else:
            c_stat = c_stat.text
        logger.info("Novo protocolo recebido para a order {}. cStat: {}".format(order_id, c_stat))
        return c_stat, fiscal_data_xml

    @staticmethod
    def _add_protocol_to_request_xml(
        request_xml,    # type: str
        protocol_xml,   # type: str
    ):
        # type: (...) -> str

        request_with_protocol = "<nfeProc xmlns=\"http://www.portalfiscal.inf.br/nfe\" versao=\"4.00\">"
        request_with_protocol += request_xml + protocol_xml + "</nfeProc>"

        return request_with_protocol

    @staticmethod
    def _get_protocol_nfe(
        response_xml,  # type: str
    ):
        # type: (...) -> str

        index = response_xml.index("<protNFe")
        index2 = response_xml.index("</protNFe>")
        return response_xml[index:index2 + 10]

    def do_validation(self, get_days_to_expiration=None):
        try:
            if get_days_to_expiration:
                return True, self.fiscal_validation_service.get_expiration_days()
            elif self.fiscal_validation_service.certificate_is_valid():
                return True, "OK"
            else:
                return False, "$EXPIRED_STORE_CERTIFICATE"
        except (Exception,):
            logger.exception("Falha na Leitura da Validade do Certificado NFCE")
            return False, "$CERTIFICATE_EXPIRATION_READING_FAILURE"

    def terminate(self):
        if isinstance(self.nfce_contingencia, NfceContingencia):
            self.nfce_contingencia.finaliza()

    @staticmethod
    def _verify_nfce_response(
        response,      # type: str
    ):
        # type: (...) -> None

        try:
            xml = remove_xml_namespace(xml=response)
            c_stat = xml.find(".//retEnviNFe/cStat").text
            x_motivo = xml.find(".//retEnviNFe/xMotivo").text if xml.find(".//retEnviNFe/xMotivo") is not None else None
            x_motivo = remove_accents(x_motivo or "")
            message = "NFCe Nao Autorizada. Status: {}".format(c_stat)
            reason = "Motivo: {}".format(x_motivo)
            error_message = "{}. {}".format(message, reason)
            logger.error(error_message)
            raise FiscalException(
                message=message,
                motivo=x_motivo,
                response_xml=response,
            )
        except FiscalException:
            raise
        except (Exception,):
            logger.exception("Error verify nfce response. Response: {0}".format(response))

    def _get_search_response(
        self,           # type: NfceProcessor
        order_xml,      # type: eTree.Element
        request_xml,    # type: str
        response_xml,   # type: str
    ):
        # type: (...) -> Tuple[Optional[str], eTree.Element]

        nfe_proc = None
        c_stat = remove_xml_namespace(xml=response_xml).find(".//retEnviNFe/cStat").text
        if "protNFe" in response_xml:
            c_stat = remove_xml_namespace(response_xml).find(".//infProt/cStat").text
            nfe_proc = self._get_nfe_prot(
                request_xml=request_xml,
                response_xml=response_xml,
            )

        duplicate_status = [NfceStatus.DuplicidadeNfce, NfceStatus.DuplicidadeDiferencaChaveAcesso]
        if c_stat not in duplicate_status:
            return nfe_proc, response_xml

        if nfe_proc is None:
            nfe_proc = self._add_protocol_tag(
                request_xml=request_xml,
                response_xml=response_xml,
            )

        nfe_proc, search_response_xml = self._get_new_request_situation(
            order_xml=order_xml,
            request_str=nfe_proc,
            c_stat_from_search_response=c_stat,
        )
        response_xml = eTree.tostring(search_response_xml)

        return nfe_proc, response_xml

    def _get_nfe_prot(
        self,           # type: NfceProcessor
        request_xml,    # type: str
        response_xml,   # type: str
    ):
        # type: (...) -> str

        protocol_xml = self._get_protocol_nfe(response_xml=response_xml)
        nfe_proc = self._add_protocol_to_request_xml(
            request_xml=request_xml,
            protocol_xml=protocol_xml,
        )
        return nfe_proc

    def _add_protocol_tag(
        self,           # type: NfceProcessor
        request_xml,    # type: str
        response_xml,   # type: str
    ):
        # type: (...) -> str

        logger.info("Adding protocol tag on xml: [{}]".format(response_xml))

        response_xml = remove_xml_namespace(xml=response_xml)
        protocol_xml = response_xml.find(".//retEnviNFe")
        inf_prot_tag = "<infProt>{}</infProt>".format(eTree.tostring(protocol_xml))
        nfe_proc = NFCE_WT_RESP.format(
            self.versao_ws,
            NfceRequestBuilder.NAMESPACE_NFE,
            request_xml,
            inf_prot_tag,
        )

        return nfe_proc

    @staticmethod
    def _log_original_request(
        order,           # type: eTree.Element
        request_xml,     # type: str
    ):
        # type: (...) -> None

        order_id = order.get("orderId").zfill(9)
        xml_base64 = base64.b64encode(request_xml)
        loggerFiscalXml.info("Request fiscal. OrderId: [{}] - RequestXML: [{}]".format(order_id, xml_base64))

        order_picture = eTree.tostring(order)
        order_picture = base64.b64encode(order_picture)
        loggerFiscalXml.info("Request fiscal. OrderId: [{}] OrderPicture: [{}]".format(order_id, order_picture))


class NfceStatus(object):
    IncorrectWebService = "0"
    NfceAutorizada = "100"
    ServicoParalisadoTemporariamento = "108"
    ServicoParalisadoSemPrevisao = "109"
    AutorizadoUso = "150"
    DuplicidadeNfce = "204"
    NaoConstaNoSefaz = "217"
    ErroAcessoLCR = "296"
    DuplicidadeDiferencaChaveAcesso = "539"
    PedidoConsultaDuplicado = "562"
    DataEntradaContingenciaPosteriorRecebimento = "558"
    NumeroSeriaJaTransmitido = "635"
    DataHoraMemissaoPosteriroRecebimentoSefaz = "703"
    EmissaoAtrasada = "704"
    ErroInterno = "999"


class NfceAutorizador(object):
    def __init__(
        self,                           # type: NfceAutorizador
        nfce_request_builder,           # type: NfceRequestBuilder
        nfce_request,                   # type: NfceRequest
        url_autorizacao,                # type: str
        url_ret_autorizacao,            # type: str
        versao_ws,                      # type: int
        max_tentativas_envio_lote,      # type: int
        intervalo_retentativa_lote,     # type: int
        send_sleep_time,                # type: int
        synchronous_mode,               # type: bool
        fiscal_validation_service,      # type: FiscalValidationService
        nfe_url_autorizacao,            # type: str
        fiscal_sent_dir,                # type: unicode
    ):
        # type: (...) -> NfceAutorizador

        self.nfce_request_builder = nfce_request_builder
        self.nfce_request = nfce_request
        self.url_autorizacao = url_autorizacao
        self.url_ret_autorizacao = url_ret_autorizacao
        self.versao_ws = versao_ws
        self.max_tentativas_envio_lote = max_tentativas_envio_lote
        self.intervalo_retentativa_lote = intervalo_retentativa_lote
        self.send_sleep_time = send_sleep_time
        self.synchronous_mode = synchronous_mode
        self.nfe_url_autorizacao = nfe_url_autorizacao
        self.logger = logger
        self.fiscal_validation_service = fiscal_validation_service
        self._last_sefaz_communication = None
        self._communication_lock = Lock()
        self.fiscal_sent_dir = fiscal_sent_dir

    def autoriza_notas(
        self,                   # type: NfceAutorizador
        request,                # type: str
        nf_model,             # type: str
        max_tentativas=None,    # type: Optional[int]
        envio_em_lote=False,    # type: bool
    ):
        # type: (...) -> (str, str, int)

        if max_tentativas is None:
            max_tentativas = self.max_tentativas_envio_lote

        self.logger.info("Enviando lote para SEFAZ")
        if self.versao_ws not in (1, 3, 4):
            raise AttributeError("Versão WS Inválida. Parâmetros válidos: 1, 3 e 4")

        if self.synchronous_mode:
            self.logger.info("Preparando para envio em modo síncrono...")

        status_code, response = self._envia_lote_com_retentativa(
            request=request,
            nf_model=nf_model,
        )
        if status_code == LoteStatus.CertificadoExpirado:
            if self.fiscal_validation_service.certificate_is_valid():
                error_msg = "SEFAZ Indisponivel - Falha na validacao do certificado: {}".format(status_code)
                self.logger.error(error_msg)
                raise ConnectionError(error_msg)

            raise LoteException("$EXPIRED_STORE_CERTIFICATE", status_code)

        if status_code == LoteStatus.RecebidoComSucesso or self.synchronous_mode:
            recibo = None
            if not self.synchronous_mode:
                self.logger.info("Lote recebido com sucesso, tentando obter o recibo...")
                recibo = self._busca_recibo(response)
                self.logger.info("Recibo obtido com sucesso")

            response_nfce, tentativas = self._busca_resposta_do_lote(recibo, max_tentativas, envio_em_lote, response)
            return request, response_nfce, tentativas
        else:
            contingency_reasons = (
                LoteStatus.ServicoParalisadoTemporariamente,
                LoteStatus.ServicoParalisadoSemPrevisao,
                LoteStatus.ErroInterno,
                LoteStatus.ConsumoIndevido,
                LoteStatus.ErroAcessoLCR,
            )
            if status_code in contingency_reasons:
                error_msg = "SEFAZ Indisponivel - Status: {}".format(status_code)
                self.logger.error(error_msg)
                raise ConnectionError(error_msg)

            error_msg = "Erro ao enviar lote de NFCe. Codigo: {}".format(status_code)
            self.logger.error(error_msg)
            raise LoteException(error_msg, status_code)

    def get_last_sefaz_communication(self):
        # type: (...) -> Optional[str]

        with self._communication_lock:
            if self._last_sefaz_communication:
                return self._last_sefaz_communication.isoformat()

        if self.fiscal_sent_dir:
            return self._get_last_success_from_files()

        return None

    def _envia_lote_com_retentativa(
        self,       # type: NfceAutorizador
        request,    # type: str
        nf_model,   # type: str
    ):
        # type: (...) -> (str, Optional[Response])

        current_attempt = 0
        response = None
        soap_act = None
        if self.versao_ws not in [1, 3]:
            soap_act = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4/nfeAutorizacaoLote"

        url_autorizacao = self.url_autorizacao
        if int(nf_model) == NfModel.NFE.value:
            url_autorizacao = self.nfe_url_autorizacao

        while current_attempt < self.max_tentativas_envio_lote:
            try:
                response = self.nfce_request.envia_nfce(
                    request=request,
                    url=url_autorizacao,
                    soap_action=soap_act,
                )
                if response.status_code == 200:
                    self._update_last_communication()
                    self.logger.info("Resposta do lote recebida")
                    c_stat = self._get_c_stat(content=response.content)
                    return c_stat, response

                log_message = "Sefaz retornou status diferente de 200: {0} e content: {1}"
                self.logger.info(log_message.format(response.status_code, response.content))

            except (Exception,):
                log_message = "Erro obtendo codigo retorno"
                if response is not None:
                    log_message = "Erro obtendo codigo retorno: StatusCode: {0}, Body: {1}"
                    log_message = log_message.format(response.status_code, response.content)

                self.logger.warning(log_message, exc_info=True)

            current_attempt += 1

        if response is not None and str(response.status_code) == LoteStatus.CertificadoExpirado:
            return str(response.status_code), response

        self.logger.info("Falha ao enviar lote. Excedido numero de tentativas")
        return "999", None

    def _get_c_stat(
        self,       # type: NfceAutorizador
        content,    # type: str
    ):
        # type: (...) -> str

        content = remove_xml_namespace(xml=content)
        c_stat = content.find(".//retEnviNFe/cStat").text
        if c_stat not in ["100", "150"]:
            reason = content.find(".//retEnviNFe/xMotivo")
            content = eTree.tostring(content)
            reason = reason.text if reason is not None else content
            info_message = "Error sending batch to Sefaz. cStat: {0} | Reason: {1} | Content: [{2}]"
            self.logger.info(info_message.format(c_stat, reason.encode("utf-8"), content))

        return c_stat

    def _busca_recibo(self, response):
        # type: (Response) -> str

        # Lote foi recebido para processamento com sucesso, vamor pegar o recibo e inicar a consulta
        try:
            xml = remove_xml_namespace(response.content)
            return xml.find(".//infRec/nRec").text
        except Exception:
            error_msg = "Erro ao obter o recibo da NFCe"
            self.logger.exception(error_msg)
            raise FiscalException(error_msg)

    def _busca_resposta_do_lote(self, recibo, max_tentativas, envio_de_lote, resposta_lote):
        # type: (str, int, bool, Response) -> (str, int)

        processado = False
        tentativas = 0
        status_consulta = None
        nfce_response_xml = None

        while not processado and (max_tentativas == -1 or tentativas < max_tentativas):
            try:
                if self.synchronous_mode:
                    response_consulta = resposta_lote
                    if not response_consulta:
                        break
                else:
                    request_consulta = self.nfce_request_builder.build_consulta(recibo)

                    self.logger.info("Consultando status do lote na SEFAZ")
                    if self.versao_ws in (1, 3):
                        soap_act = None
                    else:
                        soap_act = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeRetAutorizacao4/nfeAutorizacaoLote"

                    time.sleep(1)  # Tempo aguardando status ser processado antes de tentar obter seu status

                    response_consulta = self.nfce_request.envia_nfce(
                        request=request_consulta,
                        url=self.url_ret_autorizacao,
                        soap_action=soap_act,
                    )

                if response_consulta and response_consulta.status_code == 200:
                    nfce_response_xml = eTree.XML(response_consulta.content)
                    status_consulta = self._busca_status_consulta(nfce_response_xml)
                else:
                    if not response_consulta:
                        self.logger.error("Sem resposta da sefaz")
                        time.sleep(self.send_sleep_time)
                    else:
                        message = "Sefaz retornou status diferente de 200: {0} e content: {1}"
                        self.logger.info(message.format(response_consulta.status_code, response_consulta.content))
                        time.sleep(self.intervalo_retentativa_lote)
                    continue
            except Timeout:
                self.logger.exception("Timeout processando o lote, retentando")
                time.sleep(self.send_sleep_time)
                continue
            except (Exception,):
                self.logger.exception("Exceção processando o lote, retentando")
                time.sleep(self.intervalo_retentativa_lote)
                continue
            finally:
                tentativas += 1

            if status_consulta == LoteConsultaStatus.LoteEmProcessamento:
                message = "Lote ainda não processado. Aguardando processamento... Tentativa: %d - Status: %s"
                self.logger.info(message % (tentativas, status_consulta))
                time.sleep(self.intervalo_retentativa_lote)
                continue

            if status_consulta == LoteConsultaStatus.ConsumoIndevido:
                self.logger.error("Falha ao obter status da NFCe. SEFAZ Error 656 - Consumo Indevido")
                raise ConnectionError("Falha ao obter status da NFCe. SEFAZ Error 656 - Consumo Indevido")

            if status_consulta == LoteConsultaStatus.LoteProcessado:
                self.logger.info("Lote ja processado, verificando status do processamento da NFCE")
                if envio_de_lote:
                    return response_consulta.content, tentativas

                self._verifica_status_processamento_nfce(nfce_response_xml)
                return eTree.tostring(nfce_response_xml), tentativas

            if status_consulta == LoteConsultaStatus.ErroInterno:
                msg = "Erro interno não identificado ao tentar obter o status do lote na sefaz. 999 - Erro Interno"
                self.logger.error(msg)
                raise ConnectionError(msg)

            if status_consulta == LoteConsultaStatus.ErroAcessoLCR:
                msg = "Problemas para verificar se o certificado do assinante está na lista de certificados revogados"
                self.logger.error(msg)
                raise ConnectionError(msg)

            if status_consulta == LoteConsultaStatus.ServicoParalisadoTemporariamente:
                msg = "SEFAZ temporariamente indisponível. Erro 108 - Serviço Paralisado Momentaneamente"
                self.logger.error(msg)
                raise ConnectionError(msg)

            if status_consulta == LoteStatus.ServicoParalisadoSemPrevisao:
                msg = "SEFAZ indisponível sem previsão de retorno. Erro 109 - Serviço Paralisado Sem Previsão"
                self.logger.error(msg)
                raise ConnectionError(msg)

            if status_consulta in ("204", "539", "635", "558", "703"):
                return response_consulta.content, tentativas

            error_message = "Erro ao consultar status do lote NFCe. Codigo: {}".format(str(status_consulta))
            self.logger.error(error_message)
            raise LoteException(error_message, status_consulta)

        self.logger.warn("Falha ao obter status da NFCe. Excedido Numero de Tentativas")
        raise ConnectionError("Falha ao obter status da NFCe. Excedido Numero de Tentativas")

    def _busca_status_consulta(self, response_consulta_xml):
        # type: (eTree) -> str
        try:
            xml = remove_xml_namespace(eTree.tostring(response_consulta_xml))
            if self.synchronous_mode:
                c_stat = xml.find(".//retEnviNFe/cStat").text
            else:
                c_stat = xml.find(".//retConsReciNFe/cStat").text

            logger.info("Resposta da consulta de lote obtida. cStat: {}".format(c_stat))
            return c_stat

        except Exception as ex:
            logger.exception("Erro obtendo status consulta: {0}".format(eTree.tostring(response_consulta_xml)))
            raise ex

    def _update_last_communication(self):
        # type: (...) -> None

        with self._communication_lock:
            self._last_sefaz_communication = datetime.now(tz=pytz.utc)

    def _get_last_success_from_files(
        self,
    ):
        # type: (...) -> Optional[str]

        try:
            pattern = os.path.join(self.fiscal_sent_dir, "Enviados", "*", "*", "*", "*_proc_pos*.xml")
            files = glob.glob(pattern)
            if not files:
                return None

            latest_file = max(files, key=os.path.getmtime)
            last_modified = datetime.fromtimestamp(
                os.path.getmtime(latest_file),
                tz=pytz.utc,
            )
            return last_modified.isoformat()

        except (Exception,):
            self.logger.exception("Erro buscando último arquivo nfe_proc")
            return None

    @staticmethod
    def _verifica_status_processamento_nfce(response_consulta_xml):
        # type: (eTree) -> None
        response_consulta = eTree.tostring(response_consulta_xml)
        try:
            xml = remove_xml_namespace(response_consulta)

            status_nfce = xml.find(".//infProt/cStat").text
            x_motivo = xml.find(".//infProt/xMotivo").text if xml.find(".//infProt/xMotivo") is not None else None
        except Exception as ex:
            logger.exception("Erro obtendo status protocolo. ResponseXML: {0}".format(response_consulta))
            raise ex

        if x_motivo is None:
            x_motivo = ""
            logger.warning("Nao foi possivel encontrar xMotivo: {}".format(response_consulta))

        if status_nfce == NfceStatus.NfceAutorizada:
            logger.info("Protocolo processado com sucesso, finalizado")
            return

        contingency_reasons = (
            NfceStatus.ServicoParalisadoTemporariamento,
            NfceStatus.ServicoParalisadoSemPrevisao,
            NfceStatus.ErroInterno,
            NfceStatus.EmissaoAtrasada,
            NfceStatus.ErroAcessoLCR,
        )
        duplicate_status = [NfceStatus.DuplicidadeNfce, NfceStatus.DuplicidadeDiferencaChaveAcesso]
        if status_nfce in contingency_reasons:
            logger.warning("SEFAZ Indisponivel - Entrando em Contingencia - Status: %s ", status_nfce)
            raise ConnectionError("SEFAZ Indisponivel - Entrando em Contingencia - Status: %s" % status_nfce)
        elif status_nfce in duplicate_status:
            logger.info("{status} retornado mas mesma nota presente. Vamos aceitar".format(status=status_nfce))
        else:
            x_motivo = remove_accents(x_motivo)
            message = "NFCe Nao Autorizada. Status: {}".format(status_nfce)
            reason = "Motivo: {}".format(x_motivo)
            error_message = "{}. {}".format(message, reason)
            logger.warning(error_message)
            raise FiscalException(
                message="{} - {}".format(message, x_motivo),
                motivo=x_motivo,
                response_xml=response_consulta,
            )

    @staticmethod
    def _verifica_mesma_nota(response_xml):
        # type: (eTree) -> bool
        try:
            xml = remove_xml_namespace(eTree.tostring(response_xml))
            chave_atual = xml.find(".//infProt/chNFe").text
            x_motivo = xml.find(".//infProt/xMotivo").text

            index = x_motivo.find("[chNFe: ")
            chave_original = x_motivo[index + 8:index + 8 + 44]

            return chave_atual == chave_original

        except (Exception,):
            logger.exception("Erro verificando mesma nota. Xml: " + eTree.tostring(response_xml))
            return False


class NfceConnectivityTester(object):
    def __init__(self, nfce_request, url_status_servico, c_uf, versao_ws):
        self.nfce_request = nfce_request
        self.url_status_servico = url_status_servico
        self.c_uf = c_uf
        self.versao_ws = versao_ws
        self.logger = logger
        self.last_test = None
        self.last_connection_status = False

    def test_connectivity(self):
        if self.last_test and self.last_test > datetime.now() - timedelta(minutes=3):
            return self.last_connection_status

        envi_nfe = """<enviNFe versao="%.2f" xmlns="%s"></enviNFe>"""
        envi_nfe = envi_nfe % (3.1 if self.versao_ws in (1, 3) else 4, NfceRequestBuilder.NAMESPACE_NFE)
        if self.versao_ws in (1, 3):
            namespace = "http://www.portalfiscal.inf.br/nfe/wsdl/NfeStatusServico2"
        else:
            namespace = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeStatusServico4"
        envelopado = NfceRequestBuilder.envelopa(envi_nfe, namespace, self.c_uf, 3.1 if self.versao_ws in (1, 3) else 4)

        soap_action = None
        if self.versao_ws not in (1, 3):
            soap_action = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeStatusServico4/nfeStatusServicoNF"
        has_connection = False
        try:
            resp = self.nfce_request.envia_nfce(envelopado, self.url_status_servico, soap_action)
            has_connection = resp.status_code == 200
            if has_connection:
                self.logger.info("Sucesso no teste de conexão com a sefaz")
            return has_connection
        except (Exception,):
            self.logger.warning("Falha no teste de conexão com a sefaz")
            return has_connection
        finally:
            self.last_connection_status = has_connection
            self.last_test = datetime.now()


class NfceSituationChecker(object):

    def __init__(
        self,                       # type: NfceSituationChecker
        nfce_request,               # type: NfceRequest
        url_consultar_situacao,     # type: str
        ambiente,                   # type: unicode
        c_uf,                       # type: unicode
        versao_ws,                  # type: int
        max_tentativas_envio_lote,  # type: int
    ):
        # type: (...) -> None

        self.nfce_request = nfce_request
        self.url_consultar_situacao = url_consultar_situacao
        self.ambiente = ambiente
        self.c_uf = c_uf
        self.versao_ws = versao_ws
        self.logger = logger
        self.max_tentativas_envio_lote = max_tentativas_envio_lote

    def check_situation_nfe(self, nfe, timeout=None):
        cons_nfe = """<consSitNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="%.2f"><tpAmb>%s</tpAmb>"""\
            """<xServ>CONSULTAR</xServ><chNFe>%s</chNFe></consSitNFe>"""
        cons_nfe = cons_nfe % (3.1 if self.versao_ws in (1, 3) else 4, self.ambiente, nfe)
        namespace = NfceRequestBuilder.NAMESPACE_CONSULTA_SITUACAO_4
        if self.versao_ws in (1, 3):
            namespace = NfceRequestBuilder.NAMESPACE_CONSULTA_SITUACAO

        envelopado = NfceRequestBuilder.envelopa(cons_nfe, namespace, self.c_uf)
        soap_action = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo4/nfeConsultaNF"
        if self.versao_ws in (1, 3):
            soap_action = NfceRequestBuilder.NAMESPACE_CONSULTA_SITUACAO

        current_attempt = 0
        while current_attempt < self.max_tentativas_envio_lote:
            try:
                resp = self.nfce_request.envia_nfce(
                    request=envelopado,
                    url=self.url_consultar_situacao,
                    soap_action=soap_action,
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    return resp.text

                msg = "Erro ao consultar situação NFe - Code: {0} / Content: {1}"
                msg = msg.format(resp.status_code, resp.content)
                self.logger.error(msg)
            except (Exception,):
                msg = "Erro ao consultar situacao NFe. Tenativa: {} de {}"
                msg = msg.format(current_attempt, self.max_tentativas_envio_lote)
                self.logger.warning(msg=msg, exc_info=True)

            current_attempt += 1

        self.logger.error("Falha ao checar a situação da NFCE. Excedido numero de tentativas")


class NfceContingencia:
    def __init__(
        self,
        mbcontext,                          # type: MBEasyContext
        nfce_request_builder,               # type: NfceRequestBuilder
        nfce_autorizador,                   # type: NfceAutorizador
        versao_ws,                          # type: int
        nfce_situation_checker,             # type: NfceSituationChecker
        fiscal_sent_dir,                    # type: unicode
        max_tantavivas_envio_contingencia,  # type: int
        synchronous_mode,                   # type: bool
        nfce_connectivity_tester,           # type: NfceConnectivityTester
        database_dir,                       # type: str
        fiscal_schema_sql_path,             # type: str
    ):

        # type: (...) -> NfceContingencia

        self.mbcontext = mbcontext
        self.nfce_request_builder = nfce_request_builder
        self.nfce_aurotizador = nfce_autorizador
        self.versao_ws = versao_ws
        self.thread_condition = Condition()
        self.contingencia_lock = Lock()
        self.contingencia = False
        self.dh_contingencia = None
        self.just_contingencia = None
        self.running = True
        self.max_tentativas = 1
        self.contingencia_thread = None
        self.nfce_situation_checker = nfce_situation_checker
        self.fiscal_sent_dir = fiscal_sent_dir
        self.force_contingency = False
        self.process_now = False
        self.max_tantavivas_envio_contingencia = max_tantavivas_envio_contingencia
        self.synchronous_mode = synchronous_mode
        self.initial_batch_size = 50 if not self.synchronous_mode else 1
        self.batch_size = self.initial_batch_size
        self.wait_for_start = False
        self.nfce_connectivity_tester = nfce_connectivity_tester
        self.database_dir = database_dir
        self.fiscal_schema_sql_path = fiscal_schema_sql_path
        self.order_service = OrderService(mb_context=mbcontext)

    def start_contingencia(self):
        with FiscalDataRepository(self.mbcontext) as fiscalrepository:
            with self.contingencia_lock:
                if fiscalrepository.get_count_nfce_orders_to_send() == 0 and self.force_contingency is False:
                    self.contingencia = False
                else:
                    self.contingencia = True
                    self.dh_contingencia = datetime.now()
                    self.just_contingencia = "Notas anteriores em contingencia"

        if self.versao_ws not in (1, 3, 4):
            raise AttributeError("Versão WS Inválida. Parâmetros válidos: 1, 3 e 4")

        with self.contingencia_lock:
            if self.contingencia_thread is not None and self.contingencia_thread.is_alive():
                logger.info("Thread de contingencia já estava ativa")
                return

            self.contingencia_thread = Thread(target=self._processa_contingencia)
            self.contingencia_thread.daemon = True
            self.contingencia_thread.start()

    def is_contingencia(self):
        with self.contingencia_lock:
            return self.contingencia, self.dh_contingencia, self.just_contingencia

    def entra_contingencia(self, justificativa):
        with self.contingencia_lock:
            self.contingencia = True
            self.dh_contingencia = datetime.now()
            self.just_contingencia = justificativa
            with self.thread_condition:
                loggerThread.info("Entrando em contingência...")
                self.wait_for_start = True
                self.thread_condition.notifyAll()

            return self.contingencia, self.dh_contingencia, self.just_contingencia

    def finaliza(self):
        if self.running:
            self.thread_condition.acquire()
            self.running = False
            self.thread_condition.notifyAll()
            self.thread_condition.release()

    def _processa_contingencia(self):
        count = 0
        dir_enviados = os.path.join(self.fiscal_sent_dir, "Enviados")

        while self.running:
            try:
                if self.force_contingency:
                    loggerThread.info("Contingência forçada está ativa. Não tentaremos envio a SEFAZ no momento")
                    return

                self.nfce_connectivity_tester.logger = loggerThread
                connection_working = self.nfce_connectivity_tester.test_connectivity()
                if not connection_working:
                    self._wait(300)
                    continue

                if self.process_now is False and self.contingencia is False and count < 60:
                    self.thread_condition.acquire()
                    self.thread_condition.wait(5)
                    self.thread_condition.release()

                    count += 1
                    continue

                # No release da thread, vamos aguardar para nao tratar uma order que ainda nao esta preparada
                if self.wait_for_start:
                    time.sleep(5)

                self.wait_for_start = False

                count = 0
                with FiscalDataRepository(
                    mbcontext=self.mbcontext,
                    database_dir=self.database_dir,
                    schema_sql_path=self.fiscal_schema_sql_path,
                ) as fiscalrepository:
                    orders_to_send = fiscalrepository.get_nfce_orders_to_send(self.batch_size)
                    qty_orders_selected = len(orders_to_send)

                    if self.synchronous_mode:
                        loggerThread.info("Modo síncrono ativado. Vamos processar pedidos individualmente")
                    else:
                        message = "Encontradas {} orders para serem enviadas. Tamanho do lote: {}"
                        loggerThread.info(message.format(qty_orders_selected, self.batch_size))

                    if orders_to_send:
                        all_xml = ""
                        tentativas = 0

                        orders_to_send_dict = self._get_orders_to_send(orders_to_send=orders_to_send)
                        for key in orders_to_send_dict.keys():
                            orders_to_send = orders_to_send_dict.get(key)
                            for order_to_send in orders_to_send[:]:
                                try:
                                    xml_order_pict = self.order_service.get_order_picture(
                                        pos_id=order_to_send.pos_id,
                                        order_id=order_to_send.order_id,
                                    )
                                    order_state = xml_order_pict.attrib["state"]
                                    if order_state != "PAID":
                                        log_message = "Order {} nao contém status PAID. Status da order: {}"
                                        msg = log_message.format(order_to_send.order_id, order_state)
                                        loggerThread.error(msg)
                                        raise Exception(msg)
                                except Exception as ex:
                                    loggerThread.info("Order antiga e/ou sem referencia: {}".format(ex.message))
                                    fiscalrepository.set_nfce_sent(order_to_send.order_id, -1)
                                    orders_to_send.remove(order_to_send)
                                    continue

                                xml_base64 = order_to_send.xml
                                xml_string = base64.b64decode(xml_base64)
                                all_xml += xml_string
                                order_to_send.xml = xml_string

                            if not orders_to_send or not all_xml:
                                loggerThread.info("Nenhuma nota a ser processada. Vamos tentar obter novas notas.")
                                continue
                            else:
                                message = "Quantidade de notas a serem processadas: {}".format(len(orders_to_send))
                                loggerThread.info(message)
                                orders_to_send = sorted(orders_to_send, key=lambda x: int(x.order_id))

                            envelopado = self.nfce_request_builder.envelopa_lote(all_xml)
                            try:
                                is_nfce = key == str(NfModel.NFCE.name)
                                nf_model = str(NfModel.NFCE.value) if is_nfce else str(NfModel.NFE.value)
                                self.nfce_aurotizador.logger = loggerThread
                                _, response, tentativas = self.nfce_aurotizador.autoriza_notas(
                                    request=envelopado,
                                    nf_model=nf_model,
                                    max_tentativas=self.max_tantavivas_envio_contingencia,
                                    envio_em_lote=True,
                                )
                            except LoteException:
                                if not self.synchronous_mode and self.batch_size == self.initial_batch_size:
                                    # Se estamos processando um lote inteiro e tivemos algum problema,
                                    # vamos processar nota a nota
                                    self.batch_size = 1
                                    loggerThread.info("Problemas com o lote - Enviando XMLs individualmente")
                                else:
                                    # Estamos processando uma unica nota e tivemos problemas,
                                    # marcamos ela como problema e pegamos a próxima
                                    fiscalrepository.set_nfce_sent(orders_to_send[0].order_id, -1)
                                    message = "Marcando XML da order {} como invalido"
                                    loggerThread.info(message.format(orders_to_send[0].order_id))
                                    # Vamos esperar alguns segundos para enviar a proxima nota afim
                                    # de nao receber 656 da SEFAZ
                                    time.sleep(10)

                                # De qualquer maneira, vamos selecionar novas notas para enviar
                                continue

                            except ConnectionError:
                                loggerThread.info("Problemas de Conexao - Aguardando 5 minutos para tentar novamente")
                                self._wait(300)
                                continue

                            protocolos_notas = []
                            try:
                                response_xml = remove_xml_namespace(response)
                                loggerThread.info("Notas processadas com sucesso. Verificando protocolos")
                                protocolos_notas = response_xml.findall(".//infProt")
                                if not protocolos_notas:
                                    protocolos_notas = response_xml.findall(".//retEnviNFe")
                            except (Exception,):
                                loggerThread.exception("Erro obtendo protocolo - Response: {}".format(response))

                            message = "Quantidade de protocolos a serem processados: {}"
                            loggerThread.info(message.format(len(protocolos_notas)))
                            for protocolo in protocolos_notas:
                                try:
                                    chave_protocolo = None
                                    if len(orders_to_send) != 1:
                                        chave_protocolo = protocolo.find(".//chNFe").text

                                    for order in orders_to_send:
                                        order_id = order.order_id.zfill(9)
                                        loggerThread.info("OrderId a ser processada: [{}]".format(order_id))
                                        order_xml = remove_xml_namespace(order.xml)
                                        nfe_key = order_xml.find(INF_NFE).attrib['Id']
                                        chave_xml = nfe_key[3:]

                                        if chave_xml == chave_protocolo or chave_protocolo is None:
                                            data_emissao = order_xml.find(".//ide/dhEmi").text
                                            serie_nota = order_xml.find(".//ide/serie").text
                                            serie_nota = serie_nota.zfill(3)
                                            numero_nota = order_xml.find(".//ide/nNF").text
                                            numero_nota = numero_nota.zfill(9)
                                            dir_arquivo = os.path.join(
                                                data_emissao[0:4],
                                                data_emissao[5:7],
                                                data_emissao[8:10],
                                            )
                                            dir_nota = os.path.join(dir_enviados, dir_arquivo)

                                            if not os.path.exists(dir_nota):
                                                os.makedirs(dir_nota)

                                            stat = protocolo.find(".//cStat").text
                                            if stat == NfceStatus.DuplicidadeDiferencaChaveAcesso:
                                                msg = "539 detectado. Tratando via _handle_duplicated_diff_key."
                                                loggerThread.info(msg)
                                                try:
                                                    self._handle_duplicated_diff_key(
                                                        order=order,
                                                        protocolo=protocolo,
                                                        fiscalrepository=fiscalrepository,
                                                        dir_nota=dir_nota,
                                                        serie_nota=serie_nota,
                                                        numero_nota=numero_nota,
                                                    )
                                                except (TypeError, eTree.ParseError):
                                                    loggerThread.warning(
                                                        "Erro de parser/decode ao tratar 539 da Order:%s "
                                                        "com order_picture atual. Tentando recarregar orderPicture.",
                                                        order_id,
                                                    )
                                                    try:
                                                        order_picture = self.order_service.get_order_picture(
                                                            pos_id=order.pos_id,
                                                            order_id=order.order_id,
                                                        )
                                                        order_str = eTree.tostring(order_picture)
                                                        order.order_picture = base64.b64encode(order_str)
                                                        self._handle_duplicated_diff_key(
                                                            order=order,
                                                            protocolo=protocolo,
                                                            fiscalrepository=fiscalrepository,
                                                            dir_nota=dir_nota,
                                                            serie_nota=serie_nota,
                                                            numero_nota=numero_nota,
                                                        )
                                                    except Exception:
                                                        loggerThread.exception(
                                                            "Falha ao tratar 539 da Order:%s após recarregar "
                                                            "orderPicture. OrderPicture(base64): %s",
                                                            order_id,
                                                            order.order_picture,
                                                        )
                                                except Exception:
                                                    loggerThread.exception(
                                                        "Falha ao tratar 539 da Order:%s.",
                                                        order_id,
                                                    )
                                                finally:
                                                    if order in orders_to_send:
                                                        orders_to_send.remove(order)
                                                break

                                            message = "Protocolo encontrado para nota %s; Order: %s; Status: %s"
                                            loggerThread.info(message, chave_xml, order_id, stat)

                                            status_to_reprocess = [
                                                NfceStatus.DuplicidadeNfce,
                                                NfceStatus.ErroAcessoLCR,
                                                NfceStatus.EmissaoAtrasada,
                                                NfceStatus.NumeroSeriaJaTransmitido,
                                                NfceStatus.NaoConstaNoSefaz,
                                                NfceStatus.ErroAcessoLCR,
                                                NfceStatus.DataEntradaContingenciaPosteriorRecebimento,
                                                NfceStatus.DataHoraMemissaoPosteriroRecebimentoSefaz,
                                                NfceStatus.IncorrectWebService,
                                                NfceStatus.DataHoraMemissaoPosteriroRecebimentoSefaz
                                            ]
                                            if stat in status_to_reprocess:
                                                eTree.register_namespace('', NfceRequestBuilder.NAMESPACE_NFE)

                                                message = "OrderId: [{}] - Response processamento [{}]"
                                                loggerThread.info(message.format(order_id, response))
                                                status_mark_reprocess = [
                                                    NfceStatus.NaoConstaNoSefaz,
                                                    NfceStatus.ErroAcessoLCR,
                                                    NfceStatus.IncorrectWebService,
                                                ]
                                                if stat in status_mark_reprocess:
                                                    try:
                                                        message = "Preparando pedido [{}] para ser enviada"\
                                                            "novamente na próxima tentativa"
                                                        loggerThread.info(message.format(order_id))

                                                        order_picture = self.order_service.get_order_picture(
                                                            pos_id=order.pos_id,
                                                            order_id=order.order_id,
                                                        )
                                                        order_str = eTree.tostring(order_picture)
                                                        order_str = base64.b64encode(order_str)
                                                        message = ("Contingency Process. OrderId: [{}] "
                                                                   "OrderPicture: [{}]")
                                                        loggerFiscalXml.info(message.format(order_id, order_str))
                                                        reason = protocolo.find(".//xMotivo")

                                                        if reason is not None:
                                                            message = "OrderId: [{}] - cStat motivo [{}]"
                                                            loggerThread.info(
                                                                message.format(
                                                                    order_id,
                                                                    reason.text.encode("utf-8")
                                                                )
                                                            )

                                                        envelopado, data_emissao, serie_nota, numero_nota, nf_model = \
                                                            self.nfce_request_builder.build_request(
                                                                order=order_picture,
                                                                contingencia=False,
                                                                dh_contingencia=None,
                                                                just_contingencia=None,
                                                            )
                                                        index1 = envelopado.index("<NFe")
                                                        index2 = envelopado.index("</NFe>")
                                                        new_req = envelopado[index1:index2 + 6]
                                                        xml_base64 = base64.b64encode(new_req)
                                                        order.xml = new_req

                                                        message = "Marcando OrderId: [{}] como pendente de envio"
                                                        loggerThread.info(message.format(order_id))

                                                        fiscalrepository.set_nfce_sent_with_xml(
                                                            order_id=order_id,
                                                            xml_base64=xml_base64,
                                                            status=0,
                                                        )

                                                        index1 = envelopado.index("Id=\"NFe")
                                                        nfe_key = envelopado[index1 + 4:index1 + 51]
                                                        message = "Contingency Process. OrderId: [{}] new nfe key: [{}]"
                                                        loggerFiscalXml.info(message.format(order_id, nfe_key))

                                                        message = "Contingency Process. OrderId: [{}] new request: [{}]"
                                                        loggerFiscalXml.info(message.format(order_id, xml_base64))

                                                    except FiscalException:
                                                        message = "Marcando XML nota [{}] como invalido"
                                                        loggerThread.info(message.format(order.order_id))
                                                        fiscalrepository.set_nfce_sent(order.order_id, -1)

                                                    except (Exception,):
                                                        message = "Erro ao gerar XML da order [{}]"
                                                        loggerThread.exception(message.format(order_id))
                                                        fiscalrepository.set_nfce_sent(order.order_id, -1)

                                                    break

                                                if stat == "635":
                                                    message = "Recebemos 635, vamos esgotar as tentativas"\
                                                        " e aguardar para tentar novamente."
                                                    loggerThread.info(message)
                                                    tentativas = self.max_tentativas + 1
                                                    break

                                                if stat in ("204", "558", "703", "704"):
                                                    nfe_key = order_xml.find(INF_NFE).attrib['Id']
                                                    chave_xml = nfe_key[3:]

                                                    # Vamos esperar alguns segundos para checar a situacao da nota
                                                    # afim de nao receber 656 da SEFAZ
                                                    self.nfce_situation_checker.logger = loggerThread
                                                    for _ in range(3):
                                                        time.sleep(5)
                                                        fiscal_data = self.nfce_situation_checker.check_situation_nfe(
                                                            nfe=chave_xml,
                                                            timeout=5,
                                                        )
                                                        if fiscal_data:
                                                            break
                                                    else:
                                                        message = "Problemas ao tentar consultar a" \
                                                            "situação da nota na sefaz. Vamos tentar novamente depois."
                                                        loggerThread.error(message)
                                                        break

                                                    fiscal_data_xml = remove_xml_namespace(fiscal_data.encode("utf8"))
                                                    c_stat = fiscal_data_xml.find(".//retConsSitNFe/cStat")

                                                    if c_stat is None:
                                                        loggerThread.error("Campo cStat nao encontrado ou vazio")
                                                        break

                                                    c_stat = c_stat.text

                                                    message = "Protocolo recebido. OrderId: {}; cStat: {}"
                                                    loggerThread.info(message.format(order_id, c_stat))

                                                    if c_stat in ("100", "150"):
                                                        inf_prot = fiscal_data_xml.find(".//protNFe/infProt")
                                                        inf_prot.attrib.pop("Id", None)
                                                        protocol = inf_prot
                                                        response_xml = fiscal_data_xml
                                                    else:
                                                        fiscalrepository.set_nfce_sent(order.order_id, -1)
                                                        break

                                                self._finalize_authorized_nfce(
                                                    fiscal_repository=fiscalrepository,
                                                    order=order,
                                                    protocol=protocol,
                                                    response_xml=response_xml,
                                                    dir_nota=dir_nota,
                                                    serie_nota=serie_nota,
                                                    numero_nota=numero_nota,
                                                    order_id=order_id,
                                                    nfe_key=nfe_key,
                                                )
                                            elif stat in ("100", "150"):
                                                self._finalize_authorized_nfce(
                                                    fiscal_repository=fiscalrepository,
                                                    order=order,
                                                    protocol=protocolo,
                                                    response_xml=response_xml,
                                                    dir_nota=dir_nota,
                                                    serie_nota=serie_nota,
                                                    numero_nota=numero_nota,
                                                    order_id=order_id,
                                                    nfe_key=nfe_key,
                                                )
                                            elif stat not in ("108", "109", "635", "999"):
                                                message = "Nota com status desconhecido, vamos invalida-la. Status: {}"
                                                loggerThread.error(message.format(stat))
                                                fiscalrepository.set_nfce_sent(order.order_id, -1)

                                            orders_to_send.remove(order)
                                            break
                                except (Exception,):
                                    protocolo_str = eTree.tostring(protocolo)
                                    loggerThread.exception("Erro tratando protocolo: {}".format(protocolo_str))
                                    continue

                        if tentativas > self.max_tentativas:
                            message = "SEFAZ lenta. Não vamos sair da contingência. "\
                                "Aguardando 5 minutos para tentar novamente"
                            loggerThread.info(message)
                            self._wait(300)
                            continue

                    # Todas as orders foram enviadas. Vamos verificar se ainda estamos em contingencia
                    if qty_orders_selected < self.batch_size and self.force_contingency is False:
                        # Da ultima vez enviamos menos do que o esperado, verificamos se podemos sair da contingencia
                        with self.contingencia_lock:
                            if fiscalrepository.get_count_nfce_orders_to_send() == 0:
                                loggerThread.info("Saindo da contingencia")
                                self.batch_size = self.initial_batch_size
                                self.contingencia = False

                    if self.synchronous_mode and self.contingencia:
                        time.sleep(1)
                        self.process_now = True
                    else:
                        self.process_now = False
            except (Exception,):
                loggerThread.exception("Erro tratando notas em contingencia")
                self._wait(5)

    def _finalize_authorized_nfce(
        self,               # type: NfceContingencia
        fiscal_repository,  # type: FiscalDataRepository
        order,              # type: Order
        protocol,           # type: eTree.Element
        response_xml,       # type: eTree.Element
        dir_nota,           # type: str
        serie_nota,         # type: str
        numero_nota,        # type: str
        order_id,           # type: str
        nfe_key,            # type: str
    ):
        # type: (...) -> None

        ws_version = 3.1 if self.versao_ws in (1, 3) else 4

        nfce_wt_resp = NFCE_WT_RESP.format(
            ws_version,
            NfceRequestBuilder.NAMESPACE_NFE,
            order.xml,
            eTree.tostring(protocol),
        )

        nfce_file_name = NFE_PROC_POS.format(
            serie_nota,
            numero_nota,
            order_id,
            str(order.pos_id).zfill(2),
            nfe_key,
        )

        path_dir = "{}/*_{}_{}_request_pos*".format(
            dir_nota,
            numero_nota,
            order_id.zfill(9),
        )

        for xml_file in glob.glob(path_dir):
            os.remove(xml_file)

        with open(os.path.join(dir_nota, nfce_file_name), "w+") as nfe_proc_file:
            nfe_proc_file.write(nfce_wt_resp)

        self._update_order_properties(
            fiscal_repository=fiscal_repository,
            nfce_wt_resp=nfce_wt_resp,
            order=order,
        )

        xml_response = base64.b64encode(eTree.tostring(response_xml))
        fiscal_repository.set_xml_response(
            order_id=order.order_id,
            xml_base64=xml_response,
        )

    def _handle_duplicated_diff_key(
        self,
        order,
        protocolo,
        fiscalrepository,
        dir_nota,
        serie_nota,
        numero_nota,
    ):
        # type: (...) -> None

        order_id_padded = order.order_id.zfill(9)
        motivo_node = protocolo.find(".//xMotivo")
        motivo = motivo_node.text if motivo_node is not None else ""
        loggerThread.info("Tratando 539 da order %s. xMotivo SEFAZ: %s" % (order_id_padded, motivo))

        try:
            order_picture_xml = self._parse_order_picture(order.order_picture)
            request_str = self._replace_contingency_nfe(
                request_str=order.xml,
                order_xml=order_picture_xml,
            )
            rebuilt_key = remove_xml_namespace(request_str).find(INF_NFE).attrib["Id"][3:]
            loggerThread.info(
                "Consultando SEFAZ com chave reconstruida %s para order %s" % (rebuilt_key, order_id_padded)
            )

            self.nfce_situation_checker.logger = loggerThread
            fiscal_data = None
            for _ in range(3):
                time.sleep(5)
                fiscal_data = self.nfce_situation_checker.check_situation_nfe(
                    nfe=rebuilt_key,
                    timeout=5,
                )
                if fiscal_data:
                    break

            if not fiscal_data:
                loggerThread.warning(
                    "539 da order %s sem resposta da SEFAZ para a chave reconstruida %s. Sera retentado." % (
                        order_id_padded,
                        rebuilt_key,
                    )
                )
                fiscalrepository.set_nfce_sent(order.order_id, -1)
                return

            fiscal_data_xml = remove_xml_namespace(fiscal_data.encode("utf8"))
            c_stat_node = fiscal_data_xml.find(".//retConsSitNFe/cStat")
            c_stat = c_stat_node.text if c_stat_node is not None else None
            loggerThread.info(
                "Order %s: chave reconstruida %s retornou cStat %s" % (order_id_padded, rebuilt_key, c_stat)
            )

            if c_stat not in ("100", "150"):
                loggerThread.warning(
                    "539 nao recuperavel para order %s (cStat %s). "
                    "Marcando como excecao terminal para intervencao manual." % (order_id_padded, c_stat)
                )
                fiscalrepository.set_nfce_sent(order.order_id, CSTAT_539_DUP_DIFF_KEY)
                return

            inf_prot = fiscal_data_xml.find(".//protNFe/infProt")
            if inf_prot is None:
                loggerThread.warning(
                    "539 da order %s: protNFe/infProt ausente apesar de cStat %s. "
                    "Marcando como excecao terminal para intervencao manual." % (order_id_padded, c_stat)
                )
                fiscalrepository.set_nfce_sent(order.order_id, CSTAT_539_DUP_DIFF_KEY)
                return

            inf_prot.attrib.pop("Id", None)
            order.xml = request_str[request_str.index("<NFe"):request_str.index("</NFe>") + 6]
            self._finalize_authorized_nfce(
                fiscal_repository=fiscalrepository,
                order=order,
                protocol=inf_prot,
                response_xml=fiscal_data_xml,
                dir_nota=dir_nota,
                serie_nota=serie_nota,
                numero_nota=numero_nota,
                order_id=order_id_padded,
                nfe_key=rebuilt_key,
            )
            loggerThread.info("Order %s resolvida com chave reconstruida %s" % (order_id_padded, rebuilt_key))

        except Exception:
            loggerThread.exception(
                "Erro ao processar 539 para order %s. Marcando como excecao." % order_id_padded
            )
            fiscalrepository.set_nfce_sent(order.order_id, -1)

    def _parse_order_picture(
        self,
        order_picture_b64,    # type: str
    ):
        # type: (...) -> eTree.Element

        padding = "=" * ((4 - len(order_picture_b64) % 4) % 4)
        order_picture_bytes = base64.b64decode(order_picture_b64 + padding)
        order_xml = eTree.XML(order_picture_bytes)
        return order_xml.find("Order") if order_xml.find("Order") is not None else order_xml

    def _replace_contingency_nfe(
        self,
        request_str,    # type: str
        order_xml,      # type: eTree.Element
    ):
        # type: (...) -> str

        request = self.nfce_request_builder.build_request(
            order=order_xml,
            contingencia=False,
            dh_contingencia=None,
            just_contingencia=None,
        )
        envelopado = request[0]
        index1 = envelopado.index("<NFe")
        index2 = envelopado.index("</NFe>")
        new_nfe = envelopado[index1:index2 + 6]
        nfe_start_tag = request_str[:request_str.index("<NFe ")]
        nfe_end_tag = request_str[request_str.index("</NFe>") + 6:]
        return nfe_start_tag + new_nfe + nfe_end_tag

    def get_pending_contingency_count(
        self,
    ):
        # type: (...) -> int

        try:
            with FiscalDataRepository(
                mbcontext=self.mbcontext,
                database_dir=self.database_dir,
                schema_sql_path=self.fiscal_schema_sql_path,
            ) as fiscalrepository:
                return fiscalrepository.get_count_nfce_orders_to_send()

        except (Exception,):
            loggerThread.exception("Erro ao obter quantidade de notas em contingência")
            return 0

    @staticmethod
    def _get_orders_to_send(
        orders_to_send,    # type: List[Order]
    ):
        # type: (...) -> Dict[str, List[str]]

        response = {
            NfModel.NFCE.name: [],
            NfModel.NFE.name: [],
        }
        for order in orders_to_send:
            xml_base64 = order.xml
            xml_string = base64.b64decode(xml_base64)
            xml_fiscal = eTree.fromstring(xml_string)
            ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
            mod = xml_fiscal.find('.//nfe:ide/nfe:mod', ns).text

            if int(mod) == NfModel.NFCE.value:
                response[NfModel.NFCE.name].append(order)
            elif int(mod) == NfModel.NFE.value:
                response[NfModel.NFE.name].append(order)

        return response

    @staticmethod
    def _update_order_properties(fiscal_repository, nfce_wt_resp, order):
        xml_base64 = base64.b64encode(nfce_wt_resp)
        fiscal_repository.set_fiscal_xml_custom_property(
            pos_id=order.pos_id,
            xml_request=xml_base64,
            order_id=order.order_id,
            blk_notify=False,
        )
        order_picture = OrderTaker().get_order_picture(order.pos_id, order.order_id)
        fiscal_repository.set_order_picture(order.order_id, base64.b64encode(order_picture))
        fiscal_repository.set_nfce_sent_with_xml(order.order_id, xml_base64, 1)
        return order_picture

    def _wait(self, wait_time):
        self.thread_condition.acquire()
        self.thread_condition.wait(wait_time)
        self.thread_condition.release()


class LoteException(Exception):
    def __init__(self, message, error_code):
        super(LoteException, self).__init__()
        self.message = message
        self.error_code = error_code

    def __str__(self):
        return "LoteException Error Code: {0}\\Message: {1}.".format(self.error_code, self.message)


class LoteStatus(object):
    RecebidoComSucesso = "103"
    ServicoParalisadoTemporariamente = "108"
    ServicoParalisadoSemPrevisao = "109"
    ConsumoIndevido = "656"
    ErroInterno = "999"
    ErroAcessoLCR = "296"
    CertificadoExpirado = "403"


class LoteConsultaStatus(object):
    LoteProcessado = "104"
    LoteEmProcessamento = "105"
    ServicoParalisadoTemporariamente = "108"
    LoteComFalhaSchema = "225"
    ConsumoIndevido = "656"
    ErroInterno = "999"
    ErroAcessoLCR = "296"


class FiscalException(Exception):

    def __init__(
        self,               # type: FiscalException
        message,            # type: str
        motivo=None,        # type: Optional[str]
        response_xml=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        super(FiscalException, self).__init__()

        self.message = message
        self.motivo = motivo
        self.response_xml = response_xml

    def __str__(self):
        ret = "FiscalException: " + self.message + "."
        if self.motivo is not None:
            ret += " Motivo: " + self.motivo + "."

        return ret


class FiscalBuildException(Exception):
    def __init__(self, message, motivo=None):
        # type: (str, str) -> None
        super(FiscalBuildException, self).__init__()
        self.message = message
        self.motivo = motivo

    def __str__(self):
        ret = "FiscalBuildException: " + self.message + "."
        if self.motivo is not None:
            ret += " Motivo: " + self.motivo + "."

        return ret
