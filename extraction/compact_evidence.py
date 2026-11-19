"""Relation-aware retrieval over indivisible semantic evidence units."""
from dataclasses import asdict
import os
from pathlib import Path
from evidence_chunking import POLICY, chunk_units
from evidence_index import EvidenceIndex
from evidence_retrieval import FrozenBGE, RetrievalConfig, RetrievalIndex
from rcec_rule_queries import RuleQueriesMixin
from rcec_structure import StructuralRetrievalMixin
from rcec_score_fusion import ScoreFusionMixin

BEST_FIRST_POOL_CONFIG = RetrievalConfig(
    method='chains', strategy='semantic', chars=0, sentences=1, dense=.8,
    anchor=.2, edge=.45, role_profile='context_expansion', endpoint_weight=.65,
    neighbor_weight=.2, math_weight=.15, attribution_weight=.15, causal_weight=.5,
    beam=24,
)
BEST_FIRST_POOL_PACK = dict(limit=12, retiling=False, chunking='semantic')


class CompactEvidenceIndex(ScoreFusionMixin, StructuralRetrievalMixin, RuleQueriesMixin, RetrievalIndex):
    """Keep the entry-point name; rank whole units without packing or retiling."""
    def __init__(self, markdown, embedder, *, config=None, pack=None):
        config = config or BEST_FIRST_POOL_CONFIG
        if config.strategy != 'semantic':
            raise ValueError('The new pipeline requires semantic chunking')
        self.pack = dict(BEST_FIRST_POOL_PACK)
        super().__init__(markdown, config, embedder, units=chunk_units(markdown))


def bge_path(path=None):
    value = path or os.getenv('BGE_MODEL_DIR')
    return (Path(value).expanduser() if value else
            Path(__file__).resolve().parent/'embedding_files'/'bge-small-en-v1.5')


def add_retrieval_arguments(parser, *, legacy_alias=False):
    parser.add_argument('--evidence-retrieval', choices=('compact','sparse'), default='compact',
                        help='First pool retrieval profile (default: compact)')
    flags = ['--compact-bge']
    if legacy_alias:
        flags.append('--experimental-evidence-bge')
    parser.add_argument(*flags, dest='compact_bge', type=Path,
                        help='Local BGE directory; defaults to BGE_MODEL_DIR or extraction/embedding_files/bge-small-en-v1.5')


def create_index(markdown, retrieval='compact', model_path=None, *, embedder=None):
    if not markdown.strip():
        raise ValueError('Empty source document')
    if retrieval=='sparse':
        return EvidenceIndex(markdown)
    if retrieval!='compact':
        raise ValueError(f'Unknown evidence retrieval profile: {retrieval}')
    return CompactEvidenceIndex(markdown,embedder or FrozenBGE(bge_path(model_path)))


def retrieval_metadata(index):
    if not isinstance(index,CompactEvidenceIndex):
        return dict(profile='sparse',chunking=dict(POLICY))
    return dict(profile='semantic-chains',config=asdict(index.config),
                pack=dict(index.pack),chunking=dict(POLICY),model=FrozenBGE.model,revision=FrozenBGE.revision,
                checksums=dict(FrozenBGE.checksums))
