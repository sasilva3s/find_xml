import base64
import xml.etree.ElementTree as ET

class DecodeBase64:
    def __init__(self, xml_file):
        self.xml_file = xml_file

    def decode(self):
        xml_encoded = base64.b64decode(self.xml_file)
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
        root = ET.fromstring(xml_encoded)
        cstat = root.find(".//nfe:protNFe/nfe:infProt/nfe:cStat", ns)
        if cstat is not None:
            return cstat.text
        else:
            cstat = root.find(".//nfe:infNFe/nfe:ide/nfe:xJust", ns)
            return cstat.text



