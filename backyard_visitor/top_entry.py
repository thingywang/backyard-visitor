"""Short real upper-edge arrivals; native supports and all clocks remain transient."""
SPRITE_WIDTH = 144.0
SPRITE_HEIGHT = 112.0
TAIL_EXTENT = 56.0
TOP_ENTRY_CHANCE = .35
TOP_STATES = frozenset({'top_entering', 'top_leaving'})


def hidden_y(engine):
    return engine.screen_top-SPRITE_HEIGHT-TAIL_EXTENT-2.0


def _slots(engine, visitor):
    slots=[]
    for identity,(left,right,top) in engine.window_edges.items():
        if identity not in engine.low_headroom_edges:continue
        intervals=[(left,right-SPRITE_WIDTH)]
        family=identity.rsplit(':top:',1)[0]
        for other in engine.visitors.values():
            if other is visitor:continue
            ref=other.window_edge_id or other.window_target_id
            if ref is None or ref.rsplit(':top:',1)[0]!=family:continue
            low,high=other.target_x-SPRITE_WIDTH-24,other.target_x+SPRITE_WIDTH+24
            free=[]
            for a,b in intervals:
                if high<a or low>b:free.append((a,b))
                else:
                    if a<low:free.append((a,min(b,low)))
                    if high<b:free.append((max(a,high),b))
            intervals=free
        slots.extend((identity,a,b,top) for a,b in intervals if a<=b)
    return slots


def maybe_enter(engine, visitor):
    slots=_slots(engine,visitor)
    if not slots or engine.rng.random()>=TOP_ENTRY_CHANCE:return False
    identity,left,right,top=engine.rng.choice(slots)
    x=engine.rng.uniform(left,right)
    # This assignment happens while creating the visitor, before any UI sync.
    # The entire 168px rear canvas starts outside the selected full display.
    engine._state(visitor,'top_entering')
    visitor.x=visitor.target_x=visitor.jump_start_x=x
    visitor.y=visitor.jump_start_y=hidden_y(engine)
    visitor.target_y=top-SPRITE_HEIGHT
    visitor.window_target_id=visitor.route_edge_id=identity
    visitor.route_final_x,visitor.route_final_y=x,visitor.target_y
    visitor.route_stage='top_entry'
    visitor.jump_duration=max(.7,(visitor.target_y-visitor.y)/95.0)
    visitor.jump_elapsed=0.0
    visitor.rear_blend=1.0
    return True


def abort(engine, visitor, *, fleeing=False):
    if visitor.state=='top_leaving':return
    engine._clear_route(visitor);engine._clear_window(visitor)
    # Reuse emergency cleanup before selecting the short upward retreat.
    engine._state(visitor,'fleeing' if fleeing else 'leaving')
    engine._state(visitor,'top_leaving')
    visitor.jump_start_x=visitor.target_x=visitor.x
    visitor.jump_start_y=visitor.y
    visitor.target_y=hidden_y(engine)
    visitor.jump_duration=max(.25,1.5*abs(visitor.y-visitor.target_y)/(210 if fleeing else 130))
    visitor.jump_elapsed=0.0
    visitor.route_motion='fleeing' if fleeing else 'leaving'
    visitor.rear_blend=1.0


def step(engine, visitor, dt):
    entering=visitor.state=='top_entering'
    if not entering and visitor.target_y > hidden_y(engine):
        # Available-area rebasing can precede the fresh full-display top.
        # Extend an upward retreat from its current point, never remove a
        # still-visible tail or jump back to the previous trajectory origin.
        visitor.jump_start_y=visitor.y
        visitor.target_y=hidden_y(engine)
        speed=210 if visitor.route_motion=='fleeing' else 130
        visitor.jump_duration=max(.25,1.5*abs(visitor.y-visitor.target_y)/speed)
        visitor.jump_elapsed=0.0
    if entering:
        edge=engine.window_edges.get(visitor.window_target_id)
        if (edge is None or visitor.window_target_id not in engine.low_headroom_edges
                or not edge[0]<=visitor.target_x<=edge[1]-SPRITE_WIDTH
                or edge[2]-SPRITE_HEIGHT!=visitor.target_y):
            abort(engine,visitor);return
    visitor.jump_elapsed=min(visitor.jump_duration,visitor.jump_elapsed+dt)
    p=min(1.0,visitor.jump_elapsed/visitor.jump_duration)
    eased=p*p*(3-2*p)
    visitor.y=visitor.jump_start_y+(visitor.target_y-visitor.jump_start_y)*eased
    if p<1:return
    visitor.y=visitor.target_y
    if not entering:
        del engine.visitors[visitor.cat_id]
        engine._emit('departure',visitor.cat_id,f'{engine.cat_reference(visitor.cat_id)}从上方轻轻退回去了。')
        return
    visitor.window_edge_id=visitor.window_target_id
    visitor.window_target_id=None
    engine._clear_route(visitor)
    engine._begin_window_rear(visitor)
    visitor.landing_remaining=.24
    if visitor.carrying_gift:
        visitor.demo_gift_pending=visitor.carrying_gift
        visitor.carrying_gift=None
        visitor.gift_spot=None
