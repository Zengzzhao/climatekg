"""Bounded scoring alternatives; default exactly preserves early fusion."""
from dataclasses import replace
import numpy as np
from evidence_retrieval import minmax

FUSIONS=('early_linear','late_linear','rrf','geometric','harmonic','agreement','specificity')


def combine(sparse,dense,weight,mode):
    if mode=='late_linear':return (1-weight)*sparse+weight*dense
    if mode=='rrf':
        def rr(row):
            order=np.argsort(-row,kind='stable');ranks=np.empty(len(row),dtype=float);ranks[order]=np.arange(1,len(row)+1)
            return 1/(60+ranks)
        return np.stack([minmax((1-weight)*rr(s)+weight*rr(d)) for s,d in zip(sparse,dense)])
    if mode=='geometric':return (sparse+.02)**(1-weight)*(dense+.02)**weight-.02
    if mode=='harmonic':return 1/((1-weight)/(sparse+.02)+weight/(dense+.02))-.02
    if mode=='agreement':return np.maximum(0,(1-weight)*sparse+weight*dense-.15*np.abs(sparse-dense))
    raise ValueError(f'Unknown score fusion: {mode}')


class ScoreFusionMixin:
    def facet_affinities(self,candidate,anchors):
        cfg=self.config;mode=cfg.score_fusion
        if mode=='early_linear':return super().facet_affinities(candidate,anchors)
        if mode=='specificity':
            labels,affinity=super().facet_affinities(candidate,anchors)
            others=(affinity.sum(axis=0,keepdims=True)-affinity)/max(1,len(affinity)-1)
            return labels,np.stack([minmax(row) for row in affinity-.2*others])
        if mode not in FUSIONS:raise ValueError(f'Unknown score fusion: {mode}')
        if not hasattr(self,'_channel_affinities'):self._channel_affinities={}
        key=(candidate['source_entity_instance'],candidate['target_entity_instance'],candidate['relation'],replace(cfg,score_fusion='early_linear',dense=1.,beam_policy='score'))
        if key not in self._channel_affinities:
            try:
                self.config=replace(cfg,score_fusion='early_linear',dense=0.)
                labels,sparse=super().facet_affinities(candidate,anchors)
                self.config=replace(cfg,score_fusion='early_linear',dense=1.)
                dense_labels,dense=super().facet_affinities(candidate,anchors)
                assert labels==dense_labels
            finally:self.config=cfg
            self._channel_affinities[key]=(labels,sparse,dense)
        labels,sparse,dense=self._channel_affinities[key]
        return labels,combine(sparse,dense,cfg.dense,mode)
