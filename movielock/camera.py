"""Camera presets, computed in world space so every renderer frames shots the same way.

Conventions (Blender, Z up, metres): heroes stand on z=0 at their position; yaw 0 faces -Y and
yaw grows counter-clockwise seen from above, so a hero with yaw θ faces (sin θ, -cos θ).
The "audience side" is -Y: wide shots prefer to look from there.

The camera uses a 36 mm sensor fitted to the longer side (Blender sensor_fit AUTO); ``lens`` is the focal length in mm.
"""

from __future__ import annotations

import math

SENSOR = 36.0
AUDIENCE = (0.0, -1.0)


# ------------------------------------------------------------------ tiny vector helpers
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b, s=1.0):
    return (a[0] + b[0] * s, a[1] + b[1] * s)


def _len(a):
    return math.hypot(a[0], a[1])


def _norm(a, fallback=(0.0, -1.0)):
    n = _len(a)
    return (a[0] / n, a[1] / n) if n > 1e-9 else fallback


def _perp(a):
    return (-a[1], a[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def facing_vector(yaw_deg: float):
    r = math.radians(yaw_deg)
    return (math.sin(r), -math.cos(r))


def yaw_towards(src, dst) -> float:
    d = _sub(dst, src)
    if _len(d) < 1e-9:
        return 0.0
    return round(math.degrees(math.atan2(d[0], -d[1])), 4)


def _fov(lens: float, aspect: float):
    """Horizontal and vertical field of view (radians); aspect = width / height.

    Like Blender's sensor_fit AUTO: the 36 mm sensor spans the longer side of the frame.
    """
    if aspect >= 1:
        h = 2 * math.atan(SENSOR / 2 / lens)
        v = 2 * math.atan(SENSOR / 2 / aspect / lens)
    else:
        v = 2 * math.atan(SENSOR / 2 / lens)
        h = 2 * math.atan(SENSOR / 2 * aspect / lens)
    return h, v


def _distance(lens, aspect, width_needed, height_needed):
    h, v = _fov(lens, aspect)
    return max(width_needed / 2 / math.tan(h / 2), height_needed / 2 / math.tan(v / 2))


def _front(actor):
    """Direction from an actor toward a pleasing 3/4 front camera position."""
    f = facing_vector(actor["yaw"])
    return _norm(_add(f, AUDIENCE, 0.8), fallback=f)


def _side_toward_audience(axis):
    side = _perp(_norm(axis))
    if _dot(side, AUDIENCE) < 0 or (abs(_dot(side, AUDIENCE)) < 1e-6 and side[0] < 0):
        side = (-side[0], -side[1])
    return side


def _rot(v, deg):
    r = math.radians(deg)
    c, s_ = math.cos(r), math.sin(r)
    return (v[0] * c - v[1] * s_, v[0] * s_ + v[1] * c)


def _seg_dist(p, a, b):
    ab = _sub(b, a)
    L2 = _dot(ab, ab)
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / L2))
    return _len(_sub(p, _add(a, ab, t)))


def _clear(center, direction, dist, others, sweep=0.0):
    """Rotate ``direction`` (up to +-75 deg) until the camera (and its sightline, and for an orbit
    the whole arc of +-sweep/2 degrees) keeps clear of the other actors."""
    if not others:
        return direction
    for step in (0, 10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 75, -75):
        d = _rot(direction, step)
        angles = [0.0] if not sweep else [-sweep / 2 + sweep * i / 8 for i in range(9)]
        ok = True
        for ang in angles:
            cam = _add(center, _rot(d, ang), dist)
            for o in others:
                if _len(_sub(cam, o)) < 0.9 or _seg_dist(o, cam, _add(center, _sub(cam, center), 0.25)) < 0.45:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return d
    return direction


def _pt(xy, z):
    return [round(xy[0], 4), round(xy[1], 4), round(z, 4)]


def _key(t, pos_xy, pos_z, tgt_xy, tgt_z, lens):
    return {"t": round(t, 4), "pos": _pt(pos_xy, pos_z), "target": _pt(tgt_xy, tgt_z), "lens": lens}


def _base(a):
    return (a["position"][0], a["position"][1])


def _z(a):
    return a["position"][2] if len(a["position"]) > 2 else 0.0


def compute_camera(kind: str, actors: dict, on: str | None, toward: str | None, duration: float,
                   aspect: float) -> dict:
    """Return {type, keys:[{t,pos,target,lens}], dof} for a shot.

    ``actors`` maps cast name -> {"position": [x,y,z], "yaw": deg, "height": m}.
    """
    names = list(actors)
    if not names:
        return {"type": kind, "keys": [_key(0, (0, -6), 1.6, (0, 0), 1.0, 30)], "dof": None,
                "subject": None, "partner": None}
    subj = actors[on] if on in actors else actors[names[0]]
    subj_name = on if on in actors else names[0]
    partner_name = toward if toward in actors and toward != subj_name else None
    if partner_name is None and len(names) > 1:
        # whoever the subject looks at, else the nearest other cast member
        others = [n for n in names if n != subj_name]
        f = facing_vector(subj["yaw"])
        partner_name = max(others, key=lambda n: _dot(_norm(_sub(_base(actors[n]), _base(subj))), f)
                           - 0.01 * _len(_sub(_base(actors[n]), _base(subj))))
    partner = actors.get(partner_name) if partner_name else None
    sp, sh, sz = _base(subj), subj["height"], _z(subj)
    dof = None
    keys: list[dict] = []

    if kind == "over_shoulder" and partner is None:
        kind_eff = "medium"
    elif kind == "two_shot" and partner is None:
        kind_eff = "medium"
    else:
        kind_eff = kind

    if kind_eff == "wide":
        lens = 24
        pts = [_base(a) for a in actors.values()]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        c = (cx, cy)
        if len(pts) >= 2:
            far = max(((a, b) for a in pts for b in pts), key=lambda ab: _len(_sub(ab[0], ab[1])))
            axis = _sub(far[1], far[0])
            side = _side_toward_audience(axis) if _len(axis) > 1e-6 else _front(subj)
        else:
            side = _front(subj)
        right = _perp(side)
        half = max(abs(_dot(_sub(p, c), right)) for p in pts) + 0.7
        hmax = max(a["height"] for a in actors.values())
        dist = max(3.0, _distance(lens, aspect, 2 * half, hmax * 1.3))
        cam = _add(c, side, dist)
        keys.append(_key(0, cam, sz + hmax * 0.62, c, sz + hmax * 0.5, lens))

    elif kind_eff in ("medium", "closeup", "low_angle", "orbit", "dolly_in"):
        d = _front(subj)
        if kind_eff == "orbit":  # swing around the front, favouring the audience side
            d = _norm(_add(facing_vector(subj["yaw"]), AUDIENCE, 2.0), fallback=d)
        others = [_base(a) for n, a in actors.items() if n != subj_name]
        if kind_eff == "medium":
            lens, tz, hn, wn, cz = 40, 0.8, 0.62, 0.9, 0.82
        elif kind_eff == "closeup":
            lens, tz, hn, wn, cz = 85, 0.93, 0.3, 0.42, 0.93
        elif kind_eff == "low_angle":
            lens, tz, hn, wn, cz = 28, 0.75, 1.15, 1.0, None
        else:  # orbit / dolly_in start
            lens, tz, hn, wn, cz = 35, 0.72, 0.95, 1.2, 0.8
        dist = _distance(lens, aspect, wn, hn * sh)
        target_z = sz + tz * sh
        cam_z = sz + 0.35 if cz is None else sz + cz * sh
        d = _clear(sp, d, dist, others, sweep=50.0 if kind_eff == "orbit" else 0.0)
        if kind_eff == "orbit":
            base_ang = math.atan2(d[1], d[0])
            span = math.radians(50)
            steps = 8
            for i in range(steps + 1):
                u = i / steps
                ang = base_ang - span / 2 + span * u
                pos = _add(sp, (math.cos(ang), math.sin(ang)), dist)
                keys.append(_key(duration * u, pos, cam_z, sp, target_z, lens))
        elif kind_eff == "dolly_in":
            end_dist = _distance(lens, aspect, 0.5, 0.42 * sh)
            keys.append(_key(0, _add(sp, d, dist), cam_z, sp, target_z, lens))
            keys.append(_key(duration, _add(sp, d, end_dist), sz + 0.9 * sh, sp, sz + 0.9 * sh, lens))
        else:
            keys.append(_key(0, _add(sp, d, dist), cam_z, sp, target_z, lens))
        if kind_eff in ("closeup",):
            dof = {"focus": subj_name, "fstop": 2.0}
        elif kind_eff == "dolly_in":
            dof = {"focus": subj_name, "fstop": 2.8}

    elif kind_eff == "over_shoulder":
        lp, lh, lz = _base(partner), partner["height"], _z(partner)
        back = _norm(_sub(lp, sp))
        side = _side_toward_audience(back)
        cam = _add(_add(lp, back, 0.95), side, 0.45)
        lens = 50
        tgt = _add(sp, side, 0.15)
        keys.append(_key(0, cam, lz + 0.93 * lh, tgt, sz + 0.86 * sh, lens))
        dof = {"focus": subj_name, "fstop": 2.8}

    elif kind_eff == "two_shot":
        pp, ph = _base(partner), partner["height"]
        mid = ((sp[0] + pp[0]) / 2, (sp[1] + pp[1]) / 2)
        side = _side_toward_audience(_sub(pp, sp))
        lens = 35
        hmax = max(sh, ph)
        dist = max(2.0, _distance(lens, aspect, _len(_sub(pp, sp)) + 1.3, hmax * 0.8))
        keys.append(_key(0, _add(mid, side, dist), sz + 0.8 * hmax, mid, sz + 0.74 * hmax, lens))
    else:
        raise ValueError(f"unknown camera {kind}")

    return {"type": kind, "effective": kind_eff, "keys": keys, "dof": dof,
            "subject": subj_name, "partner": partner_name}
