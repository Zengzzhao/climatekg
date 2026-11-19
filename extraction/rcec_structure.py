"""Candidate fusion and coverage structures; no annotation-dependent logic."""
from dataclasses import replace
import numpy as np
import re
from evidence_retrieval import RetrievalIndex,endpoint_names,minmax

STRUCTURES=('chains','fusion','coverage','seeded_coverage','repair2','repair4','graph_walk')


class StructuralRetrievalMixin:
    def rank(self,candidate,query,excluded=(),limit=12):
        if self.config.pool_structure=='chains':return super().rank(candidate,query,excluded,limit)
        if self.config.pool_structure not in STRUCTURES:raise ValueError('Unknown RCEC structure')
        excluded=set(excluded)
        chain=super().rank(candidate,query,excluded,max(48,4*limit))
        if not chain:return []
        s,t=endpoint_names(candidate)
        base,(_,affinity)=self.features[(s,t,candidate['relation'],query,self.config)]
        overlap,graph=self.similarities()
        if not hasattr(self,'_structure_hybrid'):
            self._structure_hybrid=RetrievalIndex(self.markdown,replace(self.config,method='hybrid',pool_structure='chains'),self.embedder,units=self.units)
        hybrid=self._structure_hybrid.rank(candidate,query,excluded,max(50,4*limit))
        positions={u.id:i for i,u in enumerate(self.units)}
        relevance=minmax(.4*base+.4*affinity.mean(axis=0)+.2*affinity.max(axis=0))
        global_order=[int(i) for i in np.argsort(-relevance,kind='stable') if self.units[i].id not in excluded][:max(30,3*limit)]
        lists=[[positions[u.id] for _,u in chain],[positions[u.id] for _,u in hybrid],global_order]
        bank=set(i for rows in lists for i in rows)
        for role in affinity:
            bank.update(int(i) for i in np.argsort(-role,kind='stable')[:12] if self.units[i].id not in excluded)
        # Candidate context closure adds only adjacent complete original units.
        for i in lists[0][:12]:
            for j in (i-1,i+1):
                if 0<=j<len(self.units) and self.units[j].id not in excluded and self.units[i].section==self.units[j].section:
                    bank.add(j)
        fused=np.zeros(len(self.units))
        for weight,rows in zip((.6,.2,.2),lists):
            for rank,i in enumerate(rows):fused[i]+=weight/(60+rank+1)
        fused=minmax(fused)
        mode=self.config.pool_structure
        if mode=='fusion':
            picked=sorted(bank,key=lambda i:(-fused[i],self.units[i].start))[:limit]
        elif mode=='graph_walk':
            ids=sorted(bank);g=graph[np.ix_(ids,ids)].copy();np.fill_diagonal(g,0)
            g/=np.maximum(1e-9,g.sum(axis=1,keepdims=True));seed=.5*fused[ids]+.5*relevance[ids];seed/=max(1e-9,seed.sum());score=seed.copy()
            for _ in range(3):score=.8*seed+.2*(g.T@score)
            picked=[ids[int(i)] for i in np.argsort(-score,kind='stable')[:limit]]
        else:
            keep={'coverage':0,'seeded_coverage':3,'repair2':max(0,limit-2),'repair4':max(0,limit-4)}[mode]
            picked=lists[0][:min(keep,limit)]
            best=affinity[:,picked].max(axis=1) if picked else np.zeros(affinity.shape[0])
            while len(picked)<min(limit,len(bank)):
                options=sorted(bank-set(picked))
                gain=np.maximum(0,affinity[:,options]-best[:,None]).mean(axis=0)
                if picked:
                    redundancy=graph[np.ix_(options,picked)].max(axis=1)
                    adjacency=np.asarray([max((float(abs(i-j)==1 and self.units[i].section==self.units[j].section) for j in picked),default=0.) for i in options])
                    cues=np.asarray([bool(re.match(r'^(?:where|however|these|this|such|further|then|now)\b',self.units[i].text,re.I)) for i in options])
                    connection=adjacency*(.5+.5*cues)
                else:redundancy=np.zeros(len(options));connection=np.zeros(len(options))
                utility=.35*relevance[options]+.2*fused[options]+.45*gain-.1*redundancy
                if mode.startswith('repair'):utility+=.15*connection
                i=options[int(np.argmax(utility))];picked.append(i);best=np.maximum(best,affinity[:,i])
        return [(-float(rank),self.units[i]) for rank,i in enumerate(picked)]
