"""Controlled retrieval baselines and two relation-conditioned rankers."""
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import math
import re

import numpy as np

from evidence_chunking import MATH, chunk_units, sentence_ranges
from evidence_index import EvidenceIndex, compact_endpoint_match, entity_match, reading_form, tokens
from evidence_selection import ROLE_SEARCH_TERMS, paths_for


# These describe entailment roles across papers, without paper-specific indicators
# or task terms. A role can retrieve evidence without a literal endpoint mention.
RELATION_FACETS = {
    'alternative_to': (
        ('base', 'The original indicator is proposed or defined to measure a specific evaluation task.', 'target'),
        ('membership', 'A named indicator is introduced as one of the variants or adaptations of the original indicator.', 'source'),
        ('rule', 'If a condition is met, an alternative variant can be used instead of the original indicator for the same task.', 'target'),
    ),
    'improves_on': (
        ('limitation', 'The original indicator is prone to a specific bias, limitation, defect or missing evaluation capability.', 'target'),
        ('rationale', 'A design technique can eliminate, mitigate, address or reduce a limitation of an indicator.', None),
        ('mechanism', 'A concrete method changes or replaces the original computation, inputs, weighting or normalization.', None),
        ('design', 'A named indicator is defined or computed from the revised inputs, weights or measurement design.', 'source'),
    ),
}

# Opt-in corpus profile. Split mathematical dependencies and attribution into
# distinct retrieval layers so a bridge need not repeat the candidate names.
PROOF_FACETS = {
    'component_of': (
        ('input', 'Definition or calculation of the input score, adjusted count, statistic, or feature.', 'source'),
        ('bridge', 'A formula uses a previously computed quantitative result as an input, factor or summand of an intermediate score, or ranks by that specific feature.', None),
        ('output', 'Calculation, formula or aggregation of scores or intermediate rankings into the final composite indicator.', 'target'),
    ),
    'proposes': (
        ('attribution', 'In this paper we construct, introduce or operationalize a quantitative measurement. The current study constructs the measure even if it borrows a formula.', None),
        ('definition', 'The named measure is given by the following equation or formal quantitative definition.', 'target'),
        ('composition', 'The named measure is calculated for each item by combining or aggregating its underlying scores or measurements.', 'target'),
    ),
    'variant_of': (
        ('base', 'Definition and counting scope or time period of the original measure.', 'target'),
        ('comparison', 'The distinction between an original and modified measurement follows the same counting method with a different time window, weight or scope.', None),
        ('variant', 'The named variant is defined with a modified, shorter or longer measurement time period, window, scope or weighting.', 'source'),
    ),
}
STOP = set('a an and any are as at be been both but by can for from had has have how if in into is it its may more of on one or other our same so than that the their then these they this those through to used using was were what when where which who will with would'.split())


def retrieval_query(candidate):
    roles = sorted({q for path in paths_for(candidate) for q in path})
    return ' '.join(roles) + ' ' + ' '.join(ROLE_SEARCH_TERMS[q] for q in roles)


def endpoint_names(candidate):
    source = candidate['source_entity_instance'] if candidate['source_entity_type'] == 'Indicator' else ''
    return source, candidate['target_entity_instance']


def search_tokens(text):
    return [t for t in tokens(text) if t not in STOP]


@dataclass(frozen=True)
class RetrievalConfig:
    method: str = 'sparse'
    strategy: str = 'legacy'
    chars: int = 2600
    sentences: int = 3
    mention: float = 2.
    structure: float = .25
    lexical: float = 1.
    k1: float = 1.2
    b: float = .75
    dense: float = .5
    temperature: float = .08
    anchor: float = .3
    coverage: float = .65
    redundancy: float = .25
    edge: float = .35
    beam: int = 16
    query_style: str = 'default'
    role_profile: str = 'legacy'
    endpoint_weight: float = 0.
    neighbor_weight: float = 0.
    math_weight: float = 0.


RELATION_CHAIN_350 = RetrievalConfig(method='chains', strategy='bounded', chars=350, sentences=2,
                                     dense=.5, anchor=.3, edge=1.5)


class FrozenBGE:
    """BAAI/bge-small-en-v1.5, CLS pooling followed by L2 normalization."""
    model = 'BAAI/bge-small-en-v1.5'
    revision = '160f4d645d32abe3cabc5af6b6b39823eadf3c0e'
    checksums = {
        'model.onnx': '828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35',
        'tokenizer.json': 'd241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66',
    }

    def __init__(self, path):
        import hashlib
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for name, expected in self.checksums.items():
            with (path/name).open('rb') as handle:
                digest = hashlib.sha256()
                for block in iter(lambda: handle.read(1024*1024), b''):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError(f'Unexpected checkpoint hash: {name}')
        self.tokenizer = Tokenizer.from_file(str(path/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 3
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path/'model.onnx'), options, providers=['CPUExecutionProvider'])
        self.cache = {}

    def encode(self, texts, *, query=False):
        prefix = 'Represent this sentence for searching relevant passages: ' if query else ''
        keys = [prefix + reading_form(text) for text in texts]
        missing = sorted(set(keys)-self.cache.keys(), key=lambda text: (len(text), text))
        for i in range(0, len(missing), 16):
            batch = missing[i:i+16]
            encoded = self.tokenizer.encode_batch(batch)
            fields = {'input_ids': 'ids', 'attention_mask': 'attention_mask', 'token_type_ids': 'type_ids'}
            inputs = {inp.name: np.asarray([getattr(x, fields[inp.name]) for x in encoded], dtype=np.int64)
                      for inp in self.session.get_inputs()}
            hidden = self.session.run(None, inputs)[0]
            embeddings = hidden[:, 0, :]
            embeddings /= np.maximum(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-12)
            self.cache.update(zip(batch, embeddings))
        return np.stack([self.cache[k] for k in keys])


def minmax(scores):
    return (scores-scores.min()) / max(1e-9, scores.max()-scores.min())


def cosine_matrix(left, right):
    # Inputs are normalized. einsum avoids spurious Accelerate matmul FP flags.
    return np.einsum('id,jd->ij', left, right)


def rank_fusion(sparse, dense, weight):
    def reciprocal(x):
        order = np.argsort(-x, kind='stable')
        positions = np.argsort(order) + 1
        return 1 / (60 + positions)
    return (1-weight)*reciprocal(sparse) + weight*reciprocal(dense)


class RetrievalIndex(EvidenceIndex):
    """Drop-in index for collect_evidence. Rankers receive no labels or Validator output."""

    def __init__(self, markdown, config, embedder=None, *, units=None):
        if config.strategy == 'legacy' and units is None:
            super().__init__(markdown)
        else:
            import hashlib
            self.markdown = markdown
            self.sha256 = hashlib.sha256(markdown.encode()).hexdigest()
            self.units = units if units is not None else chunk_units(
                markdown, config.strategy, config.chars, config.sentences)
            self.by_id = {u.id:u for u in self.units}
            self.counts = {u.id:Counter(tokens(u.text)) for u in self.units}
            df = Counter(t for c in self.counts.values() for t in c)
            self.idf = {t:math.log(1+(len(self.units)+1)/(n+1)) for t,n in df.items()}
        self.config = config
        self.embedder = embedder
        self.lexical_counts = [Counter(search_tokens(u.text)) for u in self.units]
        self.doclen = np.array([sum(c.values()) for c in self.lexical_counts])
        self.structure = np.array([bool(re.search('abstract|method|definition|conclusion', u.section, re.I))
                                   for u in self.units])
        df = Counter(t for c in self.lexical_counts for t in c)
        self.bm25_idf = {t:math.log1p((len(self.units)-n+.5)/(n+.5)) for t,n in df.items()}
        self.features = {}
        self._sentences = None
        self._overlap = None
        self._graph = None
        self._math = None

    @lru_cache(maxsize=128)
    def bm25(self, query, k1, b):
        query = set(search_tokens(query))
        length = k1*(1-b+b*self.doclen/max(1, self.doclen.mean()))
        return np.asarray([
            sum(self.bm25_idf[t]*tf*(k1+1)/(tf+length[i])
                for t in query & counts.keys() for tf in [counts[t]])
            for i, counts in enumerate(self.lexical_counts)
        ])

    def similarities(self):
        if self._overlap is None:
            starts, ends = (np.asarray(a) for a in zip(*[(u.start,u.end) for u in self.units]))
            common = np.maximum(0, np.minimum(ends[:,None],ends)-np.maximum(starts[:,None],starts))
            length = ends-starts
            self._overlap = common / np.minimum(length[:,None],length)
            vocabulary = {t:i for i,t in enumerate(self.bm25_idf)}
            weights = np.zeros((len(starts), len(vocabulary)), dtype=np.float32)
            for i, counts in enumerate(self.lexical_counts):
                for term, count in counts.items():
                    weights[i, vocabulary[term]] = math.log1p(count) * self.bm25_idf[term]
            weights /= np.maximum(1e-9, np.linalg.norm(weights, axis=1, keepdims=True))
            cosine = cosine_matrix(weights, weights)
            midpoint = (starts+ends)/2
            distance = np.abs(midpoint[:,None]-midpoint)
            sections = np.asarray([u.section for u in self.units])
            proximity = np.exp(-distance/800) * (sections[:,None] == sections)
            self._graph = (.7*cosine+.3*proximity) * (1-self._overlap)
        return self._overlap, self._graph

    def facet_affinities(self, candidate, anchors):
        cfg = self.config
        source, target = endpoint_names(candidate)
        relation = candidate['relation']
        facets = PROOF_FACETS.get(relation) if cfg.role_profile=='proof' else None
        facets = facets or RELATION_FACETS.get(relation)
        if facets is None:
            roles = dict.fromkeys(q for path in paths_for(candidate) for q in path)
            facets = [(q, ROLE_SEARCH_TERMS[q], q.split('_')[0] if q.endswith('_identity') else None)
                      for q in roles]
        facet_labels = [f[0] for f in facets]
        keys = [source, target]
        lexical_scores = []
        for _, text, _ in facets:
            lexical_scores.append(minmax(self.bm25(text, cfg.k1, cfg.b)))
        affinities = np.asarray(lexical_scores)
        if cfg.dense:
            if self._sentences is None:
                self._sentences = [[u.text[a:b] for a,b in sentence_ranges(u.text)] for u in self.units]
            flat = [s for unit in self._sentences for s in unit]
            boundaries = np.cumsum([len(s) for s in self._sentences])[:-1]
            dense_queries = [text + (' Indicator: ' + keys[{'source':0, 'target':1}[side]] if side else '')
                             for _, text, side in facets]
            similarity = cosine_matrix(self.embedder.encode(dense_queries, query=True), self.embedder.encode(flat))
            pooled = []
            for group in np.split(similarity, boundaries, axis=1):
                attention = np.exp((group-group.max(axis=1, keepdims=True))/cfg.temperature)
                pooled.append((attention*group).sum(axis=1)/attention.sum(axis=1))
            affinities = (1-cfg.dense)*affinities + cfg.dense*np.asarray([minmax(row) for row in np.asarray(pooled).T])
        algebra = self.math_support(source,target) if cfg.math_weight else None
        for i, (_, _, endpoint) in enumerate(facets):
            if endpoint:
                side = {'source':0, 'target':1}[endpoint]
                bound = anchors[side]
                if algebra is not None:
                    affinities[i] = (1-cfg.math_weight)*affinities[i] + cfg.math_weight*algebra[side]
                    bound = np.maximum(bound,algebra[side])
                if cfg.endpoint_weight:
                    affinities[i] = (1-cfg.endpoint_weight)*affinities[i] + cfg.endpoint_weight*bound
                affinities[i] = affinities[i] * (1-cfg.anchor+cfg.anchor*bound)
        if cfg.neighbor_weight:
            mid = np.asarray([(u.start+u.end)/2 for u in self.units])
            sections = np.asarray([u.section for u in self.units])
            nearby = np.exp(-np.abs(mid[:,None]-mid)/600) * (sections[:,None]==sections)
            context = np.max(affinities[:,None,:]*nearby,axis=2)
            affinities = (1-cfg.neighbor_weight)*affinities + cfg.neighbor_weight*context
        return facet_labels, affinities

    def math_support(self,source,target):
        if self._math is None:
            self._math = [
                [re.sub(r'[^a-z0-9_=+]', '', re.sub(
                    r'\\(?:mathrm|text|left|right|mathit)\b','',m.group().lower()))
                 for m in MATH.finditer(unit.text)]
                for unit in self.units
            ]
        scores = []
        for endpoint in (source,target):
            bare = re.sub(r'^(?:original|modified|adjusted)\s+|\s+(?:score|indicator|index)$',
                          '',endpoint,flags=re.I)
            key = re.sub(r'[^a-z0-9_]','',bare.lower())
            name = re.compile(r'(?<![a-z0-9])'+re.escape(key)+r'(?![a-z0-9])') if len(key)>=2 else None
            scores.append(np.array([
                max((1. if '=' in s else .45 for s in parts if name and name.search(s)),default=0.)
                for parts in self._math
            ]))
        return scores

    def orient_alternative(self, candidate):
        """Infer proof orientation for a symmetric edge using only document affinities."""
        source, target = endpoint_names(candidate)
        orientations = []
        for left, right in ((source,target),(target,source)):
            hypothesis = dict(candidate,source_entity_instance=left,target_entity_instance=right)
            anchors = np.asarray([[entity_match(n,u.text) for u in self.units] for n in (left,right)])
            _, affinities = self.facet_affinities(hypothesis, anchors)
            orientations.append((float(affinities.max(axis=1).mean()), left, right, hypothesis))
        return max(orientations,key=lambda row:row[:3])[-1]

    def rank(self, candidate, query, excluded=(), limit=12):
        cfg = self.config
        source, target = endpoint_names(candidate)
        key = source, target, candidate['relation'], query, cfg
        if key not in self.features:
            match = compact_endpoint_match if cfg.role_profile=='proof' else entity_match
            anchors = np.asarray([[bool(name) and match(name,u.text) for u in self.units]
                                  for name in (source, target)])
            facets = ' '.join(f[1] for f in RELATION_FACETS.get(candidate['relation'], ()))
            if cfg.method == 'sparse':
                terms = set(tokens(f'{source} {target} {facets if cfg.query_style == "facets" else query}'))
                raw = np.asarray([
                    sum(self.idf.get(t,0)*min(c[t],3) for t in terms)/math.sqrt(max(20,sum(c.values())))
                    for c in self.counts.values()
                ])
                scores = cfg.lexical*raw + cfg.mention*anchors.sum(axis=0) + cfg.structure*self.structure
                self.features[key] = scores, None
            else:
                text = f'{source} {target} ' + ((facets or query) if cfg.query_style != 'legacy' else query)
                if query != retrieval_query(candidate):
                    text += ' ' + query
                bm25 = self.bm25(text, cfg.k1, cfg.b)
                if cfg.method in {'dense', 'hybrid'}:
                    cosine = cosine_matrix(self.embedder.encode([text], query=True),
                                           self.embedder.encode([u.text for u in self.units]))[0]
                    scores = cosine if cfg.method == 'dense' else rank_fusion(bm25, cosine, cfg.dense)
                    self.features[key] = scores, None
                elif cfg.method == 'bm25':
                    self.features[key] = bm25, None
                elif cfg.method in {'facets', 'chains'}:
                    labels, scores_by_facet = self.facet_affinities(candidate, anchors)
                    self.features[key] = minmax(bm25), (labels, scores_by_facet)
                else:
                    raise ValueError(f'Unknown retrieval method {cfg.method}')
        base, roles = self.features[key]
        excluded = set(excluded)
        available = np.asarray([u.id not in excluded for u in self.units])
        if roles is None:
            return [(float(base[i]), self.units[i]) for i in np.argsort(-base, kind='stable') if available[i]][:limit]
        _, affinity = roles
        overlap, graph = self.similarities()
        if cfg.method == 'facets':
            return self.rank_facets(base, affinity, overlap, available, limit)
        return self.rank_chains(base, affinity, overlap, graph, available, limit)

    def rank_facets(self, base, affinity, overlap, available, limit):
        """Select by marginal soft coverage of complementary relation conditions."""
        cfg = self.config
        best = np.zeros(affinity.shape[0])
        selected = []
        for _ in range(min(limit, int(available.sum()))):
            gain = np.maximum(0, affinity-best[:,None]).mean(axis=0)
            redundancy = overlap[:,[i for _,i in selected]].max(axis=1) if selected else 0
            utility = ((1-cfg.coverage)*(.45*base+.55*affinity.max(axis=0))
                       + cfg.coverage*gain-cfg.redundancy*redundancy)
            i = int(np.argmax(np.where(available,utility,-np.inf)))
            selected.append((float(utility[i]), i))
            available[i] = False
            best = np.maximum(best, affinity[:,i])
        return self.ranked_units(selected)

    def rank_chains(self, base, affinity, overlap, graph, available, limit):
        """Beam search over typed evidence chains, then pool their distinct witnesses."""
        cfg = self.config
        states = [(0., ())]
        for role in affinity:
            choices = [int(i) for i in np.argsort(-role,kind='stable') if available[i]][:cfg.beam]
            extensions = []
            for score, path in states:
                for i in choices:
                    transition = 0
                    if path:
                        last = path[-1]
                        # A single original may witness multiple steps of the chain.
                        if last != i:
                            transition = cfg.edge*graph[last,i]-cfg.redundancy*overlap[last,i]
                    extensions.append((score+float(role[i])+transition, path+(i,)))
            states = sorted(extensions,key=lambda s:(-s[0],s[1]))[:cfg.beam]
        selected = []
        for score, path in states:
            for i in path:
                if len(selected) == limit:
                    return self.ranked_units(selected)
                if available[i]:
                    selected.append((score/affinity.shape[0], i))
                    available[i] = False
        for i in np.argsort(-(.5*base+.5*affinity.max(axis=0)),kind='stable'):
            if len(selected) == limit:
                break
            if available[i]:
                selected.append((float(base[i]), int(i)))
        return self.ranked_units(selected)

    def ranked_units(self, selected):
        # The existing selector re-sorts by score. Preserve set/chain retrieval order.
        return [(float(-rank), self.units[i]) for rank, (_,i) in enumerate(selected)]
