"""
Utility functions for link prediction
Most code is adapted from authors" implementation of RGCN link prediction:
https://github.com/MichSchli/RelationPrediction

Extended by Giuseppe Futia to export score related to validation triples.
In particular, see the implementation of export_triples_score() function.

"""

import numpy as np
import torch
import dgl
import json
import os

#######################################################################
#
# Utility function for building training and testing graphs
#
#######################################################################


def get_adj_and_degrees(num_nodes, triplets):
    """ Get adjacency list and degrees of the graph
    """
    adj_list = [[] for _ in range(num_nodes)]
    for i, triplet in enumerate(triplets):
        adj_list[triplet[0]].append([i, triplet[2]])
        adj_list[triplet[2]].append([i, triplet[0]])

    degrees = np.array([len(a) for a in adj_list])
    adj_list = [np.array(a) for a in adj_list]
    return adj_list, degrees


def sample_edge_neighborhood(adj_list, degrees, n_triplets, sample_size):
    """ Edge neighborhood sampling to reduce training graph size
    """

    edges = np.zeros((sample_size), dtype=np.int32)

    # initialize
    sample_counts = np.array([d for d in degrees])
    picked = np.array([False for _ in range(n_triplets)])
    seen = np.array([False for _ in degrees])

    for i in range(0, sample_size):
        weights = sample_counts * seen

        if np.sum(weights) == 0:
            weights = np.ones_like(weights)
            weights[np.where(sample_counts == 0)] = 0

        probabilities = (weights) / np.sum(weights)
        chosen_vertex = np.random.choice(np.arange(degrees.shape[0]),
                                         p=probabilities)
        chosen_adj_list = adj_list[chosen_vertex]
        seen[chosen_vertex] = True

        chosen_edge = np.random.choice(np.arange(chosen_adj_list.shape[0]))
        chosen_edge = chosen_adj_list[chosen_edge]
        edge_number = chosen_edge[0]

        while picked[edge_number]:
            chosen_edge = np.random.choice(np.arange(chosen_adj_list.shape[0]))
            chosen_edge = chosen_adj_list[chosen_edge]
            edge_number = chosen_edge[0]

        edges[i] = edge_number
        other_vertex = chosen_edge[1]
        picked[edge_number] = True
        sample_counts[chosen_vertex] -= 1
        sample_counts[other_vertex] -= 1
        seen[other_vertex] = True

    return edges


def generate_sampled_graph_and_labels(triplets, sample_size, split_size,
                                      num_rels, adj_list, degrees,
                                      negative_rate):
    """Get training graph and signals
    First perform edge neighborhood sampling on graph, then perform negative
    sampling to generate negative samples
    """
    # perform edge neighbor sampling
    edges = sample_edge_neighborhood(adj_list, degrees, len(triplets),
                                     sample_size)

    # relabel nodes to have consecutive node ids
    edges = triplets[edges]
    src, rel, dst = edges.transpose()
    uniq_v, edges = np.unique((src, dst), return_inverse=True)
    src, dst = np.reshape(edges, (2, -1))
    relabeled_edges = np.stack((src, rel, dst)).transpose()

    # negative sampling
    samples, labels = negative_sampling(relabeled_edges, len(uniq_v),
                                        negative_rate)

    # further split graph, only half of the edges will be used as graph
    # structure, while the rest half is used as unseen positive samples
    split_size = int(sample_size * split_size)
    graph_split_ids = np.random.choice(np.arange(sample_size),
                                       size=split_size, replace=False)
    src = src[graph_split_ids]
    dst = dst[graph_split_ids]
    rel = rel[graph_split_ids]

    # build DGL graph
    print("# sampled nodes: {}".format(len(uniq_v)))
    print("# sampled edges: {}".format(len(src) * 2))
    g, rel, norm = build_graph_from_triplets(len(uniq_v), num_rels,
                                             (src, rel, dst))
    return g, uniq_v, rel, norm, samples, labels


def comp_deg_norm(g):
    in_deg = g.in_degrees(range(g.number_of_nodes())).float().numpy()
    norm = 1.0 / in_deg
    norm[np.isinf(norm)] = 0
    return norm


def build_graph_from_triplets(num_nodes, num_rels, triplets):
    """ Create a DGL graph. The graph is bidirectional because RGCN authors
        use reversed relations.
        This function also generates edge type and normalization factor
        (reciprocal of node incoming degree)
    """
    g = dgl.DGLGraph()
    g.add_nodes(num_nodes)
    src, rel, dst = triplets
    src, dst = np.concatenate((src, dst)), np.concatenate((dst, src))
    rel = np.concatenate((rel, rel + num_rels))
    edges = sorted(zip(dst, src, rel))
    dst, src, rel = np.array(edges).transpose()
    g.add_edges(src, dst)
    norm = comp_deg_norm(g)
    print("# nodes: {}, # edges: {}".format(num_nodes, len(src)))
    return g, rel, norm


def build_test_graph(num_nodes, num_rels, edges):
    src, rel, dst = edges.transpose()
    print("Test graph:")
    return build_graph_from_triplets(num_nodes, num_rels, (src, rel, dst))


def negative_sampling(pos_samples, num_entity, negative_rate):
    size_of_batch = len(pos_samples)
    num_to_generate = size_of_batch * negative_rate
    neg_samples = np.tile(pos_samples, (negative_rate, 1))
    labels = np.zeros(size_of_batch * (negative_rate + 1), dtype=np.float32)
    labels[: size_of_batch] = 1
    values = np.random.randint(num_entity, size=num_to_generate)
    choices = np.random.uniform(size=num_to_generate)
    subj = choices > 0.5
    obj = choices <= 0.5
    neg_samples[subj, 0] = values[subj]
    neg_samples[obj, 2] = values[obj]

    return np.concatenate((pos_samples, neg_samples)), labels

#######################################################################
#
# Utility function for evaluations
#
#######################################################################


def sort_and_rank(score, target):
    _, indices = torch.sort(score, dim=1, descending=True)
    indices = torch.nonzero(indices == target.view(-1, 1))
    indices = indices[:, 1].view(-1)
    return indices


def perturb_and_get_rank(embedding, w, a, r, b, num_entity, pert_string, epoch, batch_size=100, test_stage=False):
    """ Perturb one element in the triplets
    """
    n_batch = (num_entity + batch_size - 1) // batch_size
    ranks = []
    # list that stores score triples to print filled only during the test_stage
    score_list = []
    for idx in range(n_batch):
        print("batch {} / {}".format(idx, n_batch))
        batch_start = idx * batch_size
        batch_end = min(num_entity, (idx + 1) * batch_size)
        batch_a = a[batch_start: batch_end]
        batch_r = r[batch_start: batch_end]
        emb_ar = embedding[batch_a] * w[batch_r]
        emb_ar = emb_ar.transpose(0, 1).unsqueeze(2)  # size: D x E x 1
        emb_c = embedding.transpose(0, 1).unsqueeze(1)  # size: D x 1 x V
        # out-prod and reduce sum
        out_prod = torch.bmm(emb_ar, emb_c)  # size D x E x V
        score = torch.sum(out_prod, dim=0)  # size E x V
        score = torch.sigmoid(score)
        target = b[batch_start: batch_end]
        ranks.append(sort_and_rank(score, target))

        # export score values during the test stage
        if test_stage == True:
            score_list.extend(export_triples_score(
                batch_a, batch_r, target, embedding, w, score))

    # print the score only during the test stage
    if test_stage == True:
        print_scores_as_json(score_list, pert_string, epoch)

    return torch.cat(ranks)

# TODO (lingfan): implement filtered metrics
# return MRR (raw), and Hits @ (1, 3, 10)


def evaluate(test_graph, model, test_triplets, num_entity, epoch, hits=[], eval_bz=100, test_stage=False):
    with torch.no_grad():
        embedding, w = model.evaluate(test_graph)
        s = test_triplets[:, 0]
        r = test_triplets[:, 1]
        o = test_triplets[:, 2]

        # perturb subject
        ranks_s = perturb_and_get_rank(
            embedding, w, o, r, s, num_entity, "perturb_s", epoch, eval_bz, test_stage)
        # perturb object
        ranks_o = perturb_and_get_rank(
            embedding, w, s, r, o, num_entity, "perturb_o", epoch, eval_bz, test_stage)

        ranks = torch.cat([ranks_s, ranks_o])
        ranks += 1  # change to 1-indexed

        mrr = torch.mean(1.0 / ranks.float())
        print("MRR (raw): {:.6f}".format(mrr.item()))

        for hit in hits:
            avg_count = torch.mean((ranks <= hit).float())
            print("Hits (raw) @ {}: {:.6f}".format(hit, avg_count.item()))
    return mrr.item()

# The following methods are added by Giuseppe Futia


def print_scores_as_json(score_list, perturb_string, epoch):
    dir_path = "./output/epoch_" + str(epoch) + "/"
    print("Print score as json: " + dir_path + "...")
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    with open(dir_path + perturb_string + "_" + "_score.json", "w") as f:
        json.dump(score_list, f, ensure_ascii=False, indent=4)


def create_list_from_batch(batch, embedding):
    """ Create list of dictionaries including the id of the node (or the relation)
        and its embedding value
    """
    batch_list = []
    for index, value in enumerate(batch.tolist()):
        new_dict = {"id": value, "emb": embedding[batch][index, :].tolist()}
        batch_list.append(new_dict)
    return batch_list


def export_triples_score(s, r, o, emb_nodes, emb_rels, score):
    """ Export score associated to each triple included in the validation dataset.
        This function is called for each evaluation batch.
        Exported scores could be useful for a deep analysis of the evaluation
        results and are necessary for the refinement process of the SEMI tool.

        Arguments:
        s -- tensor batch of subject ids
        r -- tensor batch of relation ids
        o -- tensor batch of object ids
        emb_nodes -- tensor with embeddings of all nodes
        emb_rels -- tensor with embeddings of all relations
        score -- tensor of scores associated to eache triple, size(batch, num_of_nodes)

        Returns:
        score_list -- list of dictionaries including triple ids and the associated score
    """
    batch_s_list = create_list_from_batch(s, emb_nodes)
    batch_r_list = create_list_from_batch(r, emb_rels)
    batch_o_list = create_list_from_batch(o, emb_nodes)

    # Prepare a list of dicts containing the triples and its scores
    score_list = []
    for row_index, row in enumerate(score):
        for col_index, col in enumerate(row):
            s_id = str(batch_s_list[row_index]["id"])
            r_id = str(batch_r_list[row_index]["id"])
            o_id = str(batch_o_list[row_index]["id"])
            # score tensor includes also perturbed triples, for such reason
            # I need to get data from the correct column
            if str(col_index) == str(o_id):
                score_value = col
                score_dict = {"s": s_id,
                              "r": r_id,
                              "o": o_id,
                              "score": score_value.item()}
                score_list.append(score_dict)
    return score_list