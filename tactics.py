"""Bounded information-set planning using the unchanged engine on hypothetical worlds.

No live GameState enters this module. Worlds contain a hypothesized hidden card,
shuffled unseen deck and sampled opponent trumps, never server secrets. Engine
functions are rebound to private globals so planning cannot consume game RNG.
"""
import math
import random
import types
from functools import lru_cache
from dataclasses import replace

import re7_21 as engine


def simulator(seed):
    scope = dict(vars(engine), random=random.Random(seed), SETTINGS=dict(engine.SETTINGS), WEIGHTS=dict(engine.WEIGHTS))
    methods = {name: types.FunctionType(fn.__code__, scope, name, fn.__defaults__, fn.__closure__)
               for name, fn in vars(engine.GameState).items() if isinstance(fn, types.FunctionType)}
    return type('Hypothesis', (engine.GameState,), methods)


def clone(gs):
    out = object.__new__(type(gs))
    out.__dict__ = dict(gs.__dict__)
    for key in ('deck','p1_hand','p2_hand','p1_trumps','p2_trumps'):
        setattr(out,key,list(getattr(gs,key)))
    out.active_trumps = [dict(t) for t in gs.active_trumps]
    return out


def legal(gs,pid,card=None):
    enemy = [t for t in gs.active_trumps if t['owner']!=pid]
    if card is None:
        return bool(gs.deck) and not gs.check_bust(pid) and not any(t['type'] in ('SILENCE','GAMBLE') for t in enemy)
    if any(t['type']=='DESTROY_BLOCK' for t in enemy): return False
    own = [t for t in gs.active_trumps if t['owner']==pid]
    if card[1]=='DRAW_SPEC':
        # Only use information available to this player. Never consult the
        # sampled enemy hole card or deck membership: unknown numbers can probe.
        known = getattr(gs,f'p{pid}_hand') + getattr(gs,f'p{3-pid}_hand')[1:]
        unavailable = card[2] in known or not gs.deck or any(t['type'] in ('SILENCE','GAMBLE') for t in enemy)
        secondary = any(t['type']=='HARVEST' for t in own) or any(t['type'] in ('FORCE_CONSUME','FORCE_CONSUME_PLUS') for t in enemy)
        if unavailable and not secondary: return False
    return len(own)<engine.MAX_TABLE_SLOTS or card[1] in ('SHIELD_ATTACK','SHIELD_ATTACK_PLUS','OBLIVION') or (card[1]=='TARGET' and any(t['type']=='TARGET' for t in own))


def play(gs,pid,action):
    out = clone(gs)
    hand = getattr(out,f'p{pid}_trumps')
    kind,card = action
    if kind=='TRUMP':
        if card not in hand or not legal(out,pid,card): return None
        out.use_trump(pid,hand.index(card))
        if any(t['owner']==pid and t['type']=='HARVEST' for t in out.active_trumps): out.give_trump(pid,1)
        setattr(out,f'p{3-pid}_stop',False)
    elif kind=='DISCARD':
        if card not in hand: return None
        out.discard_trump(pid,hand.index(card))
        setattr(out,f'p{pid}_stop',False)
    elif kind=='HIT':
        if not legal(out,pid): return None
        out.draw_card(pid)
        setattr(out,f'p{pid}_stop',False)
    else:
        setattr(out,f'p{pid}_stop',True)
    return out


def reserve(card):
    # Valuable rescue/counter cards survive small, safe rounds for later use.
    return {'PERFECT':.28,'RETURN':.25,'TARGET':.24,'DESTROY':.28,'DESTROY_BLOCK':.4,
            'CHANGE':.24,'OBLIVION':.35,'CURSE':.3,'SHIELD':.13,'ADD':.10}.get(card[1],.15)


def score(gs):
    a,b = sum(gs.p1_hand),sum(gs.p2_hand)
    target = gs.target_score
    if a>target and b>target: winner=(b>a)-(a>b)
    elif a>target: winner=-1
    elif b>target: winner=1
    else: winner=(a>b)-(a<b)
    outgoing,incoming = gs.calculate_potential_damage(2),gs.calculate_potential_damage(1)
    gain = min(gs.p2_fingers,outgoing)+ (12 if outgoing>=gs.p2_fingers else 0)
    loss = min(gs.p1_fingers,incoming)+ (15 if incoming>=gs.p1_fingers else 0)
    value = gain if winner>0 else -loss if winner<0 else 0
    if any(t['type']=='ESCAPE' for t in gs.active_trumps): value=-1.5
    ours,theirs = gs.p1_trumps,gs.p2_trumps
    for t in gs.active_trumps:
        if t['type'] in ('FORCE_CONSUME','FORCE_CONSUME_PLUS'):
            factor = 1 if t['type'].endswith('PLUS') else .5
            value += (.16*len(theirs) if t['owner']==1 else -.20*len(ours))*factor
    value += getattr(gs,'reserve_scale',1.)*(sum(map(reserve,ours))-.75*sum(map(reserve,theirs)))
    # A small tie-breaker, never a substitute for winning/damage/lethal outcomes.
    value += .015*((a if a<=target else -a)-(b if b<=target else -b))
    return value


class Planner:
    @staticmethod
    def number_draw_pending(v):
        # Do not replan a successful targeted draw into a blind draw in the
        # same turn. Failed probes and a card already returned are different.
        for event in reversed(v.events):
            _,actor,action,kind,number,before,opp,after,after_opp,target,locked=event
            if actor==2 or action in ('hit','stay'):break
            if kind in ('DRAW_SPEC','DRAW_SPEC_PLUS') and len(after)>len(before):
                return number in v.hand[1:] and len(v.hand)-1>len(before)
        return False

    @staticmethod
    def certainly_ahead(v):
        """A current win for EVERY possible hole card, not just likely samples.

        Recomputed from each new observation: staying is a one-turn choice,
        never a latch that prevents responding after the opponent acts.
        """
        total=sum(v.hand)
        hidden=[n for n in v.numbers if n not in v.hand+v.opponent_visible]
        visible=sum(v.opponent_visible)
        return total<=v.target and bool(hidden) and all(visible+n<total or visible+n>v.target for n in hidden)

    def __init__(self,rng,nightmare=False):
        self.rng,self.nightmare=rng,nightmare
        self.key=None
        self.seen=set()
        self.belief={}
        self.last_plan=()
        self.pending_probe=None

    def protected_probe(self,v,belief,extra):
        """Buy information only when it changes a consequential decision.

        Successful probes that bust are immediately returned; a failed probe
        must never return an unrelated card. Only public posterior branches
        enter the value calculation, not sampled private opponent hands.
        """
        pending,self.pending_probe=self.pending_probe,None
        if pending and pending[0]==v.round_key:
            _,before,number=pending
            if v.hand==before+(number,) and sum(v.hand)>v.target:
                for i,c in enumerate(v.trumps):
                    if c[1]=='RETURN' and not v.trump_locked and not v.table_full and extra<12:
                        self.last_plan=(('TRUMP',c),)
                        return f'TRUMP:{i}'
        if not self.nightmare or extra>10 or len(v.trumps)<5 or not v.opponent_stopped:
            return None
        if v.trump_locked or v.draw_locked or v.table_full or not v.deck_count or sum(v.hand)>v.target:
            return None
        returns=[c for c in v.trumps if c[1]=='RETURN']
        own_slots=sum(p==1 for p,*rest in v.table)
        if not returns or own_slots+2>engine.MAX_TABLE_SLOTS: return None
        stake=max(v.incoming,v.outgoing)
        if stake<3 and v.incoming<v.hp and v.outgoing<v.opponent_hp: return None
        # Value of choosing hold/draw/target AFTER observing the probe outcome.
        targets={v.target}|{c[2] for c in v.trumps if c[1]=='TARGET'}
        def best(posterior):
            choices=[]
            for target in targets:
                stay,draw=self.draw_values(replace(v,target=target),posterior)
                cost=0.10 if target!=v.target else 0.
                choices.extend((stay-cost,draw-cost))
            return max(choices)
        baseline=best(belief)
        candidate=None
        for i,c in enumerate(v.trumps):
            if c[1]!='DRAW_SPEC' or sum(v.hand)+c[2]<=v.target:continue
            p=belief.get(c[2],0.)
            if not .10<=p<=.80:continue
            success={n:w/(1-p) for n,w in belief.items() if n!=c[2]}
            improvement=p*best({c[2]:1.})+(1-p)*best(success)-baseline
            # Charge both scarce trumps, including Return's alternative uses.
            cost=reserve(c)+(1-p)*reserve(returns[0])+.15
            value=improvement*min(stake,6)-cost
            if value>.12 and (candidate is None or value>candidate[0]):
                candidate=(value,i,c)
        if candidate:
            _,i,c=candidate
            self.pending_probe=(v.round_key,v.hand,c[2])
            self.last_plan=(('TRUMP',c),('TRUMP',returns[0]))
            return f'TRUMP:{i}'
        return None

    def infer(self,v):
        pool=tuple(n for n in v.numbers if n not in v.hand+v.opponent_visible)
        if self.key!=v.round_key:
            self.key=v.round_key;self.seen=set();self.belief={n:1. for n in v.numbers}
        if v.events:
            for event in v.events:
                eid,actor,action,kind,val,own,opp,after_own,after_opp,target,locked=event
                if eid in self.seen: continue
                self.seen.add(eid)
                for hidden in self.belief:
                    likelihood=1.
                    available=[n for n in v.numbers if n not in own+opp and n not in (v.hand[0],hidden)]
                    before=own if actor==1 else opp
                    after=after_own if actor==1 else after_opp
                    total=sum(before)+(v.hand[0] if actor==1 else hidden)
                    if action=='trump' and not locked:
                        if kind in ('PERFECT','PERFECT_PLUS','ULTIMATE_DRAW') and len(after)>len(before):
                            safe=[n for n in available if n+total<=target]
                            predicted=max(safe) if safe else min(available,default=0)
                            likelihood=1. if after[-1]==predicted else .005
                        elif kind in ('DRAW_SPEC','DRAW_SPEC_PLUS'):
                            drawn=len(after)>len(before)
                            likelihood=1. if drawn==(val in available) else .005
                    # Actions are noisy signals, not proof: never eliminate a bluff.
                    if self.nightmare and actor==2 and action=='stay':
                        likelihood*=.45 if sum(v.opponent_visible)+hidden<target*.68 else 1.15
                    if self.nightmare and actor==2 and kind in ('ADD','GAMBLE','ADD_21'):
                        likelihood*=1.25 if total>=target*.8 and total<=target else .8
                    if self.nightmare and actor==2 and kind=='SHIELD':
                        likelihood*=1.1 if total<target*.85 else .95
                    self.belief[hidden]*=likelihood
        weights={n:max(1e-12,self.belief.get(n,1.)) for n in pool}
        normal=sum(weights.values()) or 1
        return {n:w/normal for n,w in weights.items()}

    def draw_values(self,v,belief):
        """Stay versus drawing with further chances to recover, using public beliefs.

        The next-card distribution excludes the hypothesized hidden card. After
        every draw, all remaining hidden hypotheses share the same next decision;
        this does not choose a different action with knowledge of each secret.
        Opponent trumps are evaluated elsewhere; this is the number-card baseline.
        """
        pool=tuple(belief)
        visible=sum(v.opponent_visible)
        def result(total,other):
            if total>v.target and other>v.target: return (other>total)-(other<total)
            if total>v.target:return -1
            if other>v.target:return 1
            return (total>other)-(total<other)
        @lru_cache(None)
        def evaluate(remaining,total,depth):
            normal=sum(belief[n] for n in remaining)
            if normal<=0:return (0.,0.)
            stay=sum(belief[n]*result(total,visible+n) for n in remaining)/normal
            if total>v.target or len(remaining)<=1 or depth<=0:return (stay,stay)
            draw=0.
            for n in remaining:
                probability=(1-belief[n]/normal)/(len(remaining)-1)
                if probability<=1e-12:continue
                rest=tuple(x for x in remaining if x!=n)
                future=evaluate(rest,total+n,depth-1)
                draw+=probability*max(future)
            return stay,draw
        return evaluate(pool,sum(v.hand),min(5,len(pool)-1) if len(pool)<=12 else 1)

    @staticmethod
    def ready_to_raise(states):
        """A lead requiring a future random draw is not a reason to raise now."""
        stable=0.
        for gs,w in states:
            own,other=sum(gs.p1_hand),sum(gs.p2_hand)
            winning=own<=gs.target_score and (own>other or other>gs.target_score)
            locked=any(t['owner']==1 and t['type'] in ('GAMBLE','SILENCE') for t in gs.active_trumps)
            # Playing a trump clears the opponent's stop flag in this game.
            # A low stopped opponent can therefore resume drawing after our ADD.
            if winning and (locked or not gs.deck or other>gs.target_score or own>=gs.target_score-2):stable+=w
        return stable>=.95

    def worlds(self,v):
        belief=self.infer(v)
        if not belief: return []
        cls=simulator(self.rng.randrange(2**32))
        base=cls()
        hidden=list(belief)
        count=8 if self.nightmare else 5
        if len(hidden)>count:
            # Weighted strata rather than a single optimistic hidden-card guess.
            cumulative=[];s=0
            for n in hidden: s+=belief[n];cumulative.append((s,n))
            selected=[next(n for c,n in cumulative if c>=(i+.5)/count) for i in range(count)]
            weights={n:selected.count(n)/count for n in set(selected)}
        else: weights=belief
        worlds=[]
        for n,weight in weights.items():
            gs=clone(base)
            gs.p1_hand=list(v.hand);gs.p2_hand=[n]+list(v.opponent_visible)
            gs.deck=[x for x in v.numbers if x not in gs.p1_hand+gs.p2_hand]
            self.rng.shuffle(gs.deck)
            gs.deck=gs.deck[:v.deck_count]
            gs.p1_trumps=list(v.trumps);gs.p2_trumps=[]
            gs.give_trump(2,v.opponent_count)
            gs.p1_fingers=v.hp;gs.p2_fingers=v.opponent_hp
            gs.reserve_scale=3.0 if self.nightmare else 2.4
            gs.target_score=v.target;gs.p1_stop=False;gs.p2_stop=v.opponent_stopped
            gs.active_trumps=[dict(owner=p,name=name,type=k,val=value,counter=c) for p,name,k,value,c in v.table]
            # Observation constructors used by tests/custom callers may omit table.
            if v.draw_locked and not any(t['owner']==2 and t['type'] in ('SILENCE','GAMBLE') for t in gs.active_trumps):
                gs.active_trumps.append(dict(owner=2,name='Silence',type='SILENCE',val=0))
            if v.trump_locked and not any(t['owner']==2 and t['type']=='DESTROY_BLOCK' for t in gs.active_trumps):
                gs.active_trumps.append(dict(owner=2,name='Blockade',type='DESTROY_BLOCK',val=0))
            if v.table_full:
                missing=max(0,engine.MAX_TABLE_SLOTS-sum(t['owner']==1 for t in gs.active_trumps))
                gs.active_trumps.extend(dict(owner=1,name='Occupied',type='UNKNOWN',val=0) for _ in range(missing))
            if not v.table:
                for victim,damage in ((1,v.incoming),(2,v.outgoing)):
                    if damage>1: gs.active_trumps.append(dict(owner=3-victim,name='Stake',type='ADD',val=damage-1))
            worlds.append((gs,weight))
        return worlds

    def expected(self,worlds):
        values=[(score(gs),w) for gs,w in worlds]
        mean=sum(x*w for x,w in values)
        # Nightmare protects against dangerous uncertain outcomes without refusing
        # a necessary gamble when all alternatives lose.
        return mean-(.10*sum(w*max(0,mean-x) for x,w in values) if self.nightmare else 0)

    def responses(self,gs):
        """Pessimistic counterplay on a sampled opponent hand; bounded to two trumps.

        Opponent knows more inside this hypothetical evaluation than a real player.
        That deliberately discounts fragile combos; it does not reveal real secrets.
        """
        gs=clone(gs);gs.cleanup_player_instants(2)
        natural=clone(gs)
        for _ in range(3):
            known=set(natural.p2_hand+natural.p1_hand[1:])
            unseen=[n for n in range(engine.SETTINGS['deck_range_start'],engine.SETTINGS['deck_range_end']+1) if n not in known]
            total=sum(natural.p2_hand)
            bust=sum(total+n>natural.target_score for n in unseen)/max(1,len(unseen))
            if not legal(natural,2) or total>=natural.target_score*.86 or bust>.5: break
            natural=play(natural,2,('HIT',None))
            if not natural.p1_stop: break
        natural=play(natural,2,('STAY',None))
        candidates=[gs]
        for _ in range(2 if self.nightmare else 1):
            expanded=list(candidates)
            for state in candidates:
                for card in sorted(set(state.p2_trumps)):
                    if card[1]=='OBLIVION': continue
                    child=play(state,2,('TRUMP',card))
                    if child is not None: expanded.append(child)
            candidates=sorted(expanded,key=score)[:2]
        outcomes=[]
        for state in candidates:
            outcomes.append(play(state,2,('STAY',None)))
            if legal(state,2):
                # Evaluate a random draw in this shuffled hypothesis.
                drawn=play(state,2,('HIT',None))
                outcomes.append(drawn)
        enemy=min(outcomes,key=score)
        if self.nightmare:
            # Our retained counter on the following turn makes bait-and-switch
            # target plans and reserve cards useful, instead of blindly cashing out.
            future=clone(enemy);future.cleanup_player_instants(1)
            counter=[future]
            threatened = score(future)<0 and (future.calculate_potential_damage(1)>=future.p1_fingers or any(t['owner']==2 and t['type'] in ('SILENCE','GAMBLE','DESIRE','DESIRE_PLUS') for t in future.active_trumps))
            for _ in range(2 if threatened else 1):
                expanded=list(counter)
                for state in counter:
                    for card in sorted(set(state.p1_trumps)):
                        if card[1]=='OBLIVION': continue
                        child=play(state,1,('TRUMP',card))
                        if child is not None: expanded.append(child)
                        if any(t['owner']==2 and t['type'] in ('DESIRE','DESIRE_PLUS') for t in state.active_trumps):
                            expanded.append(play(state,1,('DISCARD',card)))
                counter=sorted(expanded,key=score,reverse=True)[:2]
            # Both stopped settles before a future response is possible.
            if not (enemy.p1_stop and enemy.p2_stop):
                best=max(counter,key=score)
                return .65*score(natural)+.20*score(enemy)+.15*score(best)
        return .65*score(natural)+.35*score(enemy)

    def choose(self,v,extra,mood):
        worlds=self.worlds(v)
        if not worlds: return 'STAY'
        max_extra=12 if self.nightmare else 4
        if extra<max_extra and len(v.trumps)>=v.max_trumps and v.incoming<=1:
            shields=[i for i,c in enumerate(v.trumps) if c[1]=='SHIELD']
            if len(shields)>2 and sum(v.hand)>=sum(v.opponent_visible)+max(self.infer(v),default=0):
                return f'DISCARD:{shields[0]}'
        baseline=self.expected(worlds)
        belief=self.infer(v)
        wait_for_card=self.certainly_ahead(v)
        avoid_blind_draw = wait_for_card or self.number_draw_pending(v)
        probe=self.protected_probe(v,belief,extra)
        if probe: return probe
        stay_value,draw_value=self.draw_values(v,belief)
        if extra<max_extra and not v.trump_locked and not v.table_full:
            guaranteed = sum(w for gs,w in worlds if sum(gs.p1_hand)<=gs.target_score and (sum(gs.p1_hand)>sum(gs.p2_hand) or sum(gs.p2_hand)>gs.target_score))
            for i,c in enumerate(v.trumps):
                if c[1]=='ADD' and self.ready_to_raise(worlds) and v.outgoing<v.opponent_hp: return f'TRUMP:{i}'
                if c[1]=='SHIELD' and v.hp<=v.incoming and guaranteed<.5: return f'TRUMP:{i}'
            for i,c in enumerate(v.trumps):
                if c[1] in ('DRAW_SPEC','DRAW_SPEC_PLUS') and not v.draw_locked and v.deck_count and c[2] in belief and sum(v.hand)+c[2]==v.target and belief[c[2]]<.35 and guaranteed<.7:
                    self.last_plan=(('TRUMP',c),('STAY',None))
                    return f'TRUMP:{i}'
        # Safe low-total draws preserve targeted/target-changing trumps for when
        # the extra information makes them useful. Emergency protection above
        # remains possible when handing over exposes lethal damage.
        if not avoid_blind_draw and not v.draw_locked and v.deck_count and belief and sum(v.hand)+max(belief)<=v.target and sum(v.hand)<v.target*.75 and stay_value<.8:
            self.last_plan=(('HIT',None),)
            return 'HIT'
        def information(path):
            if not self.nightmare: return 0.
            bonus=0.
            for action,card in path:
                if action=='TRUMP' and card[1] in ('DRAW_SPEC','DRAW_SPEC_PLUS') and not v.draw_locked:
                    p=belief.get(card[2],0.)
                    if 0<p<1:
                        # A successful/failed specific draw splits the possible
                        # hidden cards. Small information value can justify a probe.
                        bonus+=.22*(-p*math.log2(p)-(1-p)*math.log2(1-p))
            return bonus
        terminal=[]
        frontier=[(worlds,())]
        complex_position = v.table_full or v.trump_locked or any(k in ('DESIRE','DESIRE_PLUS','SILENCE','GAMBLE','DESTROY_BLOCK') for k,val in v.enemy_effects)
        depth=min((4 if complex_position else 3) if self.nightmare else 2,max(0,max_extra-extra))
        for level in range(depth+1):
            expanded=[]
            for states,path in frontier:
                for end in ('STAY','HIT'):
                    if end=='HIT' and not path and avoid_blind_draw: continue
                    if end=='HIT' and (v.draw_locked or any(not legal(gs,1) for gs,w in states)): continue
                    after=[(play(gs,1,(end,None)),w) for gs,w in states]
                    # Holding has a future opponent turn too, unless both stopped.
                    value=sum(w*(score(gs) if end=='STAY' and gs.p2_stop else self.responses(gs)) for gs,w in after)
                    if not self.nightmare and end=='HIT':
                        value += .10 if mood=='gambler' else -.04
                    terminal.append((value+information(path),path+((end,None),)))
                if level==depth: continue
                common=set(states[0][0].p1_trumps)
                for gs,w in states[1:]: common.intersection_update(gs.p1_trumps)
                for card in sorted(common):
                    if not path and wait_for_card and card[1] in ('DRAW_SPEC','DRAW_SPEC_PLUS','PERFECT','PERFECT_PLUS','ULTIMATE_DRAW','DEATH_DESTROY'):continue
                    # Legality belongs to the current simulated state, not the
                    # opening snapshot: full tables can still use S-Attack,
                    # replace a target, or discard through a trump blockade.
                    actions=[('TRUMP',card)]
                    if len(states[0][0].p1_trumps)>=v.max_trumps or any(t['owner']==2 and t['type'] in ('DESIRE','DESIRE_PLUS') for t in states[0][0].active_trumps):
                        actions.append(('DISCARD',card))
                    for action in actions:
                        if action[0]=='TRUMP':
                            if card[1] in ('ADD','ADD_21','GAMBLE') and not self.ready_to_raise(states):continue
                            if card[1]=='TARGET' and card[2]==states[0][0].target_score:continue
                        children=[(play(gs,1,action),w) for gs,w in states]
                        if any(gs is None for gs,w in children): continue
                        value=self.expected(children)+information(path+(action,))
                        # Resetting is a rescue, not a free imagined fresh lucky hand.
                        if card[1]=='OBLIVION' and action[0]=='TRUMP':
                            terminal.append((-0.4,path+(action,)));continue
                        expanded.append((value,children,path+(action,)))
            if not expanded: break
            # Reserve beam slots for diverse first moves, including setups whose
            # single-card value is low but whose next card completes a combo.
            ranked=sorted(expanded,key=lambda item:item[0],reverse=True)
            frontier=[];firsts=set()
            width=4 if self.nightmare else 2
            for value,states,path in ranked:
                if path[0] in firsts: continue
                frontier.append((states,path));firsts.add(path[0])
                if len(frontier)>=width: break
        # A setup card must not be spent solely to justify a subsequent blind
        # draw. Keep legitimate target rescue and exact-number finishing plays.
        filtered=[]
        for value,path in terminal:
            first,card=path[0]
            if first=='TRUMP' and path[-1][0]=='HIT':
                if card[1] in ('ADD','ADD_21','GAMBLE'):continue
                if card[1]=='TARGET' and sum(v.hand)<=v.target:continue
                if card[1]=='RETURN' and sum(v.hand)<=v.target:continue
                if card[1] in ('DRAW_SPEC','DRAW_SPEC_PLUS'):continue
            filtered.append((value,path))
        terminal=filtered
        value,path=max(terminal,key=lambda item:item[0])
        self.last_plan=path
        action,card=path[0]
        if action in ('TRUMP','DISCARD'): return f'{action}:{v.trumps.index(card)}'
        # In particular, after Return or a failed number probe, do not accept a
        # likely loss merely because one draw alone may still leave us behind.
        if action=='STAY' and not avoid_blind_draw and not v.draw_locked and v.deck_count and sum(v.hand)<=v.target and stay_value<0 and draw_value>stay_value+.03:
            self.last_plan=(('HIT',None),)
            return 'HIT'
        raised=any(owner==1 and kind in ('ADD','ADD_21','GAMBLE') for owner,name,kind,value,counter in v.table)
        if not avoid_blind_draw and not self.nightmare and mood=='gambler' and action=='STAY' and not (raised and stay_value>=0) and not v.opponent_stopped and not v.draw_locked and v.deck_count and sum(v.hand)<v.target:
            pool=tuple(self.infer(v))
            if pool and sum(sum(v.hand)+n>v.target for n in pool)/len(pool)<=.75 and self.rng.random()<.32:
                return 'HIT'
        # Make room when genuinely worthless cards clog the hand. No forced waste
        # of a useful recovery card merely because a slot is full.
        if extra<max_extra and len(v.trumps)>=v.max_trumps and v.trumps:
            useless=[(reserve(c),i) for i,c in enumerate(v.trumps) if c[1]=='SHIELD' and v.incoming<=1 and baseline>=0]
            if useless: return f'DISCARD:{min(useless)[1]}'
        return action
