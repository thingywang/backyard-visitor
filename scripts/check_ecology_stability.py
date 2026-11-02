"""Deterministic, UI-free ecology stress evidence; never opens a real save.

This checks model invariants, not native window behavior or real sleep/wake.
Simulated pause means deliberately withholding Engine.tick; no wall-clock sleep.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backyard_visitor.model import (Engine, Visitor, Gift, GIFT_SIZE, MAX_GIFTS,
                                    SPRITE_WIDTH, SPRITE_HEIGHT, TURN_SECONDS,
                                    LANDING_SECONDS, SETTLE_SECONDS, RISE_SECONDS, TRUE_NAME_SECONDS, TAKEOFF_SECONDS,
                                    GAIT_STRIDE, RUN_STRIDE, WINDOW_JUMP_X, MEAL_COOLDOWN_SECONDS, GIFT_RESPONSE_SECONDS)
from backyard_visitor.interaction_model import (TEASER_SECONDS, SWAT_SECONDS,
                                               CROUCH_RISE_SECONDS, BELLY_ROLL_SECONDS)

SEEDS = (4, 17, 29, 83, 131, 257, 509, 1021)
SCREENS = ((0, 30, 1920, 985), (-1440, -180, 1440, 900),
           (1920, 0, 1280, 720), (0, 0, 800, 600))
STEPS = (.04, .04, .04, .04, .04, .25, .5)
ACTIONS = ('open', 'invite', 'feed', 'feed', 'nickname', 'call', 'pet', 'startle',
           'carry_release', 'close', 'cursor', 'cursor', 'windows', 'windows',
           'screen', 'gift_collect', 'gift_discard', 'gift_hold', 'pause',
           'restore_probe', 'paths', 'gift_spots', 'calm', 'true_name', 'adopt',
           'gift_scene', 'mouse_scene', 'fight_scene', 'fight', 'intervene',
           'teaser_start', 'teaser_start', 'teaser_stop', 'belly_pet')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


POSTURE_ENDPOINTS = {
    ('crouch_rise','play_crouch','standing'): CROUCH_RISE_SECONDS,
    ('belly_roll','sleeping','play_crouch'): BELLY_ROLL_SECONDS,
    ('belly_roll','swatting','resting'): BELLY_ROLL_SECONDS,
}


def check_posture(visitor):
    """A transition has a real endpoint contract and cannot cover an arc."""
    if visitor.posture_remaining > 0:
        key = (visitor.posture_kind, visitor.posture_source, visitor.posture_target)
        require(key in POSTURE_ENDPOINTS, 'invalid posture endpoints')
        require(math.isfinite(visitor.posture_duration)
                and abs(visitor.posture_duration-POSTURE_ENDPOINTS[key])<1e-9,
                'invalid posture duration')
        require(0 < visitor.posture_remaining <= visitor.posture_duration+1e-9,
                'invalid posture remaining')
        require(visitor.state in {'teasing','resting','waking'}, 'posture overrides unsupported/urgent state')
        if visitor.state == 'waking':
            require(key in {('belly_roll','sleeping','play_crouch'),
                            ('crouch_rise','play_crouch','standing')},
                    'invalid daily waking posture endpoints')
        require(visitor.ground_speed == 0, 'active posture retained ground velocity')
    else:
        require(visitor.posture_remaining == 0 and visitor.posture_duration == 0
                and not visitor.posture_kind and not visitor.posture_source and not visitor.posture_target,
                'completed posture retained transient fields')


def check_reward_clocks(engine):
    for profile in engine.cats.values():
        require(math.isfinite(profile.meal_cooldown_remaining)
                and 0 <= profile.meal_cooldown_remaining <= MEAL_COOLDOWN_SECONDS,
                'invalid identity meal cooldown')
        require(engine.can_eat(profile.id) == (profile.meal_cooldown_remaining <= 1e-9),
                'meal eligibility disagrees with cooldown')
    for visitor in engine.visitors.values():
        require(type(visitor.gift_response_pending) is bool, 'nonboolean gift pending')
        require(math.isfinite(visitor.gift_response_remaining)
                and 0 <= visitor.gift_response_remaining <= GIFT_RESPONSE_SECONDS,
                'invalid gift response duration')
        if visitor.gift_response_remaining > 0:
            require(visitor.state == 'purring' and not visitor.gift_response_pending,
                    'active gift response has unsafe state')
            require(not visitor.true_name_pending and visitor.true_name_remaining == 0,
                    'gift response competes with true-name priority')
            require(engine.moving, 'gift response lost animation clock')
        if visitor.gift_response_pending or visitor.gift_response_remaining > 0:
            require(visitor.state not in {'carried','fighting','fleeing','leaving'}
                    and visitor.route_motion not in {'fleeing','leaving'},
                    'urgent state retained gift response')


class Run:
    def __init__(self, seed, seconds, demo):
        self.seed, self.seconds, self.demo = seed, seconds, demo
        self.rng = random.Random(seed ^ 0xBACC)
        self.engine = Engine(seed=seed, demo=demo)
        self.stats = Counter()
        self.states, self.events, self.gift_kinds = Counter(), Counter(), Counter()
        self.actions, self.successes = Counter(), Counter()
        self.history = deque(maxlen=16)
        self.clock_watch = {}
        self.maximum_visitors = self.maximum_gifts = 0
        self.paused_seconds = 0.0
        self.active_seconds = 0.0
        self.window_revision = 0
        self.engine.set_bounds(*SCREENS[seed % len(SCREENS)])
        self.engine.open_gate()
        self.engine.invite('amber')
        self.engine.invite('mist')
        self.set_windows()

    def set_windows(self):
        e = self.engine
        left, top, width, height = e.bounds
        self.window_revision += 1
        offset = (self.window_revision % 3 - 1) * 12
        edges = [('editor', left + width*.12, left + width*.72, top + max(130, height*.25)+offset),
                 ('notes', left + width*.40, left + width*.96, top + height*.68)]
        if self.window_revision % 4 == 0:
            edges = edges[1:]
        elif self.window_revision % 7 == 0:
            edges = []
        e.set_window_edges(edges)

    def check(self):
        e = self.engine
        left, top, width, height = e.bounds
        right, bottom = left + width, top + height
        require(len(e.visitors) <= e.visitor_limit, 'visitor capacity exceeded')
        require(len(e.gifts) <= MAX_GIFTS, 'gift capacity exceeded')
        require(len({g.id for g in e.gifts}) == len(e.gifts), 'duplicate gift ID')
        require(0 <= e.food <= 3, 'food outside capacity')
        require(sum(c.adopted for c in e.cats.values()) <= 1, 'multiple residents')
        check_reward_clocks(e)
        self.maximum_visitors = max(self.maximum_visitors, len(e.visitors))
        self.maximum_gifts = max(self.maximum_gifts, len(e.gifts))
        for c in e.cats.values():
            require(math.isfinite(c.trust) and 0 <= c.trust <= 100, 'invalid trust')
        for v in e.visitors.values():
            for name in Visitor.__slots__:
                value = getattr(v, name)
                if isinstance(value, (int, float)):
                    require(math.isfinite(value), f'nonfinite visitor {name}')
            check_posture(v)
            if v.posture_remaining > 0:
                require(e.moving, 'posture lost animation clock')
                self.stats['posture_'+v.posture_kind+'_observations'] += 1
            require(0 <= v.gait_phase < 1 and 0 <= v.run_phase < 1, 'invalid locomotion phase')
            require(v.state in {'peeking','entering','idle','walking','running','jumping','window_peeking',
                               'sleeping','waking','purring','playing','teasing','resting','swatting','eating','grooming',
                               'carried','fighting','leaving','fleeing'}, 'unknown visitor state')
            if v.state in {'walking','running','entering','leaving','fleeing'}:
                require(abs(v.target_y-v.y)<1e-7, 'ground route has a vertical component')
            require(v.route_stage in {'','approach','jump','finish'}, 'invalid route stage')
            if v.route_edge_id:
                require(v.route_edge_id in e.window_edges, 'stale planned window route')
            if v.state == 'teasing':
                require(e.active_teaser_id == v.cat_id, 'teasing has no owning session')
            if v.state == 'swatting':
                require(v.state_age < SWAT_SECONDS+1e-7, 'swatting failed to settle')
            if v.state == 'resting':
                require(v.state_age < v.next_action+1e-7, 'resting failed to finish')
            require(v.facing in (-1, 1), 'invalid facing')
            require(top-1e-7 <= v.y <= e.floor_y+1e-7, 'visitor outside vertical play area')
            intentional_edge = (v.state in {'peeking', 'entering', 'leaving', 'fleeing', 'carried'}
                                or v.route_motion in {'leaving','fleeing'})
            if not intentional_edge:
                require(left-1e-7 <= v.x <= right-SPRITE_WIDTH+1e-7,
                        f'{v.state} outside horizontal play area')
            if v.window_edge_id:
                require(v.window_edge_id in e.window_edges, 'stale window support')
                x1, x2, y = e.window_edges[v.window_edge_id]
                require(x1-1e-7 <= v.x <= x2-SPRITE_WIDTH+1e-7, 'cat exceeds supporting window')
                expected_y = y-SPRITE_HEIGHT + (78*v.peek_progress if v.state == 'window_peeking' else 0)
                require(abs(v.y-expected_y) < 1e-6, 'cat detached from supporting window')
            if v.window_target_id:
                require(v.window_target_id in e.window_edges, 'stale window destination')
                x1, x2, y = e.window_edges[v.window_target_id]
                require(x1-1e-7 <= v.target_x <= x2-SPRITE_WIDTH+1e-7, 'invalid window destination x')
                require(abs(v.target_y-(y-SPRITE_HEIGHT)) < 1e-7, 'invalid window destination y')
            if v.state == 'jumping':
                require(abs(v.target_x-v.jump_start_x)<=WINDOW_JUMP_X+1e-7, 'jump crosses too far horizontally')
                require(v.jump_elapsed <= v.jump_duration+1e-7, 'jump failed to finish')
                require(v.state_age <= v.jump_duration+TAKEOFF_SECONDS+1e-7, 'jump preparation failed to finish')
                if v.takeoff_remaining > 0:
                    require(v.jump_elapsed == 0, 'flight advanced during takeoff')
                    require(abs(v.x-v.jump_start_x) < 1e-7 and abs(v.y-v.jump_start_y) < 1e-7, 'takeoff feet moved')
                    support = e.window_edges.get(v.takeoff_edge_id)
                    expected = support[2]-SPRITE_HEIGHT if support else e.floor_y
                    require(v.takeoff_edge_id is None or support is not None, 'missing takeoff support')
                    require(abs(v.y-expected) < 1e-7, 'takeoff without support')
            if v.state in {'entering', 'leaving', 'fleeing'}:
                require(v.state_age < 80, f'{v.state} failed to finish')
            for name, limit in [('takeoff_remaining', TAKEOFF_SECONDS),
                                ('turn_remaining', TURN_SECONDS),
                                ('landing_remaining', LANDING_SECONDS),
                                ('settle_remaining', SETTLE_SECONDS),
                                ('rise_remaining', RISE_SECONDS),
                                ('true_name_remaining', TRUE_NAME_SECONDS),
                                ('posture_remaining', BELLY_ROLL_SECONDS)]:
                value = getattr(v, name)
                require(0 <= value <= limit+1e-8, f'invalid {name}')
                key = (id(v), name)
                previous, since = self.clock_watch.get(key, (0.0, self.active_seconds))
                if value <= 0 or value > previous+1e-8:
                    since = self.active_seconds
                require(not value or self.active_seconds-since < max(1, limit*3), f'stuck {name}')
                self.clock_watch[key] = (value, since)
                if value > 0:
                    self.stats[name + '_observations'] += 1
            if v.true_name_pending:
                require(v.state not in {'carried','fighting','fleeing','leaving'}, 'urgent state retained a name response')
                self.stats['true_name_pending_observations'] += 1
            if v.true_name_remaining > 0:
                require(v.state == 'purring' and not v.true_name_pending, 'active name response has unsafe state')
            if v.gift_response_pending:
                self.stats['gift_response_pending_observations'] += 1
            if v.gift_response_remaining > 0:
                self.stats['gift_response_active_observations'] += 1
            self.states[v.state] += 1
        session = e._teaser
        if session is not None:
            require(session.cat_id in e.visitors, 'stale teaser session')
            require(0 < session.remaining <= TEASER_SECONDS, 'invalid teaser deadline')
            require(session.phase in {'watch','rise','chase','pounce','approach'}, 'invalid teaser phase')
            require(e.visitors[session.cat_id].state not in {'carried','fighting','leaving','fleeing'},
                    'urgent state retained teaser ownership')
            self.stats['teaser_session_observations'] += 1
        live_ids = {id(v) for v in e.visitors.values()}
        self.clock_watch = {key: value for key, value in self.clock_watch.items() if key[0] in live_ids}
        for g in e.gifts:
            require(g.facing in (-1, 1), 'invalid gift facing')
            require(g.state != 'escaping' or g.facing == g.escape_direction, 'escape facing disagrees with movement')
            require(all(math.isfinite(value) for value in (g.x, g.y, g.wait_remaining)), 'nonfinite gift')
            require(top-1e-7 <= g.y <= bottom-GIFT_SIZE+1e-7, 'gift outside vertical play area')
            if g.state == 'escaping':
                require(g.kind == 'toy_mouse' and g.escape_direction in (-1, 1), 'invalid escaping gift')
                require(left-GIFT_SIZE < g.x < right, 'fully escaped mouse was retained')
                self.stats['escaping_mouse_observations'] += 1
            elif g.state != 'held':
                require(left-1e-7 <= g.x <= right-GIFT_SIZE+1e-7, 'stationary gift outside screen')
            self.gift_kinds[g.kind] += 1
        for event in e.drain_events():
            self.events[event['kind']] += 1
        self.stats['invariant_checks'] += 1

    def tick(self, dt):
        snapshots = {cid:(id(v), v.posture_remaining, (v.x,v.y,v.gait_phase,v.run_phase))
                     for cid,v in self.engine.visitors.items() if v.posture_remaining >= min(dt,5)}
        self.engine.tick(dt)
        for cid,(identity,remaining,position) in snapshots.items():
            v = self.engine.visitors.get(cid)
            # Urgent exits supersede the pose. Otherwise a tick that fits
            # wholly inside the pose interval cannot translate or step.
            if v is not None and id(v)==identity and v.state in {'teasing','resting','waking'}:
                require((v.x,v.y,v.gait_phase,v.run_phase)==position, 'posture moved before its duration elapsed')
                self.stats['posture_frozen_tick_checks'] += 1
        self.active_seconds += min(dt, 5)
        self.stats['tick_calls'] += 1
        self.check()

    def restore_probe(self):
        e = self.engine
        state = json.loads(json.dumps(e.export(), allow_nan=False))
        restored = Engine(state, seed=self.seed)
        require(not restored.gate_open and not restored.visitors and restored.active_teaser_id is None,
                'restore resumed transient activity')
        require(all(name not in state for name in ('teaser','active_teaser_id','run_phase','route_final_x',
                                                     'posture_kind','posture_remaining','posture_duration',
                                                     'posture_source','posture_target','gift_response_pending','gift_response_remaining','meal_bowl')),
                'runtime interaction state persisted')
        again = restored.export()
        for key in ('bounds', 'calm_mode', 'cats', 'food', 'bowl', 'next_gift_id', 'gift_collection'):
            require(state[key] == again[key], f'roundtrip changed {key}')
        require([(g['id'],g['kind']) for g in state['gifts']] == [(g['id'],g['kind']) for g in again['gifts']],
                'roundtrip lost gifts')
        for original, gift in zip(state['gifts'], restored.gifts):
            if gift.kind != 'toy_mouse':
                require((gift.x,gift.y) == (original['x'],original['y']), 'static gift moved on restore')
            if gift.kind == 'toy_mouse':
                require(gift.state == 'idle' and 8 <= gift.wait_remaining <= 22, 'mouse resumed escape on restore')
        self.stats['export_restore_checks'] += 1
        return True

    def action(self, kind):
        e, rng = self.engine, self.rng
        cid = rng.choice(tuple(e.cats))
        v = rng.choice(tuple(e.visitors.values())) if e.visitors else None
        g = rng.choice(e.gifts) if e.gifts else None
        left, top, width, height = e.bounds
        accepted = False
        if kind == 'open': accepted = e.open_gate(True)
        elif kind == 'close': accepted = e.open_gate(False)
        elif kind == 'invite': accepted = e.invite(cid) is not None
        elif kind == 'feed': accepted = e.feed()
        elif kind == 'nickname': accepted = e.name_cat(cid, f'访客{self.seed}') not in {'invalid','undiscovered'}
        elif kind == 'true_name': accepted = e.name_cat(cid, e.cats[cid].true_name) == 'true_name'
        elif kind == 'call': accepted = e.call_cat(cid) == 'recognized'
        elif kind == 'pet' and v: accepted = e.pet(v.cat_id)
        elif kind == 'teaser_start' and v: accepted = e.start_teaser(v.cat_id)
        elif kind == 'teaser_stop' and v: accepted = e.stop_teaser(v.cat_id)
        elif kind == 'belly_pet':
            sleeping = [cat for cat in e.visitors.values() if cat.state == 'sleeping']
            if sleeping:
                candidate = rng.choice(sleeping)
                accepted = e.pet(candidate.cat_id) or candidate.state == 'swatting'
        elif kind == 'startle' and v: accepted = e.startle(v.cat_id)
        elif kind == 'adopt': accepted = e.adopt(cid)
        elif kind == 'fight': accepted = e.trigger_fight()
        elif kind == 'intervene': accepted = e.intervene(cid)
        elif kind == 'cursor':
            e.cursor_moved(left+rng.random()*width, top+rng.random()*height, rng.choice([40,200,1100]))
            accepted = True
        elif kind == 'carry_release' and v and e.carry(v.cat_id):
            phase = v.gait_phase
            self.tick(.15)
            x, y = left+rng.random()*(width-SPRITE_WIDTH), top+rng.random()*(height-SPRITE_HEIGHT)
            accepted = e.release(v.cat_id, x, y)
            require(v.gait_phase == phase, 'carrying/release counted as a footstep')
        elif kind == 'screen':
            e.set_bounds(*rng.choice(SCREENS)); self.set_windows(); accepted = True
        elif kind == 'windows': self.set_windows(); accepted = True
        elif kind == 'paths':
            e.set_paths([(left+width*.35, e.floor_y), (left+width*.65, top+height*.55)])
            accepted = True
        elif kind == 'gift_spots':
            e.set_gift_spots([(left+width*.25, top+height*.8), (left+width*.8, top+height*.7)])
            accepted = True
        elif kind == 'gift_collect' and g: accepted = e.collect_gift(g.id)
        elif kind == 'gift_discard' and g: accepted = e.discard_gift(g.id)
        elif kind == 'gift_hold' and g and e.set_gift_held(g.id, True):
            frozen = (g.x, g.y, g.wait_remaining)
            self.tick(.4)
            require((g.x,g.y,g.wait_remaining) == frozen, 'held mouse advanced')
            g.x, g.y = left+rng.random()*(width-GIFT_SIZE), top+rng.random()*(height-GIFT_SIZE)
            accepted = e.set_gift_held(g.id, False)
        elif kind == 'pause':
            before = (e.export(), [asdict(v) for v in e.visitors.values()], [asdict(g) for g in e.gifts], e._time, asdict(e._teaser) if e._teaser else None)
            self.paused_seconds += rng.uniform(5, 90)
            after = (e.export(), [asdict(v) for v in e.visitors.values()], [asdict(g) for g in e.gifts], e._time, asdict(e._teaser) if e._teaser else None)
            require(before == after, 'model changed without a tick')
            accepted = True
        elif kind == 'restore_probe': accepted = self.restore_probe()
        elif kind == 'calm': e.calm_mode = not e.calm_mode; accepted = True
        elif kind.endswith('_scene'): accepted = e.demo_scene(kind.removesuffix('_scene'))
        self.actions[kind] += 1
        self.successes[kind] += bool(accepted)
        self.history.append({'model_seconds': round(self.active_seconds,3), 'action':kind, 'accepted':bool(accepted)})
        self.check()

    def execute(self):
        next_action = 3.0
        next_restore = 30.0
        index = 0
        while self.active_seconds < self.seconds:
            self.tick(min(STEPS[index % len(STEPS)], self.seconds-self.active_seconds))
            index += 1
            if self.active_seconds >= next_action:
                self.action(self.rng.choice(ACTIONS))
                next_action = self.active_seconds+self.rng.uniform(1.5,7.0)
            if self.active_seconds >= next_restore:
                self.restore_probe()
                next_restore = self.active_seconds+30
        # No new activity: every visitor must leave and every released mouse
        # must eventually finish its dash, even with the gate now closed.
        self.engine.open_gate(False)
        self.engine.set_window_edges([])
        for v in list(self.engine.visitors.values()):
            if v.state == 'carried': self.engine.release(v.cat_id, v.x, v.y)
        for g in self.engine.gifts: self.engine.set_gift_held(g.id, False)
        for _ in range(480): self.tick(.25)
        require(not self.engine.visitors, 'visitor stuck after gate closure')
        require(not self.engine.has_gift_activity and not self.engine.moving, 'transient activity stuck after drain')
        self.restore_probe()
        return {'seed':self.seed, 'demo_mode':self.demo, 'active_model_seconds':round(self.active_seconds,3),
                'simulated_pause_seconds':round(self.paused_seconds,3), 'drain_seconds':120,
                'maximum_visitors':self.maximum_visitors, 'maximum_gifts':self.maximum_gifts,
                'statistics':dict(self.stats), 'actions_attempted':dict(self.actions),
                'actions_accepted':dict(self.successes), 'visitor_state_observations':dict(self.states),
                'events':dict(self.events), 'gift_kind_observations':dict(self.gift_kinds),
                'drain_passed':True, 'passed':True}



def directed_geometry_checks():
    results = []
    for name, origin, destination, carried in (
            ('legacy_jump_shrink_onto_clamped_goal', (0,0), (1700,600), False),
            ('legacy_jump_shrink_remaining_arc_negative_origin', (-1920,-100), (300,400), False),
            ('carried_release_shrink_midjump', (0,0), (1600,100), True)):
        left, top = origin
        e = Engine(seed=7)
        e.set_bounds(left,top,1920,1080)
        v = Visitor('amber',left+1600,e.floor_y,state='idle',facing=1,
                    target_x=left+1600,target_y=e.floor_y,stay_remaining=1000,next_action=1000,gait_phase=.31)
        e.visitors[v.cat_id] = v
        if carried:
            e.gate_open = True; e._visit_countdown = 1000
            require(e.carry(v.cat_id), 'directed carry setup failed')
            require(e.release(v.cat_id,left+destination[0],top+destination[1]), 'directed release setup failed')
        else:
            e._walk(v,left+destination[0],top+destination[1])
        e.tick(.2)
        require(v.state == 'jumping', 'directed setup did not exercise a jump')
        phase = v.gait_phase
        e.set_bounds(1920,30,800,600)
        if v.state == 'jumping':
            require((v.jump_start_x,v.jump_start_y) == (v.x,v.y), 'resized jump retained an obsolete origin')
        frames = jump_frames = ground_frames = 0
        for _ in range(400):
            before = (v.x,v.y)
            phase, old_state = v.gait_phase, v.state
            if old_state == 'jumping':
                require(abs(v.target_x-v.jump_start_x)<=WINDOW_JUMP_X+1e-7, 'directed hop exceeds lateral limit')
            e.tick(.04); frames += 1
            require(1920 <= v.x <= 2576 and 30 <= v.y <= e.floor_y, 'resized arc left the new screen')
            require(math.dist(before,(v.x,v.y)) < 30, 'resized arc teleported after rebasing')
            if old_state == 'jumping':
                require(v.gait_phase == phase, 'resize/jump counted as gait travel')
                jump_frames += 1
            else:
                distance = math.dist(before,(v.x,v.y))
                require(abs(v.y-before[1])<1e-7, 'finish route walked vertically')
                expected = (phase+distance/GAIT_STRIDE)%1
                require(abs(v.gait_phase-expected)<1e-7, 'ground finish gait disagrees with travel')
                ground_frames += 1
            if v.state == 'idle': break
        require(v.state == 'idle' and (v.x,v.y) == (v.target_x,v.target_y), 'resized jump did not arrive')
        results.append({'case':name,'passed':True,'checked_frames':frames,
                        'source_bounds':[left,top,1920,1080],'destination_bounds':[1920,30,800,600],
                        'uses_internal_route_staging':not carried, 'jump_frames':jump_frames,
                        'ground_finish_frames':ground_frames, 'maximum_lateral_hop':WINDOW_JUMP_X})
    return results


def directed_interaction_checks():
    results = []
    for seed in (3, 19):
        e = Engine(seed=seed, visit_limit=1)
        e.open_gate(); e.invite('amber'); e._visit_countdown = 1000
        v = e.visitors['amber']
        v.x, v.y, v.state = 300, e.floor_y, 'idle'
        v.stay_remaining, v.next_action = 1000, 1000
        e.cursor_moved(450,e.floor_y+30,0)
        require(e.start_teaser('amber'), 'directed teaser start failed')
        phases, states = Counter(), Counter()
        for _ in range(375):
            phases[e.teaser_phase or 'finished'] += 1
            states[v.state] += 1
            before, phase, old_state = (v.x,v.y), v.run_phase, v.state
            e.tick(.04)
            if old_state == 'teasing':
                distance = math.dist(before,(v.x,v.y))
                require(abs(v.run_phase-(phase+distance/RUN_STRIDE)%1)<1e-7,
                        'teaser chase phase disagrees with actual travel')
            if old_state == 'jumping':
                require(v.run_phase==phase, 'pounce advances run phase')
        require(e.active_teaser_id is None, 'teaser exceeded fifteen seconds')
        require(phases['watch'] and phases['rise'] and phases['chase'] and phases['pounce'], 'directed play missed a phase')
        for _ in range(1000):
            if v.state == 'idle': break
            e.tick(.04)
        require(v.state == 'idle', 'play fatigue did not recover')
        results.append({'case':'fifteen_second_chase_pounce_and_recovery','seed':seed,'passed':True,
                        'phase_samples':dict(phases),'state_samples':dict(states)})
    for trust in (20,70,100):
        e = Engine(seed=41);e.open_gate();e.invite('amber')
        v=e.visitors['amber'];v.x,v.y,v.state=300,e.floor_y,'sleeping'
        e.cats['amber'].trust=trust;e.rng.random=lambda:0.0
        result=e.pet('amber')
        require((v.state=='swatting') == (trust<100), 'belly/full-trust scratch contract failed')
        if trust<100:
            require(not result and v.pet_cooldown==8, 'scratch awarded affection or missed cooldown')
            e.tick(SWAT_SECONDS);require(v.state=='resting','scratch did not settle')
        else:
            require(result and v.state=='purring','full trust failed to allow belly touch')
        results.append({'case':'controlled_belly_touch','trust':trust,'passed':True,
                        'uses_controlled_random_draw':True})
    return results


def directed_posture_checks():
    results = []
    for kind in ('crouch_rise','sleeping_roll','swatting_roll'):
        e=Engine(seed=51,visit_limit=1);e.open_gate();e.invite('amber');e._visit_countdown=1000
        v=e.visitors['amber'];v.x,v.y=300,e.floor_y;v.state='idle' if kind=='crouch_rise' else 'sleeping'
        v.next_action=v.stay_remaining=1000;e.cats['amber'].trust=70
        e.cursor_moved(1000,e.floor_y+40,0)
        if kind=='swatting_roll':
            e.rng.random=lambda:0.;e.pet('amber');e.tick(SWAT_SECONDS)
        else:
            require(e.start_teaser('amber'),'posture setup failed to start')
            if kind=='crouch_rise': e.tick(.4)
        check_posture(v)
        require(v.posture_remaining>0,'directed posture did not begin')
        contract=(v.posture_kind,v.posture_source,v.posture_target)
        duration=v.posture_remaining
        original=(v.x,v.y,v.gait_phase,v.run_phase)
        before=asdict(v);e.tick(0);require(asdict(v)==before,'paused posture advanced')
        require(Engine(e.export()).active_teaser_id is None,'posture restore resumed interaction')
        frames=0;elapsed=0.
        while v.posture_remaining>0:
            dt=min(.01,v.posture_remaining)
            e.tick(dt);elapsed+=dt;frames+=1
            require((v.x,v.y,v.gait_phase,v.run_phase)==original,'directed posture moved feet')
            check_posture(v)
        require(abs(elapsed-duration)<1e-7,'posture consumed an incorrect duration')
        results.append({'case':kind,'endpoints':list(contract),'seconds':duration,
                        'frozen_frames':frames,'pause_and_restore_checked':True,'passed':True})
    for action in ('carry','close','startle','fight'):
        e=Engine(seed=52);e.open_gate();e.invite('amber')
        v=e.visitors['amber'];v.x,v.y,v.state=300,e.floor_y,'sleeping'
        v.stay_remaining=1000;e.cats['amber'].trust=5;e.start_teaser('amber')
        before=(v.x,v.y,v.gait_phase,v.run_phase)
        if action=='carry':e.carry('amber')
        elif action=='close':e.open_gate(False)
        elif action=='startle':e.startle('amber')
        else:
            e.visitors['mist']=Visitor('mist',350,e.floor_y,state='idle')
            require(e.trigger_fight(),'directed fight setup failed')
        check_posture(v)
        require(v.posture_remaining==0 and e.active_teaser_id is None,'urgent action retained posture/session')
        require((v.x,v.y,v.gait_phase,v.run_phase)==before,'urgent posture cancellation teleported')
        results.append({'case':'sleeping_roll_interrupted','action':action,'passed':True})
    return results


def directed_escape_checks():
    results=[]
    for left in (0,-1920):
        for motion in ('fleeing','leaving'):
            e=Engine(seed=7,visit_limit=1);e.set_bounds(left,-100,1280,720)
            e.open_gate();e._visit_countdown=1000
            e.set_window_edges([('ledge',left+100,left+1200,212)])
            v=Visitor('amber',left+500,100,state='idle',window_edge_id='ledge',
                      target_x=left+500,target_y=100,stay_remaining=1000,next_action=1000)
            e.visitors['amber']=v;e.cats['amber'].trust=10
            if motion=='fleeing':require(e.startle('amber'),'escape setup failed')
            else:e.open_gate(False)
            expected_trust=9 if motion=='fleeing' else 10
            initial=(v.jump_start_x,v.jump_start_y,v.jump_duration)
            frames=0
            for _ in range(500):
                if not e.visitors:break
                e.tick(.04);frames+=1
                if not e.visitors:break
                before=asdict(v)
                require(not e.cursor_moved(v.x+72,v.y+56,1200),'repeated fear was accepted during departure')
                e.open_gate(False)
                require(asdict(v)==before,'pointer/gate-close reset committed departure')
                require(e.cats['amber'].trust==expected_trust,'repeated fear reduced trust again')
                if v.state=='jumping':
                    require((v.jump_start_x,v.jump_start_y,v.jump_duration)==initial,'escape arc was replanned')
            require(not e.visitors,'continuous fast pointer prevented departure')
            require(v.x+SPRITE_WIDTH<left or v.x>left+1280,'departure disappeared before fully offscreen')
            results.append({'case':'continuous_fast_cursor_during_high_departure','left':left,
                            'motion':motion,'frames':frames,'model_seconds':round(frames*.04,3),
                            'trust_before':10,'trust_after':e.cats['amber'].trust,'passed':True})
    return results


def directed_reward_checks():
    """Real 180s effective-clock meals, followed by safe short gratitude.

    These deterministic cases start synthetic supported visitors; the normal
    randomized runs retain natural edge arrivals. No timer is shortened.
    """
    def prepared(adopted=False, negative=False):
        e = Engine(seed=3, visit_limit=1)
        if negative:
            e.set_bounds(-1920,-100,1920,1080)
        e.open_gate(); e.invite('amber')
        v = Visitor('amber', e.bounds[0]+350, e.floor_y, state='idle',
                    next_action=10000, stay_remaining=10000, parkour_cooldown=10000)
        e.visitors['amber'] = v; e._visit_countdown = 10000
        if adopted:
            e.cats['amber'].trust = 100
            require(e.adopt('amber'), 'reward setup could not adopt')
        return e,v

    def advance(e, seconds):
        while seconds > 1e-9:
            dt = min(.05,seconds); e.tick(dt); seconds -= dt
            check_reward_clocks(e)

    def until(e, predicate, limit=60):
        for _ in range(round(limit/.05)):
            if predicate(): return
            e.tick(.05); check_reward_clocks(e)
        require(predicate(), 'reward lifecycle timed out')

    meals = []
    for adopted,negative in ((False,False),(True,True)):
        e,v = prepared(adopted,negative); e.feed()
        until(e,lambda:v.state=='eating')
        first = e._time
        require(e.cats['amber'].meal_cooldown_remaining == MEAL_COOLDOWN_SECONDS,
                'meal cooldown did not start at consumption')
        saved = e.export(); e.tick(0)
        require(e.export() == saved, 'pause advanced meal cooldown')
        until(e,lambda:v.state=='grooming'); until(e,lambda:v.state=='idle')
        trust = e.cats['amber'].trust; e.feed()
        remaining = e.cats['amber'].meal_cooldown_remaining
        advance(e,remaining-.02)
        require(e.food==1 and not e.can_eat('amber') and e.cats['amber'].trust==trust,
                'cooldown consumed or rewarded another meal early')
        until(e,lambda:v.state=='eating')
        second = e._time
        require(second-first >= MEAL_COOLDOWN_SECONDS-1e-7 and e.food==0,
                'second meal bypassed identity cooldown')
        restored = Engine(e.export())
        require(restored.cats['amber'].meal_cooldown_remaining==e.cats['amber'].meal_cooldown_remaining,
                'restore reset meal cooldown')
        until(e,lambda:v.state=='grooming')
        require(e.cats['amber'].trust==min(100,trust+22),'second completed meal reward incorrect')
        events=e.drain_events()
        require(sum(event['kind']=='eating' for event in events)==2,'duplicate meal consumption')
        meals.append({'adopted':adopted,'negative_screen':negative,'meals':2,
                      'interval_seconds':round(second-first,6),'passed':True})

    responses=[]
    for route in (False,True):
        e,v=prepared()
        if route:e._walk(v,v.x+90,v.y-100)
        before=(v.x,v.y,v.target_x,v.target_y)
        e._drop('feather',v.x,v.y,'amber'); require(e.collect_gift(e.gifts[-1].id),'collect failed')
        require((v.x,v.y,v.target_x,v.target_y)==before,'collection changed current route')
        require(v.gift_response_pending==route,'unsafe collection response was not deferred')
        until(e,lambda:v.gift_response_remaining>0)
        snapshot=asdict(v);e.tick(0);require(asdict(v)==snapshot,'pause advanced gift feedback')
        e._drop('bottle_cap',v.x,v.y,'amber');e.collect_gift(e.gifts[-1].id)
        advance(e,GIFT_RESPONSE_SECONDS)
        require(v.state=='idle' and v.gift_response_remaining==0 and not v.gift_response_pending,
                'gratitude failed to complete at three seconds')
        require(e.collected_count('feather')==e.collected_count('bottle_cap')==1,'collection count lost')
        serialized=json.dumps(e.export())
        require('gift_response' not in serialized,'transient response persisted')
        require(sum(event['kind']=='gift_response' for event in e.drain_events())==1,
                'coalesced gifts replayed feedback')
        responses.append({'pending_jump':route,'response_seconds':GIFT_RESPONSE_SECONDS,
                          'collected':2,'responses':1,'passed':True})
    return {'meals':meals,'gift_responses':responses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'output/ecology-stability-v09.json')
    parser.add_argument('--seconds', type=float, default=7200, help='Active model seconds per seed, plus 120s drain')
    parser.add_argument('--seeds', type=int, nargs='+', default=SEEDS)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be positive and finite')
    started = time.perf_counter()
    model = ROOT/'backyard_visitor/model.py'
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    geometry = ROOT/'backyard_visitor/gift_geometry.py'
    geometry_digest = hashlib.sha256(geometry.read_bytes()).hexdigest()
    interaction = ROOT/'backyard_visitor/interaction_model.py'
    interaction_digest = hashlib.sha256(interaction.read_bytes()).hexdigest()
    report = {'schema_version':4, 'started_at':datetime.now(timezone.utc).isoformat(),
              'state':'running', 'model_sha256':digest, 'gift_geometry_sha256':geometry_digest,
              'interaction_model_sha256':interaction_digest,
              'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'seeds':list(args.seeds),
              'requested_active_seconds_per_seed':args.seconds,
              'invariants':['Visitor/gift capacities and unique IDs','Finite coordinates, timers and trust',
                            'Screen containment with intentional entry/exit/drag exceptions',
                            'Window support/destination geometry and maximum 64px airborne lateral travel',
                            'Ground routes never walk vertically; walk/run phases match actual directed travel',
                            'Gait/run phase range and temporary timer bounds',
                            'Fifteen-second transient teaser, emergency cancellation and bounded swatting/rest',
                            'Posture kind/endpoints/duration contract, frozen feet and walk/run phases, emergency cleanup',
                            'Posture/teaser routes never persist, explicit rise phase remains deadline-bounded',
                            'Continuous fast cursor and repeated gate closure never restart committed emergency arcs',
                            'Identity meal cooldown remains finite/bounded and survives restart; repeat meals never consume early',
                            'Gift gratitude has bounded active duration, safe priority, coalescing and transient-only storage',
                            'Transient timers and routes eventually complete, including rise and distinct name response','Persistent export/restore and safe mouse restart',
                            'No movement during deliberate no-tick pause','Gate-closed drain completes'],
              'scope':'Pure Engine deterministic stress; four normal and four demo seeds by default. Mixed 40ms/250ms/500ms ticks; random public actions and safe synthetic window/screen geometry. Separate directed cases exercise short hops followed by horizontal finish while shrinking to another monitor, plus fifteen-second play, controlled belly reactions, three explicit posture contracts and four urgent posture cancellations. Random belly_pet only targets naturally sleeping cats; controlled scratch cases are explicitly labeled. Directed reward cases run full 180-second effective meal cooldowns for resident/ordinary visitors and collect gifts both at rest and during a committed route.',
              'does_not_cover':['Native Qt paint or timers','Real OS suspend/resume','Actual desktop or permission APIs','Real elapsed endurance or memory'],
              'formal_save_accessed':False, 'ui_started':False, 'runs':[]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    try:
        report['directed_geometry_checks'] = directed_geometry_checks()
        report['directed_interaction_checks'] = directed_interaction_checks()
        report['directed_posture_checks'] = directed_posture_checks()
        report['directed_escape_checks'] = directed_escape_checks()
        report['directed_reward_checks'] = directed_reward_checks()
    except Exception as error:
        report['state'] = 'failed'; exit_code = 1
        report['failure'] = {'error':f'Directed checks: {type(error).__name__}: {error}'}
    for index, seed in enumerate(args.seeds):
        if exit_code: break
        run = Run(seed, args.seconds, demo=index >= len(args.seeds)//2)
        try:
            report['runs'].append(run.execute())
        except Exception as error:
            report['state'] = 'failed'; exit_code = 1
            report['failure'] = {'seed':seed,'demo_mode':run.demo,'model_seconds':round(run.active_seconds,6),
                                 'error':f'{type(error).__name__}: {error}', 'recent_actions':list(run.history),
                                 'visitors':[asdict(v) for v in run.engine.visitors.values()],
                                 'bounds':list(run.engine.bounds),'window_edges':run.engine.window_edges}
            break
    if not exit_code and hashlib.sha256(model.read_bytes()).hexdigest() != digest:
        report['state'] = 'failed'; exit_code = 1
        report['failure'] = {'error':'model.py changed during the run; rerun to establish a single source version'}
    if not exit_code and hashlib.sha256(geometry.read_bytes()).hexdigest() != geometry_digest:
        report['state'] = 'failed'; exit_code = 1
        report['failure'] = {'error':'gift_geometry.py changed during the run; rerun for consistent source'}
    if not exit_code and hashlib.sha256(interaction.read_bytes()).hexdigest() != interaction_digest:
        report['state'] = 'failed'; exit_code = 1
        report['failure'] = {'error':'interaction_model.py changed during the run; rerun for consistent source'}
    if not exit_code: report['state'] = 'passed'
    report['wall_seconds'] = round(time.perf_counter()-started,4)
    report['within_10_second_runtime_target'] = report['wall_seconds'] < 10
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['total_active_model_seconds'] = round(sum(r['active_model_seconds'] for r in report['runs']),3)
    report['total_simulated_pause_seconds'] = round(sum(r['simulated_pause_seconds'] for r in report['runs']),3)
    report['maximum_observed_visitors'] = max((r['maximum_visitors'] for r in report['runs']),default=0)
    report['maximum_observed_gifts'] = max((r['maximum_gifts'] for r in report['runs']),default=0)
    for field in ('events','actions_attempted','actions_accepted','visitor_state_observations','gift_kind_observations','statistics'):
        counts = Counter()
        for row in report['runs']: counts.update(row[field])
        report['aggregate_'+field] = dict(counts)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'state':report['state'],'wall_seconds':report['wall_seconds'],
                      'completed_seeds':len(report['runs']),'active_model_hours':round(report['total_active_model_seconds']/3600,3),
                      'failure':report.get('failure',{}).get('error'),'output':str(args.output)},ensure_ascii=False))
    raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
