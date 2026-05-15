# -*- coding: utf-8 -*-
"""
Utilitários para processamento de XML fiscal.

Centraliza operações comuns de parsing, decodificação e extração de dados
de XMLs de nota fiscal para evitar duplicação de código.
"""

import base64
import logging
import xml.etree.ElementTree as ET


def decode_fiscal_xml(xml_base64):
    """
    Decodifica XML fiscal de base64.
    
    Args:
        xml_base64: String ou bytes em base64
        
    Returns:
        bytes: XML decodificado
    """
    if isinstance(xml_base64, str):
        xml_base64 = xml_base64.encode('utf-8')
    return base64.b64decode(xml_base64)


def parse_fiscal_xml(xml_bytes):
    """
    Parse XML com namespace fiscal.
    
    Args:
        xml_bytes: bytes do XML
        
    Returns:
        Element: Root do ElementTree
    """
    return ET.fromstring(xml_bytes)


def get_xml_namespace():
    """Retorna namespace fiscal NFe."""
    return {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def extract_cstat(xml_element):
    """
    Extrai cStat de XML fiscal (protocolo autorizado).
    
    Args:
        xml_element: Element do ET com XML parseado
        
    Returns:
        str ou None: Valor de cStat ou None se não encontrado
    """
    ns = get_xml_namespace()
    cstat = xml_element.find(".//nfe:protNFe/nfe:infProt/nfe:cStat", ns)
    return cstat.text if cstat is not None else None


def extract_cstat_inut(xml_element):
    """
    Extrai cStat de inutilização (procInutNfe).
    
    Args:
        xml_element: Element do ET com XML parseado
        
    Returns:
        str ou None: Valor de cStat ou None se não encontrado
    """
    ns = get_xml_namespace()
    cstat = xml_element.find(".//nfe:retInutNFe/nfe:infInut/nfe:cStat", ns)
    return cstat.text if cstat is not None else None


def extract_justificativa(xml_element):
    """
    Extrai justificativa de cancelamento (xJust).
    
    Args:
        xml_element: Element do ET com XML parseado
        
    Returns:
        str ou None: Justificativa ou None se não encontrado
    """
    ns = get_xml_namespace()
    just = xml_element.find(".//nfe:infNFe/nfe:ide/nfe:xJust", ns)
    return just.text if just is not None else None
