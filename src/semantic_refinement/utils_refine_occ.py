import torch
import numpy as np
import csv
import json
import os
import shutil
import subprocess
from subprocess import Popen, PIPE
import re
import urllib.parse
import rdflib
from flashtext import KeywordProcessor
keyword_processor = KeywordProcessor()


def get_dictionaries(entities_dict_path, relations_dict_path):
    f = open(entities_dict_path)
    entities = csv.reader(f, delimiter='\t')
    entities_dict = {}
    for e in entities:
        entities_dict[e[1]] = e[0]

    f = open(relations_dict_path)
    relations = csv.reader(f, delimiter='\t')
    relations_dict = {}
    for r in relations:
        relations_dict[r[1]] = r[0]

    return entities_dict, relations_dict


def get_embeddings(entities_emb_path, relations_emb_path):
    f = open(entities_emb_path)
    entities_emb = json.load(f)
    entities_emb_dict = {}
    for e in entities_emb:
        entities_emb_dict[str(e['id'])] = e['emb']

    f = open(relations_emb_path)
    relation_emb = json.load(f)
    relations_emb_dict = {}
    for r in relation_emb:
        relations_emb_dict[str(r['id'])] = r['emb']

    return entities_emb_dict, relations_emb_dict


def encode_rdf_st(rdf_path):
    f = open(os.path.join(rdf_path), 'r')
    rdf_text = f.read()

    # Get the URIs included within diamonds <> and encode them
    matches = re.findall(r'\<(.*?)\>', rdf_text)
    matches = list(
        map((lambda x: {x: urllib.parse.quote(x, safe='http://')}), matches))

    for index in range(len(matches)):
        for key in matches[index]:
            keyword_processor.add_keyword(key, matches[index][key])

    rdf_updated = keyword_processor.replace_keywords(rdf_text)

    return rdf_updated


def get_target_embeddings(rdf, entities_dict, entities_emb_dict):
    entities_source_dict = {}
    g = rdflib.Graph()
    graph = g.parse(data=rdf, format='turtle')
    for s, p, o in g:
        s = str(s)
        id = entities_dict[s]
        if entities_source_dict.get(s) == None:
            entities_source_dict[s] = {}
            entities_source_dict[s]['id'] = id
            entities_source_dict[s]['emb'] = entities_emb_dict[id]

    return entities_source_dict


def get_relations_embeddings(relations_dict, relations_emb_dict):
    relations_learnt_dict = {}
    for k, v in relations_dict.items():
        relations_learnt_dict[k] = {}
        relations_learnt_dict[k]['id'] = v
        relations_learnt_dict[k]['emb'] = relations_emb_dict[v]

    return relations_learnt_dict


def get_semantic_relations(jarql_path, relations_emb_dict):
    f = open(jarql_path)
    jarql = f.read()

    # Get semantic model triples
    matches = re.findall(r'\?(.*?)\.', jarql)

    # Remove semantic types
    matches = list(filter(lambda x: (not 'rdf:type' in x), matches))

    # Expand urls of the relations
    semantic_relations = []
    for k, v in PREFIX.items():
        for triple in matches:
            if v in triple:
                triple = triple.replace(
                    v, urllib.parse.quote(k, safe='http://'))
                semantic_relations.append(triple)

    # Filter using the relations embedding
    filtered_relations = []
    for k in relations_emb_dict.items():
        for triple in semantic_relations:
            if k[0] == triple.split(' ')[1]:
                filtered_relations.append(triple)

    return filtered_relations


def create_relation_jarqls(jarql_path, relations, refined_path):
    f = open(jarql_path)
    jarql = f.read()

    # Prepare output path for JARQLs (create folder if not exist)
    source_name = os.path.basename(jarql_path).replace('.query', '')
    output_path = refined_path + 'relations/' + source_name + '/jarql/'

    if not os.path.isdir(output_path):
        try:
            os.makedirs(output_path)
        except OSError:
            print ('** WARNING: Creation of the directory %s failed' %
                   output_path)
        else:
            print ('Successfully created the directory %s ' % output_path)

    # Remove anything in the CONSTRUCT {..} and fill it with the relation
    # XXX This approach to clean works only in the case of JARQL generated by SeMi: need to be improved
    first_pos = jarql.find('CONSTRUCT {')
    last_pos = jarql.find('WHERE')

    for r in relations:
        # XXX Should filter some relations that are not object properties
        if len(r.split(' ')) != 3:
            print('    *** WARNING: the relation has more than 3 elements: ' + r)

        predicate = r.split(' ')[1]

        if (predicate != 'http://schema.org/description'):
            # Reduce url of the predicate to the name space
            new_predicate = from_uri_to_ns(predicate)
            r = r.replace(predicate, new_predicate)

            # Fill the CONSTRUCT with the JARQL
            clean_jarql = jarql.replace(
                jarql[first_pos + 12:last_pos - 3], '    ?' + r + '.')

            # Print
            f = open(output_path + r.replace(' ', '***') + '.query', 'w')
            f.write(clean_jarql)
            f.close()


def create_relation_rdfs(jarql_path, refined_path, source_path):
    # Prepare output path for RDFs (create folder if not exist)
    source_name = os.path.basename(jarql_path).replace('.query', '')
    input_path = refined_path + 'relations/' + source_name + '/jarql/'
    output_path = refined_path + 'relations/' + source_name + '/rdf/'

    if not os.path.isdir(output_path):
        try:
            os.makedirs(output_path)
        except OSError:
            print ('** WARNING: Creation of the directory %s failed' %
                   output_path)
        else:
            print ('Successfully created the directory %s ' % output_path)

    # Loop on JARQL files including the relations
    jarql_files = [f for f in os.listdir(
        input_path) if os.path.isfile(os.path.join(input_path, f))]

    for j in jarql_files:
        # Run the JARQL script and store RDF files
        j = j.replace('.query', '')
        session = subprocess.check_call('./jarql.sh' +
                                        ' ' + source_path +
                                        ' ' + os.path.join(input_path, j + '.query') +
                                        ' > ' + os.path.join(output_path, j + '.rdf'), shell=True)


def compute_triple_score_avg(source_name, refined_path, entities_emb, relations_emb, aggregated, evaluation_path):
    # Dictionary to store details for analysis
    predicates_results = {}
    results = {}

    # Load RDF files of learnt relations
    rdf_paths = refined_path + 'relations/' + source_name + '/rdf/'
    rdf_files = [f for f in os.listdir(
        rdf_paths) if os.path.isfile(os.path.join(rdf_paths, f))]

    # Initialize dictionaries for predicates
    for file in rdf_files:
        triple = file.replace('.rdf', '')
        predicate = triple.split('***')[1]
        if predicate not in predicates_results:
            predicates_results[predicate] = {}
            predicates_results[predicate]['occurrences'] = 0
            predicates_results[predicate]['aggregated_score'] = 0
            predicates_results[predicate]['average_score'] = 0

    # Prepare dictionaries for triples
    triple_scores_sum = {}
    triple_occs = {}
    triple_scores_avg = {}

    # Parse RDF files as RDF Graphs
    for file in rdf_files:
        triple = file.replace('.rdf', '')
        predicate = triple.split('***')[1]

        # Initialize for the triples
        results[triple] = {}
        triple_scores_sum[triple] = 0
        triple_occs[triple] = 0
        triple_scores_avg[triple] = 0

        f = open(rdf_paths + file)
        rdf = f.read()

        # Get the URIs included within diamonds <> and encode them
        # TODO: should be put as utils clearner
        matches = re.findall(r'\<(.*?)\>', rdf)
        matches = list(
            map((lambda x: {x: urllib.parse.quote(x, safe='http://')}), matches))

        for index in range(len(matches)):
            for key in matches[index]:
                keyword_processor.add_keyword(key, matches[index][key])

        rdf_updated = keyword_processor.replace_keywords(rdf)
        # End of cleaning process

        g = rdflib.Graph()
        graph = g.parse(data=rdf_updated, format='turtle')

        for s, p, o in g:
            # Get embeddings
            emb_s = np.asarray(entities_emb[str(s)]['emb'])
            emb_p = np.asarray(relations_emb[str(p)]['emb'])
            emb_o = np.asarray(entities_emb[str(o)]['emb'])

            score = calc_score(emb_s, emb_p, emb_o)

            triple_occs[triple] += 1
            triple_scores_sum[triple] += score.item()
            predicates_results[predicate]['occurrences'] += 1
            predicates_results[predicate]['aggregated_score'] += score.item()

        # If there are no RDF occurrencies of the triple semantic model, the final score should be very low
        if triple_occs[triple] == 0:
            triple_occs[triple] = 1

        # Compute the average
        triple_scores_avg[triple] = triple_scores_sum[triple] / \
            triple_occs[triple]

        triple_scores_sum[triple] = triple_scores_sum[triple] * \
            triple_occs[triple]

        # Store results on the triple
        results[triple]['triple_occurrences'] = triple_occs[triple]
        results[triple]['triple_aggregated_score'] = triple_scores_sum[triple]
        results[triple]['triple_average_score'] = triple_scores_avg[triple]

        if aggregated == 'aggregated':
            results[triple]['method'] = 'aggregated'
        else:
            results[triple]['method'] = 'average'

    # Finalize results to store
    for p in predicates_results:
        predicates_results[p]['average_score'] = predicates_results[p]['aggregated_score'] / \
            predicates_results[p]['occurrences']

    for triple in results:
        p = triple.split('***')[1]
        results[triple]['predicate_occurrences'] = predicates_results[p]['occurrences']
        results[triple]['predicate_aggregated_score'] = predicates_results[p]['aggregated_score']
        results[triple]['predicate_average_score'] = predicates_results[p]['average_score']

    # Store the final results in a json file
    results_path = evaluation_path + '/results/details/' + source_name + '.json'
    f = open(results_path, 'w')
    json.dump(results, f, indent=4)

    if aggregated == 'aggregated':
        return triple_occs
    else:
        return triple_scores_avg


def update_weights_plausible_semantic_model(plausible_json_path, complete_rdf_path, plausible_refined_path, scores):
    # Load the complete.nt file adopted for training and evaluation as RDF Graph
    f = open(complete_rdf_path)
    rdf = f.read()
    g = rdflib.Graph()
    graph = g.parse(data=rdf, format='turtle')

    # Count the occurrences of each predicate
    predicates_occs = {}

    for k, v in scores.items():
        # Extract predicate from the semantic model triple
        predicate = k.split('***')[1]
        predicates_occs[predicate] = 0

        # Get the long uri of the predicate
        long_uri_predicate = from_ns_to_uri(predicate)

        for s, p, o in g:
            if str(p) == long_uri_predicate:
                predicates_occs[predicate] += 1

    # Load JSON serialization of the plausible semantic model, whose weights need to be updated
    f = open(plausible_json_path)
    plausible_sm = json.load(f)

    # Process predicate scores and update weights in the plausible semantic model
    for k, v in scores.items():
        # Extract predicate from the semantic model triple
        subject = k.split('***')[0]
        predicate = k.split('***')[1]
        object = k.split('***')[2]

        # Check if the score value of the semantic relation is lower than the predicate reciprocal occurrences
        if v < predicates_occs[predicate]:

            # Get all edges that are not derived from semantic types
            edges = list(filter(lambda e: e['value']['type'] !=
                                'st_property_uri', plausible_sm['edges']))

            # Update weights in the plausible semantic model
            for e in edges:
                e_subject = e['name'].split('***')[0].split(':')[1]
                e_object = e['name'].split('***')[1].split(':')[1]
                subject = subject.replace('?', '')
                object = object.replace('?', '')

                if e_subject == subject and e_object == object and e['value']['label'] == predicate:
                    if v == 0:  # XXX Sometimes the score is equals to 0, because the JARQL does not generate triples
                        v = 0.01
                        print(
                            '\n    WARNING: the score is equal to 0. It is set to 0.01')

                    e['weight'] = 1 / v
                    e['value']['weight'] = 1 / v
                    e['value']['type'] = 'updated'

    # Store updated semantic models
    f = open(plausible_refined_path, 'w')
    json.dump(plausible_sm, f, indent=4)


def compute_steiner_tree(semantic_type_path, refined_path, output_path):
    subprocess.check_call('node run/steiner_tree.js' +
                          ' ' + semantic_type_path +
                          ' ' + refined_path +
                          ' ' + output_path, shell=True)


def create_refined_jarql(semantic_type_path, classes_path, refined_steiner_path, refined_jarql_path, eval_jarql_path):
    subprocess.check_call('node run/jarql.js' +
                          ' ' + semantic_type_path +
                          ' ' + refined_steiner_path +
                          ' ' + classes_path +
                          ' ' + refined_jarql_path, shell=True)

    # Copy the file for the evaluation purpose
    shutil.copyfile(refined_jarql_path + '.query', eval_jarql_path)


'''
Utils functions
'''


def calc_score(s_emb, p_emb, o_emb):
    # DistMult
    s = torch.from_numpy(s_emb)
    r = torch.from_numpy(p_emb)
    o = torch.from_numpy(o_emb)
    score = torch.sigmoid(torch.sum(s * r * o, dim=0))

    return score


def from_uri_to_ns(long_uri):
    for k, v in PREFIX.items():
        if k in long_uri:
            ns = long_uri.replace(k, v)

    return ns


def from_ns_to_uri(ns):
    for k, v in PREFIX.items():
        if v.replace(':', '') == ns.split(':')[0]:
            long_uri = k + ns.split(':')[1]

    return long_uri


PREFIX = {
    'http://schema.org/': 'schema:',
    'http://www.w3.org/2000/01/rdf-schema#': 'rdfs:',
    'http://purl.org/procurement/public-contracts#': 'pc:',
    'http://purl.org/goodrelations/v1#': 'gr:',
    'http://www.w3.org/2002/07/owl#': 'owl:',
    'http://www.w3.org/ns/adms#': 'adms:',
    'http://vocab.deri.ie/c4n#': 'c4n:',
    'http://purl.org/dc/terms/': 'dcterms:',
    'http://xmlns.com/foaf/0.1/': 'foaf:',
    'http://www.loted.eu/ontology#': 'loted:',
    'http://reference.data.gov.uk/def/payment#': 'payment:',
    'http://purl.org/linked-data/cube#': 'qb:',
    'http://www.w3.org/1999/02/22-rdf-syntax-ns#': 'rdf:',
    'http://www.w3.org/2004/02/skos/core#': 'skos:',
    'http://purl.org/vocab/vann/': 'vann:',
    'http://www.w3.org/2001/XMLSchema#': 'xsd:',
    'http://www.w3.org/2002/07/owl#': 'owl:',
    'http://conference#': 'conference:',
    'http://cmt#': 'cmt:',
    'http://sigkdd#': 'sigkdd:',
    'http://erlangen-crm.org/current/': 'crm:',
    'http://collection.britishmuseum.org/id/': 'id:',
    'http://collection.britishmuseum.org/id/thesauri/': 'idThes:',
    'http://www.w3.org/ns/prov#': 'prov:',
    'http://collection.britishmuseum.org/id/ontology/': 'bmo:',
    'http://www.americanartcollaborative.org/ontology/': 'aac-ont:',
    'http://www.w3.org/2008/05/skos#': 'skos2:',
    'http://rdvocab.info/ElementsGr2/': 'ElementsGr2:',
    'http://rdvocab.info/uri/schema/FRBRentitiesRDA/': 'rdvocab-schema:',
    'http://www.europeana.eu/schemas/edm/': 'edm:',
    'http://schema.dig.isi.edu/ontology/': 'schema-dig:',
    'http://www.w3.org/2003/01/geo/wgs84_pos#': 'geo:',
    'http://purl.org/vocab/frbr/core#': 'frbr:',
    'http://www.w3.org/2000/10/swap/pim/contact#': 'swap:',
    'http://www.cidoc-crm.org/rdfs/cidoc-crm#': 'cidoc-crm:',
    'http://metadata.net/harmony/abc#': 'abc:',
    'http://www.loa-cnr.it/ontologies/DOLCE-Lite.owl#': 'DOLCE-Lite:',
    'http://purl.org/dc/dcmitype/': 'dcmitype:',
    'http://web.resource.org/cc/': 'msg0:',
    'http://www.isi.edu/~pan/damltime/time-entry.owl#': 'time-entry:',
    'http://xmlns.com/wordnet/1.6/Work~2': 'wordnet:',
    'http://americanart.si.edu/ontology/': 'saam-ont:',
    'http://www.openarchives.org/ore/terms/': 'ore:',
    'http://scharp.usc.isi.edu/ontology/': 'scharp:',
    'http://dig.isi.edu/ontology/memex/': 'memex:',
    'http://purl.org/dc/elements/1.1/': 'dc:'
}
