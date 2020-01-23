import requests
import json
import xmltodict
import os

# This script is adopted to:
# 1. Download public contracts data from XMLs available in urls
# 2. Generate a complete RDF file using a JARQL-serialized mapping

urls = [
    # 'https://www.swas.polito.it/services/avcp/avcp_dataset_2015_annorif_2015.xml',
    # 'https://www.swas.polito.it/services/avcp/avcp_dataset_2016_annorif_2016.xml',
    'https://www.swas.polito.it/services/avcp/avcp_dataset_2017_annorif_2017_1.xml',
    'https://www.swas.polito.it/services/avcp/avcp_dataset_2018_annorif_2018_1.xml',
    'https://www.unito.it/sites/default/files/legge190/2018/dataset_2018_00027.xml',
    'https://fire.rettorato.unito.it/gestione_bandi_gara/xml/dataset1.xml',
    'https://www.unito.it/sites/default/files/legge190/2017/dataset_2017_00007.xml',
    'https://www.poliziadistato.it/statics/15/autocentro_ca_2013.xml',
    'https://www.poliziadistato.it/statics/20/qca_2013.xml',
]

for url in urls:
    res = requests.get(url)
    xml = '<' + res.text.split('<', 1)[-1]
    JSONrecord = json.loads(json.dumps(xmltodict.parse(xml)))

    print('Download XML files and store single contracts as json...\n')

    for lotto in JSONrecord['legge190:pubblicazione']['data']['lotto']:
        # XXX Sometimes the cig is missing
        if lotto['cig'] is not None:
            filename = 'input/' + lotto['cig'] + '.json'
            with open(filename, 'w', encoding='utf-8') as file:
                file.write(json.dumps(lotto, indent=2))

            # Create rdf files only from stored JSONs
            os.system('java -jar ../../jarql-1.0.1-SNAPSHOT.jar '
                      + filename + ' ../training/pc/pc.query >> ../training/pc/complete.ttl')

    print('Download and processing complete!')