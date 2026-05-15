# -*- coding: utf-8 -*-
"""
Módulo para gerenciar leitura, escrita e atualização do CSV com status de correções.
"""
import os
import logging
from datetime import datetime


def mapear_correcao(codigo_retorno, detalhes_correcao=None):
    """
    Mapeia código de retorno de time_direction() para descrição legível.
    
    Args:
        codigo_retorno: int/None - Código retornado por time_direction()
        detalhes_correcao: dict - Detalhes adicionais (aped, cstat, etc)
    
    Returns:
        str - Descrição da correção ou "Sem correção"
    """
    mapa_correcoes = {
        5: "RESEND - Reenviado para BOH",
        -1: "REPROCESSAR - Reprocessar SEFAZ",
        None: "Sem correcao"
    }
    
    descricao_base = mapa_correcoes.get(codigo_retorno, f"Desconhecido ({codigo_retorno})")
    
    if detalhes_correcao and isinstance(detalhes_correcao, dict):
        aped = detalhes_correcao.get("aped")
        if aped:
            descricao_base = f"{aped} - {descricao_base}"
    
    return descricao_base


def ler_arquivo_csv(caminho_arquivo):
    """
    Lê arquivo CSV e retorna linhas com conteúdo.
    
    Args:
        caminho_arquivo: str - Caminho do arquivo CSV
    
    Returns:
        list - Lista de linhas do CSV
    """
    dados = []
    try:
        with open(caminho_arquivo, 'r', encoding='utf-8') as arquivo_csv:
            dados = arquivo_csv.readlines()
        logging.info(f"CSV lido com sucesso: {len(dados)} linhas")
    except Exception as e:
        logging.error(f"Erro ao ler CSV {caminho_arquivo}: {e}")
    return dados


def parsear_csv_simples(linhas_csv):
    """
    Parseia CSV simples (número_nota;status;detalhes).
    
    Args:
        linhas_csv: list - Linhas do arquivo CSV
    
    Returns:
        list - Lista de dicionários com dados do CSV
    """
    dados_parsados = []
    for linha in linhas_csv:
        linha = linha.strip()
        if linha:
            partes = linha.split(";")
            if len(partes) >= 1:
                dados_parsados.append({
                    "numero_nota": partes[0].strip(),
                    "status": partes[1].strip() if len(partes) > 1 else "",
                    "detalhes": partes[2].strip() if len(partes) > 2 else ""
                })
    return dados_parsados


def criar_csv_resultado(notas_rastreadas, caminho_saida):
    """
    Cria arquivo CSV com resultado de correções aplicadas.
    
    Formato: numero_nota;correcao_aplicada;detalhes;data_processamento
    
    Args:
        notas_rastreadas: dict - Dicionário {numero_nota: {"correcao": ..., "detalhes": ...}}
        caminho_saida: str - Caminho para salvar o CSV de resultado
    
    Returns:
        bool - True se sucesso, False caso contrário
    """
    try:
        with open(caminho_saida, 'w', encoding='utf-8') as arquivo_saida:
            # Header
            arquivo_saida.write("numero_nota;correcao_aplicada;detalhes;data_processamento\n")
            
            # Dados
            for numero_nota, info in sorted(notas_rastreadas.items()):
                correcao = info.get("correcao", "Sem correção")
                detalhes = info.get("detalhes", "")
                data_proc = info.get("data_processamento", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                
                linha = f"{numero_nota};{correcao};{detalhes};{data_proc}\n"
                arquivo_saida.write(linha)
        
        logging.info(f"CSV de resultado criado: {caminho_saida} ({len(notas_rastreadas)} notas)")
        return True
    except Exception as e:
        logging.error(f"Erro ao criar CSV de resultado: {e}")
        return False


def atualizar_csv_com_correções(arquivo_original, notas_rastreadas, caminho_saida=None):
    """
    Atualiza arquivo CSV original com informações de correções aplicadas.
    
    Adiciona coluna "Correção Aplicada" com detalhes de cada nota processada.
    
    Args:
        arquivo_original: str - Caminho do arquivo CSV original
        notas_rastreadas: dict - {numero_nota: {"correcao": ..., "detalhes": ...}}
        caminho_saida: str - Caminho para salvar (padrão: arquivo_original + "_com_resultado")
    
    Returns:
        bool - True se sucesso, False caso contrário
    """
    if not caminho_saida:
        base, ext = os.path.splitext(arquivo_original)
        caminho_saida = f"{base}_com_resultado{ext}"
    
    try:
        linhas_originais = ler_arquivo_csv(arquivo_original)
        linhas_saida = []
        
        for i, linha in enumerate(linhas_originais):
            linha = linha.strip()
            
            # Primeira linha (header)
            if i == 0:
                linhas_saida.append(f"{linha};Correcao Aplicada")
                continue
            
            if linha:
                # Extrai número da nota (primeira coluna)
                partes = linha.split(";")
                numero_nota = partes[0].strip() if partes else ""
                
                # Busca informação de correção
                info_correcao = notas_rastreadas.get(numero_nota, {})
                correcao = info_correcao.get("correcao", "Sem correcao")
                detalhes = info_correcao.get("detalhes", "")
                
                # Monta descrição final
                descricao_final = correcao
                if detalhes:
                    descricao_final = f"{correcao} ({detalhes})"
                
                # Adiciona coluna de correção
                linhas_saida.append(f"{linha};{descricao_final}")
            else:
                linhas_saida.append(linha)
        
        # Escreve arquivo de resultado
        with open(caminho_saida, 'w', encoding='utf-8') as arquivo_saida:
            for linha in linhas_saida:
                arquivo_saida.write(f"{linha}\n")
        
        logging.info(f"CSV atualizado com correcoes: {caminho_saida}")
        return True
    
    except Exception as e:
        logging.error(f"Erro ao atualizar CSV: {e}")
        return False


def extrair_numero_nota(valor_nota):
    """
    Extrai e normaliza número da nota.
    
    Args:
        valor_nota: str ou int - Número da nota (pode ter zeros à esquerda)
    
    Returns:
        str - Número da nota normalizado
    """
    return str(valor_nota).strip()
