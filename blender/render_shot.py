"""Render one shot of a Movielock plan with Blender (4.2 or newer).

    blender -b --factory-startup -P blender/render_shot.py -- plan.json <shot_id> <out_dir>

Writes <out_dir>/0001.png ... one PNG per frame. Everything the shot needs (positions,
facing, animation strips, mouth cues, camera keys, quality) comes from plan.json, which
the Movielock planner writes. Models and maps are glTF files you exported yourself.

Missing hero models are replaced by a plain placeholder mannequin, so a draft still renders.
Progress lines: "MOVIELOCK_PROGRESS <done>/<total>"; warnings: "MOVIELOCK_WARNING <text>".
"""

import json
import math
import os
import sys

import bpy
from mathutils import Euler, Quaternion, Vector

MOUTH_OPEN = {"X": 0.0, "A": 0.0, "B": 0.3, "C": 0.6, "D": 1.0, "E": 0.7, "F": 0.45, "G": 0.25, "H": 0.5}
VISEMES = ("A", "B", "C", "D", "E", "F", "G", "H", "X")


def warn(msg):
    print(f"MOVIELOCK_WARNING {msg}", flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) != 3:
        print("usage: blender -b -P render_shot.py -- plan.json <shot_id> <out_dir>", file=sys.stderr)
        sys.exit(2)
    return argv


# --------------------------------------------------------------------------- helpers

def hex_to_rgba(value, alpha=1.0):
    v = (value or "#888888").lstrip("#")
    try:
        r, g, b = (int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        r = g = b = 0.5
    # sRGB -> linear
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return (*lin, alpha)


class Timeline:
    def __init__(self, fps):
        self.fps = fps

    def frame(self, t):
        """Shot-local seconds -> scene frame (the shot starts at frame 1)."""
        return 1.0 + t * self.fps


def new_objects(before):
    return [o for o in bpy.data.objects if o.name not in before]


def import_gltf(path):
    before_objs = {o.name for o in bpy.data.objects}
    before_actions = {a.name for a in bpy.data.actions}
    bpy.ops.import_scene.gltf(filepath=path)
    objs = new_objects(before_objs)
    actions = [a for a in bpy.data.actions if a.name not in before_actions]
    return objs, actions


def make_root(name, objs):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    for o in objs:
        if o.parent is None:
            o.parent = root
    return root


def set_interpolation(kind):
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = kind
    except (AttributeError, TypeError):
        pass


def simple_material(name, rgba, roughness=0.6):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = rgba
    return mat


# --------------------------------------------------------------------------- scene setup

def setup_render(scene, plan, shot, out_dir):
    q = plan["quality"]
    scene.render.resolution_x = plan["width"]
    scene.render.resolution_y = plan["height"]
    scene.render.resolution_percentage = 100
    scene.render.fps = plan["fps"]
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = shot["frames"]
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = os.path.join(out_dir, "####")
    scene.render.use_file_extension = True
    scene.render.film_transparent = False

    engines = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    if q.get("engine") == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = int(q.get("samples", 128))
        scene.cycles.use_denoising = bool(q.get("denoise", True))
        scene.render.use_persistent_data = True
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for dev_type in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
                try:
                    prefs.compute_device_type = dev_type
                except TypeError:
                    continue
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == dev_type]
                if gpus:
                    for d in prefs.devices:
                        d.use = True
                    scene.cycles.device = "GPU"
                    break
        except Exception as exc:  # noqa: BLE001
            warn(f"GPU setup failed, using CPU: {exc}")
    else:
        for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            if name in engines:
                scene.render.engine = name
                break
        if hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = int(q.get("samples", 16))
    if q.get("motion_blur"):
        scene.render.use_motion_blur = True
        if hasattr(scene, "eevee") and hasattr(scene.eevee, "use_motion_blur"):
            scene.eevee.use_motion_blur = True
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        try:
            scene.view_settings.view_transform = "Filmic"
        except TypeError:
            pass


def setup_world(scene, map_plan):
    world = bpy.data.worlds.new("MovielockWorld")
    scene.world = world
    try:
        world.use_nodes = True
    except AttributeError:
        pass
    nt = world.node_tree
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None) or nt.nodes.new("ShaderNodeBackground")
    out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None) or nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    sky = (map_plan or {}).get("sky_color", "#20242c")
    bg.inputs["Color"].default_value = hex_to_rgba(sky)
    bg.inputs["Strength"].default_value = 1.0
    hdri = (map_plan or {}).get("hdri")
    if hdri and os.path.isfile(hdri):
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        try:
            env.image = bpy.data.images.load(hdri, check_existing=True)
            nt.links.new(env.outputs["Color"], bg.inputs["Color"])
            bg.inputs["Strength"].default_value = float(map_plan.get("hdri_strength", 1.0))
        except RuntimeError as exc:
            warn(f"could not load HDRI {hdri}: {exc}")
    elif hdri:
        warn(f"HDRI not found: {hdri} (using sky colour)")


def setup_map(map_plan):
    if not map_plan:
        map_plan = {"ground": True, "sky_color": "#20242c"}
    model = map_plan.get("model")
    if model and os.path.isfile(model):
        objs, _ = import_gltf(model)
        root = make_root("MAP", objs)
        s = float(map_plan.get("scale", 1.0))
        root.scale = (s, s, s)
        root.location = Vector(map_plan.get("offset") or (0, 0, 0))
        # map animations (flags, fans ...) would otherwise fight with nothing; leave them as imported
        return
    if model:
        warn(f"map model not found: {model} (using a plain floor)")
    if map_plan.get("ground", True) or model:
        bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, 0))
        floor = bpy.context.active_object
        floor.name = "Floor"
        floor.data.materials.append(simple_material("FloorMat", hex_to_rgba("#3a3d44"), 0.8))


# --------------------------------------------------------------------------- heroes

def placeholder_hero(name, hero, height):
    """A plain capsule mannequin (body, head, a 'nose' showing the facing, a mouth for lip sync)."""
    color = hex_to_rgba(hero.get("color"))
    mat = simple_material(f"{name}_mat", color, 0.5)
    dark = simple_material(f"{name}_mouth", (0.02, 0.02, 0.02, 1.0), 0.9)
    head_r = height * 0.11
    body_h = height - 2 * head_r + head_r * 0.15  # neck overlaps the head slightly
    bpy.ops.mesh.primitive_cylinder_add(radius=height * 0.13, depth=body_h, location=(0, 0, body_h / 2))
    body = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(radius=head_r, location=(0, 0, height - head_r))
    head = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=head_r * 0.35, location=(0, -head_r * 1.0, height - head_r * 0.85))
    nose = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -head_r * 0.93, height - head_r * 1.35))
    mouth = bpy.context.active_object
    mouth.scale = (head_r * 0.7, head_r * 0.1, head_r * 0.08)
    for o in (body, head, nose):
        o.data.materials.append(mat)
    mouth.data.materials.append(dark)
    mouth.name = f"{name}_placeholder_mouth"
    return [body, head, nose, mouth], mouth


def find_action(name, preferred):
    """Find an imported action by exact name, then case-insensitive / partial match."""
    pools = [preferred, list(bpy.data.actions)]
    low = name.lower()
    for pool in pools:
        for a in pool:
            if a.name == name:
                return a
        for a in pool:
            if a.name.lower() == low or a.name.lower().split("|")[-1] == low:
                return a
        for a in pool:  # glTF importer names actions "<animation>_<object>"
            if a.name.lower().startswith(low + "_") or a.name.lower().startswith(low + "."):
                return a
        for a in pool:
            if low in a.name.lower():
                return a
    return None


def action_length(action):
    start, end = action.frame_range
    return max(1.0, end - start)


def assign_slot(strip, action):
    """Blender 4.4+ layered actions: make sure the strip uses one of the action's slots."""
    if hasattr(strip, "action_slot") and getattr(strip, "action_slot", None) is None:
        slots = getattr(action, "slots", None)
        if slots:
            try:
                strip.action_slot = slots[0]
            except (AttributeError, TypeError, RuntimeError):
                pass


def apply_animations(arm, strips, actions, tl, shot_frames):
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = None
    for track in list(ad.nla_tracks):
        ad.nla_tracks.remove(track)
    tracks = {}
    for s in sorted(strips, key=lambda x: (x["layer"], x["start"])):
        action = find_action(s["action"], actions)
        if action is None:
            warn(f"{arm.name}: action '{s['action']}' (for '{s['name']}') not found; "
                 f"available: {', '.join(a.name for a in actions) or 'none'}")
            continue
        start = tl.frame(s["start"])
        end = min(tl.frame(s["end"]), shot_frames + 1)
        if end - start < 1:
            continue
        track = tracks.get(s["layer"])
        strip = None
        for attempt in range(3):
            if track is None:
                track = ad.nla_tracks.new()
                track.name = f"layer{s['layer']}"
                tracks[s["layer"]] = track
            try:
                strip = track.strips.new(f"{s['name']}", int(round(start)), action)
                break
            except RuntimeError:  # overlaps another strip on this track
                track = None
        if strip is None:
            continue
        assign_slot(strip, action)
        speed = float(s.get("speed", 1.0)) or 1.0
        strip.scale = 1.0 / speed
        length = action_length(action) / speed
        if s["loop"]:
            strip.repeat = max(0.05, (end - start) / length)
        else:
            strip.repeat = 1.0
        strip.blend_type = "REPLACE"
        if s["layer"] == 0:
            strip.extrapolation = "HOLD_FORWARD"
        else:
            strip.extrapolation = "NOTHING"
            span = strip.frame_end - strip.frame_start
            blend = max(0.0, min(4.0, span / 4))
            strip.blend_in = blend
            strip.blend_out = blend


def viseme_targets(meshes, visemes):
    """Map each Rhubarb shape to [(key_block, weight)] found on the hero's meshes."""
    found = {v: [] for v in VISEMES}
    for shape, spec in (visemes or {}).items():
        if isinstance(spec, str):
            wanted = {spec: 1.0}
        elif isinstance(spec, list):
            wanted = {n: 1.0 for n in spec}
        elif isinstance(spec, dict):
            wanted = {str(k): float(v) for k, v in spec.items()}
        else:
            continue
        for mesh in meshes:
            keys = mesh.data.shape_keys
            if not keys:
                continue
            for kname, w in wanted.items():
                kb = keys.key_blocks.get(kname)
                if kb is not None:
                    found[shape].append((kb, w))
    return found


def key_mouth_shapes(targets, mouth, tl):
    all_blocks = {id(kb): kb for pairs in targets.values() for kb, _ in pairs}
    if not all_blocks:
        return False
    set_interpolation("LINEAR")

    def key_state(shape, frame):
        weights = {k: 0.0 for k in all_blocks}
        for kb, w in targets.get(shape, []):
            weights[id(kb)] = max(weights[id(kb)], w)
        for k, kb in all_blocks.items():
            kb.value = weights[k]
            kb.keyframe_insert("value", frame=frame)

    key_state("X", 1)
    for cue in mouth:
        f0, f1 = tl.frame(cue["start"]), tl.frame(cue["end"])
        if f1 - f0 <= 2:
            key_state(cue["shape"], (f0 + f1) / 2)
        else:
            key_state(cue["shape"], f0 + 1)
            key_state(cue["shape"], f1 - 1)
    if mouth:
        key_state("X", tl.frame(mouth[-1]["end"]) + 1)
    return True


def key_jaw(arm, bone_name, mouth, tl, open_deg, axis):
    pb = arm.pose.bones.get(bone_name) if arm else None
    if pb is None:
        return False
    set_interpolation("LINEAR")
    pb.rotation_mode = "QUATERNION"
    rest = pb.rotation_quaternion.copy()
    ax = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(axis, (1, 0, 0))

    def key(open_amount, frame):
        pb.rotation_quaternion = rest @ Quaternion(ax, math.radians(open_deg * open_amount))
        pb.keyframe_insert("rotation_quaternion", frame=frame, group="movielock_jaw")

    key(0.0, 1)
    for cue in mouth:
        f0, f1 = tl.frame(cue["start"]), tl.frame(cue["end"])
        amt = MOUTH_OPEN.get(cue["shape"], 0.3)
        if f1 - f0 <= 2:
            key(amt, (f0 + f1) / 2)
        else:
            key(amt, f0 + 1)
            key(amt, f1 - 1)
    if mouth:
        key(0.0, tl.frame(mouth[-1]["end"]) + 1)
    return True


def key_placeholder_mouth(obj, mouth, tl):
    set_interpolation("LINEAR")
    base = obj.scale.copy()

    def key(amount, frame):
        obj.scale = (base.x, base.y, base.z * (1.0 + 5.0 * amount))
        obj.keyframe_insert("scale", frame=frame)

    key(0.0, 1)
    for cue in mouth:
        f0, f1 = tl.frame(cue["start"]), tl.frame(cue["end"])
        amt = MOUTH_OPEN.get(cue["shape"], 0.3)
        key(amt, f0 + min(1, (f1 - f0) / 2))
        key(amt, max(f0 + min(1, (f1 - f0) / 2), f1 - 1))
    if mouth:
        key(0.0, tl.frame(mouth[-1]["end"]) + 1)


def setup_hero(cast, shot, tl):
    hero = cast["hero"]
    name = cast["name"]
    actor = shot["actors"].get(name, {"anims": [], "mouth": []})
    model = hero.get("model")
    placeholder_mouth = None
    actions = []
    if model and os.path.isfile(model):
        objs, actions = import_gltf(model)
    else:
        warn(f"hero model for '{name}' not found ({model}); using a placeholder mannequin")
        objs, placeholder_mouth = placeholder_hero(name, hero, float(hero.get("height", 1.8)))
    root = make_root(f"CAST_{name}", objs)
    root.location = Vector(cast["position"])
    root.rotation_euler = Euler((0.0, 0.0, math.radians(cast["yaw"] + float(hero.get("yaw_offset", 0.0)))))
    if placeholder_mouth is None:
        s = float(hero.get("scale", 1.0))
        root.scale = (s, s, s)

    arms = [o for o in objs if o.type == "ARMATURE"]
    meshes = [o for o in objs if o.type == "MESH"]
    arm = arms[0] if arms else None
    if arm is not None and actor["anims"]:
        apply_animations(arm, actor["anims"], actions, tl, shot["frames"])
    elif actor["anims"] and placeholder_mouth is None:
        warn(f"'{name}' has no armature; animations skipped")

    mouth = actor.get("mouth") or []
    if placeholder_mouth is not None:
        key_placeholder_mouth(placeholder_mouth, mouth, tl)
    elif mouth:
        if not key_mouth_shapes(viseme_targets(meshes, hero.get("visemes")), mouth, tl):
            if not key_jaw(arm, hero.get("jaw_bone"), mouth, tl, float(hero.get("jaw_open", 18.0)),
                           hero.get("jaw_axis", "X")):
                warn(f"'{name}': no viseme shape keys and no jaw bone '{hero.get('jaw_bone')}' - no lip sync")
    return root


# --------------------------------------------------------------------------- camera & lights

def setup_camera(scene, shot, plan, tl):
    cam_plan = shot["camera"]
    cam_data = bpy.data.cameras.new("ShotCam")
    cam_data.sensor_fit = "AUTO"
    cam_data.sensor_width = 36.0
    cam_data.clip_start = 0.05
    cam_data.clip_end = 2000.0
    cam = bpy.data.objects.new("ShotCam", cam_data)
    scene.collection.objects.link(cam)
    target = bpy.data.objects.new("CamTarget", None)
    scene.collection.objects.link(target)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    scene.camera = cam

    set_interpolation("BEZIER")
    keys = cam_plan["keys"]
    for k in keys:
        f = tl.frame(k["t"])
        cam.location = Vector(k["pos"])
        target.location = Vector(k["target"])
        cam_data.lens = float(k["lens"])
        if len(keys) > 1:
            cam.keyframe_insert("location", frame=f)
            target.keyframe_insert("location", frame=f)
            cam_data.keyframe_insert("lens", frame=f)

    dof = cam_plan.get("dof")
    if dof:
        subject = next((c for c in plan["cast"] if c["name"] == dof.get("focus")), None)
        if subject:
            focus = bpy.data.objects.new("Focus", None)
            scene.collection.objects.link(focus)
            pos = subject["position"]
            focus.location = (pos[0], pos[1], (pos[2] if len(pos) > 2 else 0) + 0.92 * subject["height"])
            cam_data.dof.use_dof = True
            cam_data.dof.focus_object = focus
            cam_data.dof.aperture_fstop = float(dof.get("fstop", 2.8))
    return cam, target


def setup_lights(scene, shot, plan):
    """Classic 3-point lighting around the shot's subject, placed relative to the camera."""
    cam_plan = shot["camera"]
    k0 = cam_plan["keys"][0]
    subject = next((c for c in plan["cast"] if c["name"] == cam_plan.get("subject")), None)
    if subject:
        p = subject["position"]
        center = Vector((p[0], p[1], (p[2] if len(p) > 2 else 0) + 0.75 * subject["height"]))
    else:
        center = Vector(k0["target"])
    cam_pos = Vector(k0["pos"])
    view = center - cam_pos
    view.z = 0
    if view.length < 1e-4:
        view = Vector((0, 1, 0))
    view.normalize()
    side = Vector((-view.y, view.x, 0))
    aim = bpy.data.objects.new("LightAim", None)
    scene.collection.objects.link(aim)
    aim.location = center
    hdri = (plan.get("map") or {}).get("hdri_exists")
    scale = 0.6 if hdri else 1.0

    def area(name, offset, energy, size, color=(1, 1, 1)):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy * scale
        data.size = size
        data.color = color
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.location = center + offset
        con = obj.constraints.new("TRACK_TO")
        con.target = aim
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
        return obj

    dist = 3.0
    area("Key", (-view + side * 0.9).normalized() * dist + Vector((0, 0, 1.6)), 550, 1.5, (1.0, 0.96, 0.9))
    area("Fill", (-view - side * 1.1).normalized() * dist + Vector((0, 0, 0.6)), 160, 3.0, (0.85, 0.9, 1.0))
    area("Rim", (view * 1.0 + side * 0.3).normalized() * dist + Vector((0, 0, 2.0)), 450, 1.0, (0.9, 0.95, 1.0))


# --------------------------------------------------------------------------- main

def main():
    plan_path, shot_id, out_dir = parse_args()
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    shot = next((s for s in plan["shots"] if s["id"] == shot_id), None)
    if shot is None:
        print(f"shot '{shot_id}' not in plan", file=sys.stderr)
        sys.exit(2)
    os.makedirs(out_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    tl = Timeline(plan["fps"])
    setup_render(scene, plan, shot, out_dir)
    setup_world(scene, plan.get("map"))
    setup_map(plan.get("map"))
    for cast in plan["cast"]:
        setup_hero(cast, shot, tl)
    setup_camera(scene, shot, plan, tl)
    setup_lights(scene, shot, plan)
    scene.frame_set(1)

    total = shot["frames"]

    def on_write(scn, *_args):
        print(f"MOVIELOCK_PROGRESS {scn.frame_current}/{total}", flush=True)

    bpy.app.handlers.render_write.append(on_write)
    if os.environ.get("MOVIELOCK_SAVE_BLEND"):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, f"{shot_id}.blend"))
    bpy.ops.render.render(animation=True)
    print("MOVIELOCK_DONE", flush=True)


if __name__ == "__main__":
    main()
